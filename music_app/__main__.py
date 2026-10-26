from __future__ import annotations

import argparse
import asyncio
import json
import os
import queue
import signal
import sys
import threading
from dataclasses import asdict
from pathlib import Path

# A project-local dependency directory is useful in restricted workspaces.
vendor = Path(__file__).resolve().parent.parent / ".vendor"
if vendor.is_dir():
    sys.path.insert(0, str(vendor))

from .media import MediaState, make_adapter


class MediaWorker:
    def __init__(self) -> None:
        self.actions: queue.Queue[str] = queue.Queue()
        self.updates: queue.Queue[MediaState] = queue.Queue(maxsize=1)
        self.stop_event = threading.Event()
        self.panel_visible = threading.Event()
        self.thread = threading.Thread(target=self._run, name="media-worker", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=1.0)

    def command(self, action: str) -> None:
        if action in {"previous", "toggle", "next"}:
            self.actions.put(action)

    def _publish(self, state: MediaState) -> None:
        try:
            self.updates.put_nowait(state)
        except queue.Full:
            try:
                self.updates.get_nowait()
            except queue.Empty:
                pass
            self.updates.put_nowait(state)

    def _run(self) -> None:
        try:
            asyncio.run(self._loop())
        except Exception:
            pass

    async def _loop(self) -> None:
        try:
            adapter = make_adapter()
        except Exception as exc:
            self._publish(MediaState(detail=f"Media adapter unavailable: {exc}"))
            return
        try:
            while not self.stop_event.is_set():
                try:
                    while True:
                        action = self.actions.get_nowait()
                        try:
                            await adapter.command(action)
                        except Exception as exc:
                            self._publish(MediaState(detail=f"Control failed: {exc}"))
                except queue.Empty:
                    pass
                try:
                    self._publish(await asyncio.wait_for(adapter.snapshot(), timeout=5))
                except Exception as exc:
                    self._publish(MediaState(detail=f"Media session unavailable: {exc}"))
                # Slow polling keeps the hidden tray app quiet. A later native
                # implementation can subscribe to platform change events.
                for _ in range(20 if self.panel_visible.is_set() else 80):
                    if self.stop_event.is_set() or not self.actions.empty():
                        break
                    await asyncio.sleep(0.1)
        finally:
            await adapter.close()


def run_ui(show_on_start: bool) -> None:
    import tkinter as tk

    import pystray
    from PIL import Image, ImageDraw

    from .notch import NotchView

    # Ensure pystray's win32 loop runs in a daemon thread so it cannot block process termination
    try:
        import pystray._win32

        def _daemon_run_detached(self_tray, setup=None):
            self_tray._start_setup(setup)
            t = threading.Thread(target=self_tray._run, name="pystray-detached", daemon=True)
            t.start()

        pystray._win32.Icon.run_detached = _daemon_run_detached
    except (ImportError, AttributeError):
        pass

    worker = MediaWorker()
    root = tk.Tk()
    _quitting = threading.Event()

    def quit_app() -> None:
        if _quitting.is_set():
            return
        _quitting.set()
        try:
            worker.stop()
        except Exception:
            pass
        try:
            tray.stop()
        except Exception:
            pass
        try:
            root.quit()
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass

    view = NotchView(
        root,
        worker.command,
        lambda expanded: (
            worker.panel_visible.set() if expanded else worker.panel_visible.clear()
        ),
        on_quit=quit_app,
    )

    def icon_image() -> Image.Image:
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((3, 16, 61, 48), radius=14, fill="#030408")
        draw.line((15, 25, 49, 25), fill="#88d8ff", width=3)
        return image

    ui_events: queue.Queue[str] = queue.Queue()
    tray = pystray.Icon(
        "notch-music", icon_image(), "Notch Music",
        menu=pystray.Menu(
            pystray.MenuItem("Open music notch", lambda _icon, _item: ui_events.put("show"), default=True),
            pystray.MenuItem("Quit", lambda _icon, _item: ui_events.put("quit")),
        ),
    )

    def refresh() -> None:
        if _quitting.is_set():
            return
        try:
            while True:
                event = ui_events.get_nowait()
                if event == "show":
                    view.expand()
                    root.lift()
                elif event == "quit":
                    quit_app()
                    return
        except queue.Empty:
            pass
        try:
            while True:
                state = worker.updates.get_nowait()
                view.set_state(state)
                tray.title = f"Notch Music — {state.title}" if state.available else "Notch Music"
        except queue.Empty:
            pass
        if not _quitting.is_set():
            root.after(750, refresh)

    # Periodic heartbeat giving Python interpreter opportunities to process OS signals (e.g. Ctrl+C)
    def _signal_pump() -> None:
        if not _quitting.is_set():
            root.after(200, _signal_pump)

    def handle_sigint(_signum, _frame) -> None:
        quit_app()

    try:
        signal.signal(signal.SIGINT, handle_sigint)
    except (ValueError, AttributeError):
        pass

    root.protocol("WM_DELETE_WINDOW", view.collapse)
    worker.start()
    tray.run_detached()
    if show_on_start:
        view.expand()
    root.after(200, refresh)
    root.after(200, _signal_pump)

    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        quit_app()
        os._exit(0)


async def print_status() -> None:
    adapter = make_adapter()
    try:
        print(json.dumps(asdict(await asyncio.wait_for(adapter.snapshot(), timeout=5)), ensure_ascii=False))
    finally:
        await adapter.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Control music already playing in another app")
    parser.add_argument("--status", action="store_true", help="Print one media-session snapshot and exit")
    parser.add_argument("--show", action="store_true", help="Open the controls immediately")
    args = parser.parse_args()
    try:
        if args.status:
            asyncio.run(print_status())
        else:
            run_ui(args.show)
    except KeyboardInterrupt:
        os._exit(0)


if __name__ == "__main__":
    main()
