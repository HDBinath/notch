import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from music_app import power
from music_app.media import MediaState
from music_app.notch import NotchView


TEST_DIR = Path(__file__).resolve().parent


class BatteryTests(unittest.TestCase):
    def test_linux_battery_and_desktop(self):
        with tempfile.TemporaryDirectory(dir=TEST_DIR) as folder:
            root = Path(folder)
            self.assertIsNone(power._linux_battery(root))
            battery = root / "BAT0"
            battery.mkdir()
            (battery / "type").write_text("Battery")
            (battery / "capacity").write_text("73")
            self.assertEqual(power._linux_battery(root), 73)
            (battery / "status").write_text("Charging")
            self.assertEqual(power._linux_battery_status(root), power.BatteryStatus(73, True))
            (battery / "present").write_text("0")
            self.assertIsNone(power._linux_battery(root))

    def test_windows_battery_and_no_battery_flag(self):
        def reported(flag, percent):
            def get_status(pointer):
                pointer._obj.BatteryFlag = flag
                pointer._obj.BatteryLifePercent = percent
                return 1
            with patch.object(power.ctypes, "windll", SimpleNamespace(
                kernel32=SimpleNamespace(GetSystemPowerStatus=get_status),
            ), create=True):
                return power._windows_battery_status()

        self.assertEqual(reported(1, 81), power.BatteryStatus(81, False))
        self.assertEqual(reported(8, 19), power.BatteryStatus(19, True))
        self.assertIsNone(reported(128, 255))
        self.assertIsNone(reported(255, 75))

    def test_mac_battery_fallback_ignores_desktop(self):
        with patch.object(power, "_mac_iokit_battery_status", return_value=None), patch.object(
            power.subprocess, "run", return_value=SimpleNamespace(
                returncode=0, stdout=" -InternalBattery-0 (id=1)\t62%; discharging",
            ),
        ):
            self.assertEqual(power._mac_battery_status(), power.BatteryStatus(62, False))
        with patch.object(power, "_mac_iokit_battery_status", return_value=None), patch.object(
            power.subprocess, "run", return_value=SimpleNamespace(
                returncode=0, stdout=" -InternalBattery-0 (id=1)\t62%; charging",
            ),
        ):
            self.assertEqual(power._mac_battery_status(), power.BatteryStatus(62, True))
        with patch.object(power, "_mac_iokit_battery_status", return_value=None), patch.object(
            power.subprocess, "run", return_value=SimpleNamespace(
                returncode=0, stdout="Now drawing from 'AC Power'",
            ),
        ):
            self.assertIsNone(power._mac_battery())

    def test_low_battery_ring_and_bolt(self):
        view = NotchView.__new__(NotchView)
        def colors(percent, charging):
            view.battery_percent = percent
            view.battery_charging = charging
            icon = view._battery_icon(10, 1)
            return [icon.getpixel((x, y)) for y in range(icon.height)
                    for x in range(icon.width) if icon.getpixel((x, y))[3] > 200]

        for percent in (0, 19):
            pixels = colors(percent, False)
            self.assertTrue(any(r > 200 and g < 120 for r, g, _b, _a in pixels))
            self.assertTrue(any(min(r, g, b) > 220 for r, g, b, _a in pixels))

        pixels = colors(19, True)
        self.assertTrue(any(g > 150 and g > r for r, g, _b, _a in pixels))
        self.assertTrue(any(min(r, g, b) > 220 for r, g, b, _a in pixels))

        for percent in (20, 73):
            pixels = colors(percent, False)
            self.assertFalse(any(min(r, g, b) > 220 for r, g, b, _a in pixels))


class StageTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.root.withdraw()
        self.stages = []
        self.view = NotchView(self.root, lambda _action: None, self.stages.append)
        self.view.ANIMATION_MS = 1
        self.view.FRAME_MS = 1
        self.view.HALF_TIMEOUT_MS = 80

    def tearDown(self):
        if hasattr(self, "root"):
            self.view.close()
            self.root.destroy()

    def wait_for(self, stage):
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            self.root.update()
            if self.view.stage == stage:
                return
            time.sleep(0.005)
        self.fail(f"Did not reach {stage}; stages: {self.stages}")

    def test_full_half_minimal_and_reentry(self):
        self.view.expand()
        self.wait_for(NotchView.FULL)
        self.view.show_half()
        self.wait_for(NotchView.HALF)
        self.assertEqual(self.view.size, NotchView.HALF_DESKTOP)
        self.view.expand()
        self.wait_for(NotchView.FULL)
        self.view.show_half()
        self.wait_for(NotchView.HALF)
        self.wait_for(NotchView.MINIMAL)
        self.assertEqual(self.stages[-3:], [NotchView.FULL, NotchView.HALF, NotchView.MINIMAL])

    def test_pointer_leave_schedules_half_open(self):
        self.view.expand()
        self.wait_for(NotchView.FULL)
        with patch.object(self.view, "_schedule_collapse") as schedule:
            self.view._leave(None)
        schedule.assert_called_once_with(240)

    def test_dismiss_steps_through_both_stages(self):
        self.view.expand()
        self.wait_for(NotchView.FULL)
        self.view.dismiss()
        self.wait_for(NotchView.HALF)
        self.view.dismiss()
        self.wait_for(NotchView.MINIMAL)

    def test_battery_resize_does_not_restart_half_timeout(self):
        self.view.set_battery(73)
        self.view.show_half()
        self.wait_for(NotchView.HALF)
        self.assertEqual(self.view.size, NotchView.HALF_BATTERY)
        deadline = self.view._half_deadline
        self.view.set_battery(None)
        limit = time.monotonic() + 0.5
        while self.view.size != NotchView.HALF_DESKTOP and time.monotonic() < limit:
            self.root.update()
            time.sleep(0.005)
        self.assertEqual(self.view.size, NotchView.HALF_DESKTOP)
        self.assertEqual(self.view._half_deadline, deadline)

    def test_charging_change_updates_without_percent_change(self):
        self.view.set_battery(power.BatteryStatus(19, False))
        self.assertFalse(self.view.battery_charging)
        self.view.set_battery(power.BatteryStatus(19, True))
        self.assertTrue(self.view.battery_charging)

    def test_playback_bars_animate_only_when_playing(self):
        lines = []
        canvas = SimpleNamespace(create_line=lambda *args, **_kwargs: lines.append(args))
        self.view.state = MediaState(available=True, playing=True)
        self.view._sound_phase = 0
        self.view._draw_sound_bars(canvas, (1, 2, 3, 4), 18, 1)
        playing_lines = list(lines)
        lines.clear()
        self.view._sound_phase = 1
        self.view._draw_sound_bars(canvas, (1, 2, 3, 4), 18, 1)
        self.assertNotEqual(lines, playing_lines)
        lines.clear()
        self.view.state = MediaState(available=True, playing=False)
        self.view._draw_sound_bars(canvas, (1, 2, 3, 4), 18, 1)
        paused_lines = list(lines)
        lines.clear()
        self.view._sound_phase = 2
        self.view._draw_sound_bars(canvas, (1, 2, 3, 4), 18, 1)
        self.assertEqual(lines, paused_lines)
        lines.clear()
        self.view.state = MediaState()
        self.view._draw_sound_bars(canvas, (1, 2, 3, 4), 18, 1)
        self.assertEqual(lines, [])


if __name__ == "__main__":
    unittest.main()
