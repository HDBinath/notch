import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

from music_app.__main__ import status_payload
from music_app.media import (
    ART_SIZE, LinuxMprisMedia, MacMedia, MediaState, WindowsMedia,
    _artwork_png, _local_artwork, _valid_timeline,
)
from music_app.notch import NotchView


def image_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (160, 90), "blue").save(output, format="PNG")
    return output.getvalue()


TEST_DIR = Path(__file__).resolve().parent


def run_immediate(coroutine):
    """Run mock-backed async code without opening an OS event loop."""
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("The mock coroutine unexpectedly suspended")


class MediaTests(unittest.TestCase):
    def test_artwork_is_normalized_and_remote_urls_are_ignored(self):
        artwork = _artwork_png(image_bytes())
        self.assertIsNotNone(artwork)
        with Image.open(BytesIO(artwork)) as image:
            self.assertEqual(image.size, (ART_SIZE, ART_SIZE))
        with tempfile.TemporaryDirectory(dir=TEST_DIR) as folder:
            path = Path(folder) / "cover.png"
            path.write_bytes(image_bytes())
            self.assertEqual(_local_artwork(path.as_uri()), artwork)
        self.assertIsNone(_local_artwork("https://example.com/cover.png"))
        self.assertIsNone(_artwork_png(b"not an image"))

    def test_timeline_validation_and_display(self):
        self.assertEqual(_valid_timeline(90, 60), (60, 60))
        self.assertEqual(_valid_timeline(None, 60), (None, None))
        self.assertEqual(_valid_timeline(5, 0), (None, None))
        view = NotchView.__new__(NotchView)
        view.state = MediaState(available=True, position_s=15, duration_s=30)
        self.assertEqual(view._progress_end(), 272)
        view.state = MediaState(available=True, position_s=15, duration_s=30,
                                sampled_at=100, playing=True)
        with patch("music_app.notch.time.monotonic", return_value=105):
            self.assertEqual(view._progress_end(), 120 + 304 * 20 / 30)
        view.state = MediaState(available=True, playing=True)
        self.assertIsNone(view._progress_end())

    def test_windows_session_timeline_and_thumbnail(self):
        class PlaybackStatus(IntEnum):
            PLAYING = 4
            PAUSED = 5

        session = SimpleNamespace(
            try_get_media_properties_async=AsyncMock(return_value=SimpleNamespace(
                title="Video", artist="Creator", thumbnail=object(),
            )),
            get_playback_info=lambda: SimpleNamespace(playback_status=PlaybackStatus.PLAYING),
            source_app_user_model_id="Browser",
            get_timeline_properties=lambda: SimpleNamespace(
                start_time=timedelta(), end_time=timedelta(seconds=100),
                position=timedelta(seconds=25),
                last_updated_time=datetime.now(timezone.utc),
            ),
        )
        adapter = WindowsMedia.__new__(WindowsMedia)
        adapter._session = AsyncMock(return_value=session)
        adapter._thumbnail = AsyncMock(side_effect=lambda reference: b"png" if reference else None)
        adapter._art_key = adapter._art_png = None
        state = run_immediate(adapter.snapshot())
        self.assertEqual((state.title, state.duration_s, state.artwork_png), ("Video", 100, b"png"))
        self.assertTrue(state.playing)
        self.assertGreaterEqual(state.position_s, 25)
        next_session = SimpleNamespace(
            try_get_media_properties_async=AsyncMock(return_value=SimpleNamespace(
                title="Next item", artist="", thumbnail=None,
            )),
            get_playback_info=lambda: SimpleNamespace(playback_status=PlaybackStatus.PAUSED),
            source_app_user_model_id="OtherPlayer",
            get_timeline_properties=lambda: SimpleNamespace(
                start_time=timedelta(), end_time=timedelta(),
                position=timedelta(), last_updated_time=datetime.now(timezone.utc),
            ),
        )
        adapter._session = AsyncMock(return_value=next_session)
        state = run_immediate(adapter.snapshot())
        self.assertEqual(state.title, "Next item")
        self.assertFalse(state.playing)
        self.assertIsNone(state.position_s)
        self.assertIsNone(state.artwork_png)

    def test_mpris_position_and_local_artwork(self):
        with tempfile.TemporaryDirectory(dir=TEST_DIR) as folder:
            path = Path(folder) / "art.png"
            path.write_bytes(image_bytes())
            variant = lambda value: SimpleNamespace(value=value)
            raw = {
                "PlaybackStatus": variant("Playing"), "Position": variant(12_000_000),
                "Rate": variant(1.0),
                "Metadata": variant({
                    "xesam:title": variant("Track"), "xesam:artist": variant(["Artist"]),
                    "mpris:length": variant(30_000_000),
                    "mpris:artUrl": variant(path.as_uri()),
                    "mpris:trackid": variant("/track/1"),
                }),
            }
            adapter = LinuxMprisMedia.__new__(LinuxMprisMedia)
            adapter._selected = adapter._art_key = adapter._art_png = None
            adapter._call = AsyncMock(side_effect=[[["org.mpris.MediaPlayer2.test"]], [raw]])
            state = run_immediate(adapter.snapshot())
            self.assertEqual((state.position_s, state.duration_s), (12, 30))
            self.assertIsNotNone(state.artwork_png)

    def test_mac_timing_and_remote_artwork_fallback(self):
        adapter = MacMedia()
        adapter._script = lambda source: (
            "true" if "running of application process \"Spotify\"" in source else
            "false" if "running of application process" in source else
            "playing\x1fTrack\x1fArtist\x1f12\x1f30000" if "elapsedValue" in source else
            "https://example.com/cover.png"
        )
        state = run_immediate(adapter.snapshot())
        self.assertEqual((state.position_s, state.duration_s), (12, 30))
        self.assertIsNone(state.artwork_png)

    def test_status_remains_json_safe(self):
        payload = json.loads(json.dumps(status_payload(MediaState(
            available=True, artwork_png=b"image",
        ))))
        self.assertTrue(payload["artwork_available"])
        self.assertNotIn("artwork_png", payload)
        self.assertNotIn("sampled_at", payload)


if __name__ == "__main__":
    unittest.main()
