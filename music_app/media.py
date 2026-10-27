from __future__ import annotations

import platform
import math
import subprocess
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_ART_BYTES = 4 * 1024 * 1024
ART_SIZE = 73


def _artwork_png(raw: bytes) -> bytes | None:
    """Decode and size artwork on the media worker, before it reaches Tk."""
    if not raw or len(raw) > MAX_ART_BYTES:
        return None
    try:
        with Image.open(BytesIO(raw)) as source:
            if source.width * source.height > 16_000_000:
                return None
            source.load()
            image = ImageOps.contain(ImageOps.exif_transpose(source).convert("RGBA"), (ART_SIZE, ART_SIZE))
        tile = Image.new("RGBA", (ART_SIZE, ART_SIZE), "#030408")
        tile.alpha_composite(image, ((ART_SIZE - image.width) // 2, (ART_SIZE - image.height) // 2))
        output = BytesIO()
        tile.save(output, format="PNG")
        return output.getvalue()
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return None


def _local_artwork(uri: str) -> bytes | None:
    """Read local media artwork only; never request remote image URLs."""
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        return None
    path = Path(url2pathname(unquote(parsed.path)))
    try:
        with path.open("rb") as image:
            return _artwork_png(image.read(MAX_ART_BYTES + 1))
    except (OSError, ValueError):
        return None


def _valid_timeline(position: float | None, duration: float | None) -> tuple[float | None, float | None]:
    if (position is None or duration is None or not math.isfinite(position)
            or not math.isfinite(duration) or not (0 <= position and 0 < duration)):
        return None, None
    return min(position, duration), duration


@dataclass(frozen=True)
class MediaState:
    available: bool = False
    title: str = "Nothing playing"
    artist: str = ""
    source: str = ""
    playing: bool = False
    detail: str = ""
    position_s: float | None = None
    duration_s: float | None = None
    sampled_at: float | None = None
    playback_rate: float = 1.0
    artwork_png: bytes | None = None


class MediaAdapter(Protocol):
    async def snapshot(self) -> MediaState: ...
    async def command(self, action: str) -> bool: ...
    async def close(self) -> None: ...


class WindowsMedia:
    def __init__(self) -> None:
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager,
        )
        from winrt.windows.storage.streams import DataReader

        self._manager_type = GlobalSystemMediaTransportControlsSessionManager
        self._reader_type = DataReader
        self._manager = None
        self._art_key: tuple[str, str, str, str] | None = None
        self._art_png: bytes | None = None

    async def _session(self):
        if self._manager is None:
            self._manager = await self._manager_type.request_async()
        return self._manager.get_current_session()

    async def snapshot(self) -> MediaState:
        session = await self._session()
        if session is None:
            return MediaState(detail="Open a player that supports Windows media controls")
        props = await session.try_get_media_properties_async()
        info = session.get_playback_info()
        # PyWinRT's IntEnum string is its numeric value ("4"), not "PLAYING".
        playing = bool(info and getattr(info.playback_status, "name", str(info.playback_status)).upper() == "PLAYING")
        source_id = str(session.source_app_user_model_id or "")
        title = (props.title or "Untitled") if props else "Untitled"
        artist = (props.artist or "") if props else ""
        art_key = (source_id, title, artist, str(getattr(props, "album_title", "") or ""))
        if art_key != self._art_key or (self._art_png is None and props and props.thumbnail):
            self._art_key = art_key
            self._art_png = await self._thumbnail(props.thumbnail if props else None)

        position = duration = None
        sampled_at = time.monotonic()
        try:
            timeline = session.get_timeline_properties()
            if timeline:
                start = timeline.start_time.total_seconds()
                end = timeline.end_time.total_seconds()
                position = timeline.position.total_seconds() - start
                duration = end - start
                if playing and timeline.last_updated_time:
                    updated = timeline.last_updated_time
                    if updated.tzinfo is None:
                        updated = updated.replace(tzinfo=timezone.utc)
                    age = (datetime.now(timezone.utc) - updated).total_seconds()
                    if 0 < age < 30:
                        position += age
                position, duration = _valid_timeline(position, duration)
        except (AttributeError, OSError, TypeError, ValueError):
            position = duration = None
        return MediaState(
            available=True,
            title=title,
            artist=artist,
            source=source_id.rsplit("!", 1)[-1].split(".")[0] if source_id else "",
            playing=playing,
            position_s=position,
            duration_s=duration,
            sampled_at=sampled_at,
            artwork_png=self._art_png,
        )

    async def _thumbnail(self, reference) -> bytes | None:
        if reference is None:
            return None
        try:
            stream = await reference.open_read_async()
            try:
                size = int(stream.size)
                if not 0 < size <= MAX_ART_BYTES:
                    return None
                reader = self._reader_type(stream.get_input_stream_at(0))
                try:
                    count = await reader.load_async(size)
                    return _artwork_png(bytes(reader.read_buffer(count))) if count else None
                finally:
                    reader.close()
            finally:
                stream.close()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return None

    async def command(self, action: str) -> bool:
        session = await self._session()
        if session is None:
            return False
        method = {
            "toggle": session.try_toggle_play_pause_async,
            "previous": session.try_skip_previous_async,
            "next": session.try_skip_next_async,
        }.get(action)
        return bool(await method()) if method else False

    async def close(self) -> None:
        self._manager = None


class LinuxMprisMedia:
    def __init__(self) -> None:
        from dbus_next.aio import MessageBus
        from dbus_next import Message

        self._bus_type = MessageBus
        self._message_type = Message
        self._bus = None
        self._selected = None
        self._art_key: tuple[str, str, str] | None = None
        self._art_png: bytes | None = None

    async def _connect(self):
        if self._bus is None:
            self._bus = await self._bus_type().connect()
        return self._bus

    async def _call(self, destination: str, path: str, interface: str, member: str, signature: str = "", body=None):
        bus = await self._connect()
        reply = await bus.call(self._message_type(
            destination=destination, path=path, interface=interface,
            member=member, signature=signature, body=body or [],
        ))
        if reply.message_type.name == "ERROR":
            raise RuntimeError(reply.body[0] if reply.body else "D-Bus error")
        return reply.body

    async def snapshot(self) -> MediaState:
        names = (await self._call(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "ListNames",
        ))[0]
        players = [name for name in names if name.startswith("org.mpris.MediaPlayer2.")]
        if not players:
            self._selected = None
            return MediaState(detail="No MPRIS player found")
        states = []
        for name in players:
            try:
                raw = (await self._call(
                    name, "/org/mpris/MediaPlayer2",
                    "org.freedesktop.DBus.Properties", "GetAll", "s",
                    ["org.mpris.MediaPlayer2.Player"],
                ))[0]
                values = {key: variant.value for key, variant in raw.items()}
                metadata = values.get("Metadata", {})
                title = metadata.get("xesam:title")
                artist = metadata.get("xesam:artist")
                length = metadata.get("mpris:length")
                position, duration = _valid_timeline(
                    values.get("Position", -1) / 1_000_000,
                    length.value / 1_000_000 if length else None,
                )
                artwork = metadata.get("mpris:artUrl")
                track_id = metadata.get("mpris:trackid")
                states.append((name, MediaState(
                    available=True,
                    title=(title.value if title else "Untitled") or "Untitled",
                    artist=", ".join(artist.value) if artist else "",
                    source=name.removeprefix("org.mpris.MediaPlayer2."),
                    playing=values.get("PlaybackStatus") == "Playing",
                    position_s=position,
                    duration_s=duration,
                    sampled_at=time.monotonic(),
                    playback_rate=float(values.get("Rate", 1.0)),
                ), str(artwork.value) if artwork else "", str(track_id.value) if track_id else ""))
            except (RuntimeError, KeyError, TypeError, ValueError):
                continue
        if not states:
            self._selected = None
            return MediaState(detail="MPRIS players did not respond")
        selected = next((item for item in states if item[1].playing), states[0])
        self._selected = selected[0]
        art_key = (selected[0], selected[2], selected[3])
        if art_key != self._art_key or (self._art_png is None and selected[2].startswith("file:")):
            self._art_key = art_key
            self._art_png = _local_artwork(selected[2])
        return replace(selected[1], artwork_png=self._art_png)

    async def command(self, action: str) -> bool:
        if not self._selected:
            await self.snapshot()
        if not self._selected:
            return False
        member = {"toggle": "PlayPause", "previous": "Previous", "next": "Next"}.get(action)
        if not member:
            return False
        await self._call(
            self._selected, "/org/mpris/MediaPlayer2",
            "org.mpris.MediaPlayer2.Player", member,
        )
        return True

    async def close(self) -> None:
        if self._bus is not None:
            self._bus.disconnect()
            self._bus = None


class MacMedia:
    """Controls Apple Music or Spotify through their documented scripting support."""

    def __init__(self) -> None:
        self._selected = None
        self._art_key: tuple[str, str, str] | None = None
        self._art_png: bytes | None = None

    @staticmethod
    def _script(source: str) -> str:
        result = subprocess.run(
            ["osascript", "-e", source], capture_output=True, text=True,
            timeout=2, check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "AppleScript failed")
        return result.stdout.strip()

    async def snapshot(self) -> MediaState:
        # 'running' avoids launching a player just to inspect it.
        candidates: list[MediaState] = []
        for app in ("Spotify", "Music"):
            try:
                if self._script(f'tell application "System Events" to get running of application process "{app}"') != "true":
                    continue
                raw = self._script(f'''
                    tell application "{app}"
                        set elapsedValue to ""
                        set durationValue to ""
                        try
                            set elapsedValue to player position as text
                            set durationValue to duration of current track as text
                        end try
                        return (player state as text) & (ASCII character 31) & ¬
                            (name of current track) & (ASCII character 31) & ¬
                            (artist of current track) & (ASCII character 31) & ¬
                            elapsedValue & (ASCII character 31) & durationValue
                    end tell
                ''')
                fields = raw.split("\x1f")
                if len(fields) >= 5:
                    try:
                        position = float(fields[3])
                        duration = float(fields[4]) / (1000 if app == "Spotify" else 1)
                        position, duration = _valid_timeline(position, duration)
                    except ValueError:
                        position = duration = None
                    state = MediaState(
                        available=True, title=fields[1], artist=fields[2], source=app,
                        playing=fields[0] == "playing", position_s=position,
                        duration_s=duration, sampled_at=time.monotonic(),
                    )
                    candidates.append(state)
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                continue
        if candidates:
            selected = next((state for state in candidates if state.playing), candidates[0])
            self._selected = selected.source
            art_key = (selected.source, selected.title, selected.artist)
            if art_key != self._art_key:
                self._art_key = art_key
                self._art_png = self._artwork(selected.source)
            return replace(selected, artwork_png=self._art_png)
        self._selected = None
        return MediaState(detail="Open Music or Spotify; other macOS players are not integrated yet")

    def _artwork(self, app: str) -> bytes | None:
        try:
            if app == "Spotify":
                return _local_artwork(self._script('tell application "Spotify" to get artwork url of current track'))
            with tempfile.TemporaryDirectory(prefix="notch-art-") as folder:
                path = Path(folder) / "artwork"
                self._script(f'''
                    tell application "Music" to set imageData to raw data of artwork 1 of current track
                    set outputFile to open for access POSIX file "{path}" with write permission
                    try
                        write imageData to outputFile
                    on error
                        close access outputFile
                        error "Artwork unavailable"
                    end try
                    close access outputFile
                ''')
                with path.open("rb") as image:
                    return _artwork_png(image.read(MAX_ART_BYTES + 1))
        except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError):
            pass
        return None

    async def command(self, action: str) -> bool:
        if not self._selected:
            await self.snapshot()
        if not self._selected:
            return False
        verb = {"toggle": "playpause", "previous": "previous track", "next": "next track"}.get(action)
        if not verb:
            return False
        self._script(f'tell application "{self._selected}" to {verb}')
        return True

    async def close(self) -> None:
        self._selected = None


def make_adapter() -> MediaAdapter:
    system = platform.system()
    if system == "Windows":
        return WindowsMedia()
    if system == "Linux":
        return LinuxMprisMedia()
    if system == "Darwin":
        return MacMedia()
    raise RuntimeError(f"Unsupported platform: {system}")
