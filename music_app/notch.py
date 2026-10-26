"""A compact, curved music notch drawn with Tk's Canvas."""

from __future__ import annotations

import platform
import tkinter as tk
from datetime import datetime
from tkinter import font as tkfont
from typing import Callable

from .media import MediaState


TRANSPARENT = "#ff00ff"
BLACK = "#030408"
MUTED = "#a8a9af"
WHITE = "#f8f9fb"
CYAN = "#88d8ff"


class NotchView:
    CLOSED = (60, 8)
    OPEN = (516, 198)
    ANIMATION_MS = 200

    def __init__(self, root: tk.Tk, on_command: Callable[[str], None], on_expanded: Callable[[bool], None]) -> None:
        self.root = root
        self.on_command = on_command
        self.on_expanded = on_expanded
        self.state = MediaState(detail="Looking for music…")
        self.expanded = False
        self._target_open = False
        self.size = self.CLOSED
        self._animation_id: str | None = None
        self._collapse_id: str | None = None
        self._last_clock = ""
        self._title_font = tkfont.Font(family="Segoe UI", size=13, weight="bold")
        self._artist_font = tkfont.Font(family="Segoe UI", size=9)

        root.title("Notch Music")
        root.overrideredirect(True)
        root.resizable(False, False)
        root.attributes("-topmost", True)
        window_bg = TRANSPARENT if platform.system() == "Windows" else BLACK
        root.configure(bg=window_bg)
        if platform.system() == "Windows":
            root.wm_attributes("-transparentcolor", TRANSPARENT)
        self.canvas = tk.Canvas(root, width=self.CLOSED[0], height=self.CLOSED[1],
                                bg=window_bg, bd=0, highlightthickness=0)
        self.canvas.pack()
        self.canvas.bind("<Enter>", self._enter)
        self.canvas.bind("<Leave>", self._leave)
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Motion>", self._motion)
        root.bind("<Escape>", lambda _event: self.collapse())
        self._position(*self.CLOSED)
        self._draw()
        root.after(15_000, self._clock_tick)

    def set_state(self, state: MediaState) -> None:
        if state == self.state:
            return
        self.state = state
        self._draw()

    def expand(self) -> None:
        self._cancel_collapse()
        self._animate(True)
        # A tray-opened panel also returns to the small notch when untouched.
        self._schedule_collapse(5_000)

    def collapse(self) -> None:
        self._cancel_collapse()
        self._animate(False)

    def _position(self, width: int, height: int) -> None:
        x = (self.root.winfo_screenwidth() - width) // 2
        self.root.geometry(f"{width}x{height}+{x}+0")
        self.canvas.configure(width=width, height=height)

    def _animate(self, opening: bool) -> None:
        self._target_open = opening
        if self._animation_id is not None:
            self.root.after_cancel(self._animation_id)
            self._animation_id = None
        start_w, start_h = self.size
        target_w, target_h = self.OPEN if opening else self.CLOSED
        if (start_w, start_h) == (target_w, target_h):
            self.expanded = opening
            self.on_expanded(opening)
            self._draw()
            return
        steps = 12

        def frame(step: int) -> None:
            fraction = step / steps
            eased = 1 - (1 - fraction) ** 3 if opening else fraction ** 3
            width = round(start_w + (target_w - start_w) * eased)
            height = round(start_h + (target_h - start_h) * eased)
            self.size = (width, height)
            self.expanded = opening if step == steps else height > 110
            self._position(width, height)
            self._draw()
            if step < steps:
                self._animation_id = self.root.after(self.ANIMATION_MS // steps, frame, step + 1)
            else:
                self._animation_id = None
                self.expanded = opening
                self.on_expanded(opening)

        frame(1)

    def _rounded_panel(self, w: int, h: int) -> None:
        c = self.canvas
        if h < 95:
            # A small, unadorned bump attached to the top edge of the screen.
            radius = max(4, min(12, h // 2))
            c.create_rectangle(0, 0, w, h - radius, fill=BLACK, outline=BLACK)
            c.create_rectangle(radius, h - radius, w - radius, h, fill=BLACK, outline=BLACK)
            c.create_oval(0, h - 2 * radius, 2 * radius, h, fill=BLACK, outline=BLACK)
            c.create_oval(w - 2 * radius, h - 2 * radius, w, h, fill=BLACK, outline=BLACK)
        else:
            # Shoulders narrow slightly toward the top; the bottom rolls inward.
            points = [
                27, 0, w - 27, 0, w - 12, 4, w - 4, 18,
                w - 3, 37, w - 3, h - 44, w - 8, h - 22,
                w - 24, h - 5, w - 47, h, 47, h, 24, h - 5,
                8, h - 22, 3, h - 44, 3, 37, 4, 18, 12, 4,
            ]
            c.create_polygon(points, fill=BLACK, outline=BLACK, smooth=True, splinesteps=24)

    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        w, h = self.size
        self._rounded_panel(w, h)
        if h < 110:
            return
        if h < 165:
            return
        self._draw_album(c)
        c.create_oval(166, 18, 180, 32, outline="#22c988", width=2)
        now = datetime.now().strftime("%a %d %b %Y  %H:%M")
        c.create_text(186, 25, text=now, anchor="w", fill=WHITE, font=("Segoe UI", 10))
        # Simple sound bars echo the small visualizer in the reference.
        for x, height in ((469, 10), (475, 17), (481, 12), (487, 7)):
            c.create_line(x, 28 - height // 2, x, 28 + height // 2, fill=WHITE, width=2)

        title = self._fit(self.state.title if self.state.available else "Nothing playing", self._title_font, 310)
        artist = self._fit(
            " · ".join(x for x in (self.state.artist, self.state.source) if x)
            if self.state.available else self.state.detail,
            self._artist_font, 310,
        )
        c.create_text(120, 75, text=title, anchor="w", fill=WHITE, font=self._title_font)
        c.create_text(120, 96, text=artist, anchor="w", fill=MUTED, font=self._artist_font)

        c.create_line(120, 133, 424, 133, fill="#48494d", width=4, capstyle="round")
        if self.state.available and self.state.playing:
            # No playback position is exposed in the current adapter yet.
            # The short accent is an activity cue, not a fabricated seek value.
            c.create_line(120, 133, 171, 133, fill=WHITE, width=4, capstyle="round")
        c.create_text(120, 147, text="NOW PLAYING" if self.state.playing else "PAUSED" if self.state.available else "NO PLAYER",
                      anchor="w", fill="#9ca0aa", font=("Segoe UI", 8))

        can_control = self.state.available
        control_color = WHITE if can_control else "#62656c"
        c.create_text(216, 169, text="◀◀", fill=control_color, font=("Segoe UI Symbol", 11, "bold"), tags=("previous",))
        c.create_text(258, 169, text="Ⅱ" if self.state.playing else "▶", fill=control_color,
                      font=("Segoe UI Symbol", 14, "bold"), tags=("toggle",))
        c.create_text(300, 169, text="▶▶", fill=control_color, font=("Segoe UI Symbol", 11, "bold"), tags=("next",))
        for x in (245, 254, 263, 272):
            c.create_oval(x, 187, x + 3, 190, fill="#5b5d64", outline="")

    def _draw_album(self, c: tk.Canvas) -> None:
        # A restrained placeholder until artwork is supplied by the adapter.
        c.create_rectangle(26, 46, 99, 119, fill="#b6d9ff", outline="")
        for y in range(51, 117, 9):
            c.create_line(30, y, 96, y, fill="#e1ecff", width=3)
        c.create_oval(38, 58, 88, 108, fill="#4f9eff", outline="#eaf6ff", width=2)
        c.create_line(50, 107, 77, 59, fill="#eaf6ff", width=3)

    @staticmethod
    def _fit(text: str, font: tkfont.Font, width: int) -> str:
        if font.measure(text) <= width:
            return text
        while text and font.measure(text + "…") > width:
            text = text[:-1]
        return text + "…"

    def _enter(self, _event: tk.Event) -> None:
        self._cancel_collapse()
        if not self._target_open:
            self.expand()

    def _leave(self, _event: tk.Event) -> None:
        if self.expanded:
            self._schedule_collapse(1_100)

    def _click(self, event: tk.Event) -> None:
        if not self.expanded:
            self.expand()
            return
        tags = self.canvas.gettags("current")
        for action in ("previous", "toggle", "next"):
            if action in tags and self.state.available:
                self.on_command(action)
                self._schedule_collapse(5_000)
                return

    def _motion(self, _event: tk.Event) -> None:
        tags = self.canvas.gettags("current")
        interactive = not self.expanded or (self.state.available and any(tag in tags for tag in ("previous", "toggle", "next")))
        self.canvas.configure(cursor="hand2" if interactive else "arrow")

    def _cancel_collapse(self) -> None:
        if self._collapse_id is not None:
            self.root.after_cancel(self._collapse_id)
            self._collapse_id = None

    def _schedule_collapse(self, delay_ms: int) -> None:
        self._cancel_collapse()

        def maybe_collapse() -> None:
            self._collapse_id = None
            x = self.root.winfo_pointerx() - self.root.winfo_rootx()
            y = self.root.winfo_pointery() - self.root.winfo_rooty()
            w, h = self.size
            if not (0 <= x < w and 0 <= y < h):
                self.collapse()

        self._collapse_id = self.root.after(delay_ms, maybe_collapse)

    def _clock_tick(self) -> None:
        current = datetime.now().strftime("%Y%m%d%H%M")
        if self.expanded and current != self._last_clock:
            self._last_clock = current
            self._draw()
        self.root.after(15_000, self._clock_tick)
