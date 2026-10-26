from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MediaState:
    available: bool = False
    title: str = "Nothing playing"
    artist: str = ""
    source: str = ""
    playing: bool = False
    detail: str = ""


class MediaAdapter(Protocol):
    async def snapshot(self) -> MediaState: ...
    async def command(self, action: str) -> bool: ...
    async def close(self) -> None: ...


class WindowsMedia:
    def __init__(self) -> None:
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager,
        )

        self._manager_type = GlobalSystemMediaTransportControlsSessionManager
        self._manager = None

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
        status = str(info.playback_status).lower() if info else ""
        source_id = str(session.source_app_user_model_id or "")
        return MediaState(
            available=True,
            title=(props.title or "Untitled") if props else "Untitled",
            artist=(props.artist or "") if props else "",
            source=source_id.rsplit("!", 1)[-1].split(".")[0] if source_id else "",
            playing="playing" in status and "paused" not in status,
        )

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
                states.append((name, MediaState(
                    available=True,
                    title=(title.value if title else "Untitled") or "Untitled",
                    artist=", ".join(artist.value) if artist else "",
                    source=name.removeprefix("org.mpris.MediaPlayer2."),
                    playing=values.get("PlaybackStatus") == "Playing",
                )))
            except (RuntimeError, KeyError, ValueError):
                continue
        if not states:
            self._selected = None
            return MediaState(detail="MPRIS players did not respond")
        selected = next((item for item in states if item[1].playing), states[0])
        self._selected = selected[0]
        return selected[1]

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
                raw = self._script(
                    f'tell application "{app}" to get (player state as text) & linefeed & '
                    '(name of current track) & linefeed & (artist of current track)'
                )
                lines = raw.splitlines()
                if len(lines) >= 3:
                    state = MediaState(True, lines[1], lines[2], app, lines[0] == "playing")
                    candidates.append(state)
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                continue
        if candidates:
            selected = next((state for state in candidates if state.playing), candidates[0])
            self._selected = selected.source
            return selected
        self._selected = None
        return MediaState(detail="Open Music or Spotify; other macOS players are not integrated yet")

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
