"""A compact, curved music notch drawn with Tk's Canvas."""

from __future__ import annotations

import platform
import time
import tkinter as tk
import base64
import math
from dataclasses import replace
from datetime import datetime
from tkinter import font as tkfont
from tkinter import messagebox
from typing import Callable

from PIL import Image, ImageDraw, ImageTk

from .media import MediaState
from .power import BatteryStatus
from .tasks import TaskStore


TRANSPARENT = "#ff00ff"
BLACK = "#030408"
MUTED = "#a8a9af"
WHITE = "#f8f9fb"
CYAN = "#88d8ff"
GREEN = "#27d984"


class NotchView:
    MINIMAL = "minimal"
    HALF = "half"
    FULL = "full"
    SIZES = {MINIMAL: (60, 8), FULL: (516, 198)}
    HALF_BATTERY = (180, 36)
    HALF_DESKTOP = (150, 36)
    ANIMATION_MS = 260
    FRAME_MS = 16
    HALF_TIMEOUT_MS = 60_000

    def __init__(
        self,
        root: tk.Tk,
        on_command: Callable[[str], None],
        on_stage_change: Callable[[str], None],
        on_quit: Callable[[], None] | None = None,
        task_store: TaskStore | None = None,
    ) -> None:
        self.root = root
        self.on_command = on_command
        self.on_stage_change = on_stage_change
        self.on_quit = on_quit
        self.state = MediaState(detail="Looking for music…")
        self.tasks = task_store if task_store is not None else TaskStore()
        self.page = "music"
        self._task_scroll = 0
        self._selected_task_id: str | None = None
        self._task_editor: tk.Toplevel | None = None
        self._icon_cache: dict[tuple[str, str, int], ImageTk.PhotoImage] = {}
        self.stage = self.MINIMAL
        self.target_stage = self.MINIMAL
        self.size = self.SIZES[self.MINIMAL]
        self.battery_percent: int | None = None
        self.battery_charging = False
        self._battery_photo: ImageTk.PhotoImage | None = None
        self._sound_phase = 0
        self._animation_id: str | None = None
        self._collapse_id: str | None = None
        self._half_timeout_id: str | None = None
        self._half_deadline: float | None = None
        self._clock_id: str | None = None
        self._progress_tick_id: str | None = None
        self._closed = False
        self._hover_tag: str | None = None
        self._pressed_tag: str | None = None
        self._press_id: str | None = None
        self._pending_toggle: bool | None = None
        self._pending_id: str | None = None
        self._last_clock = ""
        self._artwork_photo: tk.PhotoImage | None = None
        self._progress_id: int | None = None
        self._title_font = tkfont.Font(family="Segoe UI", size=13, weight="bold")
        self._artist_font = tkfont.Font(family="Segoe UI", size=9)
        self._task_font = tkfont.Font(family="Segoe UI", size=10)

        root.title("Notch Music")
        root.overrideredirect(True)
        root.resizable(False, False)
        root.attributes("-topmost", True)
        window_bg = TRANSPARENT if platform.system() == "Windows" else BLACK
        root.configure(bg=window_bg)
        if platform.system() == "Windows":
            root.wm_attributes("-transparentcolor", TRANSPARENT)
        self.canvas = tk.Canvas(root, width=self.size[0], height=self.size[1],
                                bg=window_bg, bd=0, highlightthickness=0)
        self.canvas.pack()
        self.canvas.bind("<Enter>", self._enter)
        self.canvas.bind("<Leave>", self._leave)
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", self._wheel)
        self.canvas.bind("<Button-5>", self._wheel)
        root.bind("<Escape>", lambda _event: self.dismiss())
        root.bind("<Left>", lambda _event: self._key_page("music"))
        root.bind("<Right>", lambda _event: self._key_page("todo"))
        self._position(*self.size)
        self._draw()
        self._clock_id = root.after(15_000, self._clock_tick)
        self._progress_tick_id = root.after(250, self._progress_tick)

    def close(self) -> None:
        self._closed = True
        if self._task_editor is not None:
            self._task_editor.destroy()
            self._task_editor = None
        for timer in (self._animation_id, self._collapse_id, self._half_timeout_id,
                      self._clock_id, self._progress_tick_id, self._press_id, self._pending_id):
            if timer is not None:
                self.root.after_cancel(timer)
        self._animation_id = self._collapse_id = self._half_timeout_id = None
        self._clock_id = self._progress_tick_id = self._press_id = self._pending_id = None

    def set_state(self, state: MediaState) -> None:
        if self._pending_toggle is not None:
            if not state.available or state.playing == self._pending_toggle:
                self._clear_pending_toggle()
            else:
                # Poll results from before the command must not undo immediate feedback.
                state = replace(state, playing=self._pending_toggle)
        if state == self.state:
            return
        if state.artwork_png != self.state.artwork_png:
            self._artwork_photo = None
            if state.artwork_png:
                try:
                    self._artwork_photo = tk.PhotoImage(data=base64.b64encode(state.artwork_png).decode("ascii"))
                except tk.TclError:
                    pass
        self.state = state
        self._draw()

    def set_battery(self, battery: BatteryStatus | int | None) -> None:
        percent = battery.percent if isinstance(battery, BatteryStatus) else battery
        percent = percent if percent is not None and 0 <= percent <= 100 else None
        charging = battery.charging if isinstance(battery, BatteryStatus) and percent is not None else False
        if percent == self.battery_percent and charging == self.battery_charging:
            return
        self.battery_percent = percent
        self.battery_charging = charging
        if self.target_stage == self.HALF:
            self._animate(self.HALF)
        else:
            self._draw()

    def _size_for(self, stage: str) -> tuple[int, int]:
        if stage == self.HALF:
            return self.HALF_BATTERY if self.battery_percent is not None else self.HALF_DESKTOP
        return self.SIZES[stage]

    def _progress_end(self) -> float | None:
        state = self.state
        position, duration = state.position_s, state.duration_s
        if position is None or duration is None or not math.isfinite(position) or not math.isfinite(duration) or duration <= 0:
            return None
        if state.playing and state.sampled_at is not None:
            rate = state.playback_rate if math.isfinite(state.playback_rate) and state.playback_rate > 0 else 1.0
            position += max(0.0, time.monotonic() - state.sampled_at) * rate
        return 120 + 304 * min(1.0, max(0.0, position / duration))

    def _progress_tick(self) -> None:
        if self._closed:
            return
        if self.target_stage == self.FULL and self.page == "music" and self._progress_id is not None:
            end = self._progress_end()
            if end is not None:
                self.canvas.coords(self._progress_id, 120, 133, end, 133)
        if (self.target_stage == self.HALF or self.target_stage == self.FULL and self.page == "music") and self.state.available and self.state.playing:
            self._sound_phase = (self._sound_phase + 1) % 4
            self._draw()
        self._progress_tick_id = self.root.after(250, self._progress_tick)

    def expand(self) -> None:
        self._cancel_collapse()
        self._cancel_half_timeout()
        self._animate(self.FULL)
        # A tray-opened panel returns to the strip if the pointer never enters it.
        self._schedule_collapse(5_000)

    def show_half(self) -> None:
        self._cancel_collapse()
        self._cancel_half_timeout()
        self._animate(self.HALF)

    def show_minimal(self) -> None:
        self._cancel_collapse()
        self._cancel_half_timeout()
        self._animate(self.MINIMAL)

    def dismiss(self) -> None:
        if self._task_editor is not None:
            self._close_task_editor()
            return
        if self.target_stage == self.FULL:
            self.show_half()
        elif self.target_stage == self.HALF:
            self.show_minimal()

    def _position(self, width: int, height: int) -> None:
        x = (self.root.winfo_screenwidth() - width) // 2
        self.root.geometry(f"{width}x{height}+{x}+0")
        self.canvas.configure(width=width, height=height)

    def _animate(self, target: str) -> None:
        target_w, target_h = self._size_for(target)
        if self.target_stage == target and self.size == (target_w, target_h):
            return
        self.target_stage = target
        if self._animation_id is not None:
            self.root.after_cancel(self._animation_id)
            self._animation_id = None
        start_w, start_h = self.size
        if (start_w, start_h) == (target_w, target_h):
            self._finish_stage(target)
            self._draw()
            return
        started = time.monotonic()
        duration = self.ANIMATION_MS / 1000

        def frame() -> None:
            fraction = min(1.0, (time.monotonic() - started) / duration)
            # Symmetric easing keeps both directions gentle at their endpoints.
            eased = fraction * fraction * (3 - 2 * fraction)
            width = round(start_w + (target_w - start_w) * eased)
            height = round(start_h + (target_h - start_h) * eased)
            self.size = (width, height)
            self._position(width, height)
            self._draw()
            if fraction < 1:
                self._animation_id = self.root.after(self.FRAME_MS, frame)
            else:
                self._animation_id = None
                self._finish_stage(target)

        frame()

    def _finish_stage(self, target: str) -> None:
        self.stage = target
        self.on_stage_change(target)
        if target == self.HALF:
            if self._animation_id is not None:
                return
            if self._half_deadline is None:
                self._half_deadline = time.monotonic() + self.HALF_TIMEOUT_MS / 1000
            if self._half_timeout_id is not None:
                self.root.after_cancel(self._half_timeout_id)
            remaining_ms = max(1, round((self._half_deadline - time.monotonic()) * 1000))
            self._half_timeout_id = self.root.after(remaining_ms, self.show_minimal)
        else:
            self._cancel_half_timeout()

    def _cancel_half_timeout(self) -> None:
        if self._half_timeout_id is not None:
            self.root.after_cancel(self._half_timeout_id)
            self._half_timeout_id = None
        self._half_deadline = None

    def _rounded_panel(self, w: int, h: int) -> None:
        c = self.canvas
        # Keep the top edge square while the lower corners round out.
        progress = (h - self.SIZES[self.MINIMAL][1]) / (self.SIZES[self.FULL][1] - self.SIZES[self.MINIMAL][1])
        radius = min(h / 2, 4 + 43 * progress)
        c.create_rectangle(0, 0, w, h - radius, fill=BLACK, outline=BLACK)
        c.create_rectangle(radius, h - radius, w - radius, h, fill=BLACK, outline=BLACK)
        c.create_oval(0, h - 2 * radius, 2 * radius, h, fill=BLACK, outline=BLACK)
        c.create_oval(w - 2 * radius, h - 2 * radius, w, h, fill=BLACK, outline=BLACK)

    @staticmethod
    def _fade(color: str, amount: float) -> str:
        base = tuple(int(BLACK[i:i + 2], 16) for i in (1, 3, 5))
        target = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
        return "#" + "".join(f"{round(a + (b - a) * amount):02x}" for a, b in zip(base, target))

    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        self._progress_id = None
        w, h = self.size
        self._rounded_panel(w, h)
        if self.stage == self.HALF or self.target_stage == self.HALF:
            half_fade = max(0.0, min(1.0, (h - 8) / 28, (65 - h) / 29))
            if half_fade > 0:
                self._draw_half(c, w, half_fade)
        fade = max(0.0, min(1.0, (h - 65) / 115))
        if fade <= 0:
            return
        if self.page == "todo":
            self._draw_todo(c, fade)
            return
        color = lambda value: self._fade(value, fade)
        self._draw_album(c, fade)
        if self.battery_percent is not None:
            self._draw_battery(c, 173, 25, 9, fade)
        now = datetime.now().strftime("%a %d %b %Y  %H:%M")
        c.create_text(190 if self.battery_percent is not None else 166, 25,
                      text=now, anchor="w", fill=color(WHITE), font=("Segoe UI", 10))
        self._draw_sound_bars(c, (432, 438, 444, 450), 28, fade)

        # Dedicated Quit button in header
        is_quit_hover = self._hover_tag == "quit"
        quit_bg = "#3a181e" if is_quit_hover else "#181a20"
        quit_fg = "#ff6262" if is_quit_hover else "#8c8e96"
        quit_border = "#ff6262" if is_quit_hover else "#292c34"
        c.create_oval(474, 15, 498, 39, fill=color(quit_bg), outline=color(quit_border), width=1, tags=("quit",))
        c.create_text(486, 27, text="✕", fill=color(quit_fg), font=("Segoe UI", 10, "bold"), tags=("quit",))

        title = self._fit(self.state.title if self.state.available else "Nothing playing", self._title_font, 310)
        artist = self._fit(
            " · ".join(x for x in (self.state.artist, self.state.source) if x)
            if self.state.available else self.state.detail,
            self._artist_font, 310,
        )
        c.create_text(120, 75, text=title, anchor="w", fill=color(WHITE), font=self._title_font)
        c.create_text(120, 96, text=artist, anchor="w", fill=color(MUTED), font=self._artist_font)

        c.create_line(120, 133, 424, 133, fill=color("#48494d"), width=4, capstyle="round")
        end = self._progress_end()
        if end is not None:
            self._progress_id = c.create_line(120, 133, end, 133, fill=color(WHITE), width=4, capstyle="round")
        c.create_text(120, 147, text="NOW PLAYING" if self.state.playing else "PAUSED" if self.state.available else "NO PLAYER",
                      anchor="w", fill=color("#9ca0aa"), font=("Segoe UI", 8))

        can_control = self.state.available
        for tag, x, label, font_size in (
            ("previous", 216, "◀◀", 11),
            ("toggle", 258, "Ⅱ" if self.state.playing else "▶", 14),
            ("next", 300, "▶▶", 11),
        ):
            active = can_control and self._pressed_tag == tag
            hovered = can_control and self._hover_tag == tag
            if active:
                radius = 19 if tag == "toggle" else 16
                c.create_oval(x - radius, 168 - radius, x + radius, 168 + radius,
                              fill=color("#263847"), outline="", tags=(tag,))
            fg = CYAN if active or hovered else WHITE if can_control else "#62656c"
            c.create_text(x, 169 + (1 if active else 0), text=label, fill=color(fg),
                          font=("Segoe UI Symbol", font_size, "bold"), tags=(tag,))
        self._draw_page_navigation(c, fade)

    def _draw_half(self, c: tk.Canvas, width: int, fade: float) -> None:
        color = lambda value: self._fade(value, fade)
        if self.battery_percent is not None:
            self._draw_battery(c, 24, 18, 10, fade)
        clock_x = width / 2 if self.battery_percent is not None or not self.state.available else width / 2 - 12
        c.create_text(clock_x, 18, text=datetime.now().strftime("%H:%M"),
                      fill=color(WHITE), font=("Segoe UI", 10, "bold"))
        self._draw_sound_bars(c, (width - 32, width - 26, width - 20, width - 14), 18, fade)

    def _draw_battery(self, c: tk.Canvas, x: int, y: int, radius: int, fade: float) -> None:
        self._battery_photo = ImageTk.PhotoImage(self._battery_icon(radius, fade), master=self.root)
        c.create_image(x, y, image=self._battery_photo)

    def _battery_icon(self, radius: int, fade: float) -> Image.Image:
        """Draw the small battery mark at 6x resolution for smooth edges."""
        color = lambda value: self._fade(value, fade)
        scale = 6
        size = 2 * radius + 6
        center = size * scale / 2
        image = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        box = tuple(round(value) for value in
                    (center - radius * scale, center - radius * scale,
                     center + radius * scale, center + radius * scale))
        stroke = round(max(2.5, radius * 0.3) * scale)
        low = self.battery_percent is not None and self.battery_percent < 20
        alert = self.battery_charging or low
        if alert:
            draw.ellipse(box, outline=color("#22c988" if self.battery_charging else "#ff5360"), width=stroke)
        else:
            draw.ellipse(box, outline=color("#164e34"), width=stroke)
            if self.battery_percent:
                draw.arc(box, -90, -90 + 3.6 * self.battery_percent,
                         fill=color("#22c988"), width=stroke)
        if alert:
            bolt_scale = radius * scale / 10
            points = ((-1, -5), (2, -5), (0, -1), (3, -1),
                      (-2, 5), (-1, 1), (-3, 1))
            draw.polygon([(round(center + px * bolt_scale), round(center + py * bolt_scale))
                          for px, py in points], fill=color(WHITE))
        return image.resize((size, size), Image.Resampling.LANCZOS)

    def _icon(self, kind: str, color: str, size: int = 18) -> ImageTk.PhotoImage:
        key = (kind, color, size)
        if key in self._icon_cache:
            return self._icon_cache[key]
        scale = 6
        image = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        s = scale
        if kind == "check":
            draw.ellipse((s, s, (size - 1) * s, (size - 1) * s), fill=color)
            draw.line([(5*s, 9*s), (8*s, 12*s), (13*s, 6*s)], fill=WHITE,
                      width=round(2.2*s), joint="curve")
        elif kind == "emptycheck":
            draw.ellipse((s, s, (size - 1)*s, (size - 1)*s), outline=color, width=2*s)
        elif kind == "plus":
            draw.ellipse((s, s, (size - 1)*s, (size - 1)*s), fill=color)
            draw.line((size*s/2, 5*s, size*s/2, (size-5)*s), fill=WHITE, width=2*s)
            draw.line((5*s, size*s/2, (size-5)*s, size*s/2), fill=WHITE, width=2*s)
        elif kind == "edit":
            draw.line((4*s, 13*s, 12*s, 5*s), fill=color, width=2*s)
            draw.line((10*s, 4*s, 14*s, 8*s), fill=color, width=2*s)
            draw.line((3*s, 14*s, 7*s, 13*s), fill=color, width=2*s)
        elif kind in ("left", "right"):
            points = [(11, 5), (6, 9), (11, 13)] if kind == "left" else [(7, 5), (12, 9), (7, 13)]
            draw.line([(x*s, y*s) for x, y in points], fill=color, width=round(2.2*s), joint="curve")
        photo = ImageTk.PhotoImage(image.resize((size, size), Image.Resampling.LANCZOS), master=self.root)
        self._icon_cache[key] = photo
        return photo

    def _draw_page_navigation(self, c: tk.Canvas, fade: float) -> None:
        color = lambda value: self._fade(value, fade)
        for page, x in (("music", 252), ("todo", 265)):
            active = self.page == page
            c.create_oval(x - (5 if active else 3), 184, x + (5 if active else 3),
                          190 if not active else 191,
                          fill=color(WHITE if active else "#62656c"), outline="")
        x = 484 if self.page == "music" else 25
        y = 168 if self.page == "music" else 99
        c.create_oval(x - 14, y - 14, x + 14, y + 14, fill=color("#202329"), outline="")
        c.create_image(x, y, image=self._icon("right" if self.page == "music" else "left", color(WHITE)),
                       tags=("page_switch",))

    def _task_row_background(self, completed: bool, selected: bool, fade: float) -> ImageTk.PhotoImage:
        key = (f"row:{completed}:{selected}:{round(fade, 2)}", "", 0)
        if key in self._icon_cache:
            return self._icon_cache[key]
        scale = 4
        image = Image.new("RGBA", (216 * scale, 31 * scale), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        bg = self._fade("#10291d" if completed else "#1c2024", fade)
        border = self._fade(GREEN if selected else "#2b3431" if completed else "#343940", fade)
        draw.rounded_rectangle((0, 0, 216 * scale - 1, 31 * scale - 1), radius=9 * scale,
                               fill=bg, outline=border, width=scale)
        photo = ImageTk.PhotoImage(image.resize((216, 31), Image.Resampling.LANCZOS), master=self.root)
        self._icon_cache[key] = photo
        return photo

    def _draw_todo(self, c: tk.Canvas, fade: float) -> None:
        color = lambda value: self._fade(value, fade)
        c.create_text(18, 25, text="To", anchor="w", fill=color(WHITE), font=("Segoe UI", 12, "bold"))
        c.create_text(37, 25, text="Do", anchor="w", fill=color(GREEN), font=("Segoe UI", 12, "bold"))
        if self.battery_percent is not None:
            self._draw_battery(c, 177, 25, 9, fade)
        c.create_text(194 if self.battery_percent is not None else 166, 25,
                      text=datetime.now().strftime("%a %d %b %Y  %H:%M"), anchor="w",
                      fill=color(WHITE), font=("Segoe UI", 10))
        self._draw_sound_bars(c, (432, 438, 444, 450), 28, fade)
        is_quit_hover = self._hover_tag == "quit"
        c.create_oval(474, 15, 498, 39,
                      fill=color("#3a181e" if is_quit_hover else "#181a20"),
                      outline=color("#ff6262" if is_quit_hover else "#292c34"), width=1)
        c.create_text(486, 27, text="✕", fill=color("#ff6262" if is_quit_hover else "#8c8e96"),
                      font=("Segoe UI", 10, "bold"))

        c.create_text(82, 74, text=f"{self.tasks.completed_count}/{len(self.tasks.tasks)}",
                      fill=color(WHITE), font=("Segoe UI", 29, "bold"))
        c.create_text(82, 105, text="Completed", fill=color(MUTED), font=("Segoe UI", 10))
        for x0, x1, label, kind, active in (
            (18, 120, "Add Task", "plus", True),
            (128, 226, "Edit Task", "edit", self._selected_task_id is not None),
        ):
            c.create_oval(x0, 133, x0 + 31, 164, fill=color("#25282d" if active else "#15171a"), outline="")
            c.create_rectangle(x0 + 15, 133, x1 - 15, 164, fill=color("#25282d" if active else "#15171a"), outline="")
            c.create_oval(x1 - 31, 133, x1, 164, fill=color("#25282d" if active else "#15171a"), outline="")
            if active:
                c.create_image(x0 + 19, 148, image=self._icon(kind, color(GREEN if kind == "plus" else WHITE), 18))
            c.create_text(x0 + 34, 148, text=label, anchor="w",
                          fill=color(WHITE if active else "#73777d"), font=("Segoe UI", 9, "bold"))

        visible = self.tasks.tasks[self._task_scroll:self._task_scroll + 3]
        if not self.tasks.tasks:
            c.create_text(346, 89, text="No tasks yet", fill=color(WHITE), font=("Segoe UI", 12, "bold"))
            c.create_text(346, 111, text="Add one to get started", fill=color(MUTED), font=("Segoe UI", 9))
        for index, task in enumerate(visible):
            y0 = 48 + index * 36
            selected = task.id == self._selected_task_id
            c.create_image(238, y0, anchor="nw", image=self._task_row_background(task.completed, selected, fade))
            c.create_image(254, y0 + 15, image=self._icon("check" if task.completed else "emptycheck",
                                                            color(GREEN if task.completed else "#697078")))
            fitted = self._fit(task.title, self._task_font, 164)
            c.create_text(272, y0 + 15, text=fitted, anchor="w", fill=color("#aab5ad" if task.completed else WHITE),
                          font=self._task_font)
            if task.completed:
                c.create_line(272, y0 + 15, 272 + self._task_font.measure(fitted), y0 + 15,
                              fill=color("#aab5ad"), width=1)
        if len(self.tasks.tasks) > 3:
            c.create_line(463, 48, 463, 151, fill=color("#3b4045"), width=2)
            thumb = 103 * 3 / len(self.tasks.tasks)
            top = 48 + (103 - thumb) * self._task_scroll / (len(self.tasks.tasks) - 3)
            c.create_line(463, top, 463, top + thumb, fill=color(GREEN), width=3)
        self._draw_page_navigation(c, fade)

    def _draw_sound_bars(self, c: tk.Canvas, xs: tuple[int, ...], y: int, fade: float) -> None:
        if not self.state.available:
            return
        patterns = ((7, 13, 8, 5), (12, 6, 14, 8), (5, 11, 7, 13), (10, 8, 12, 5))
        heights = patterns[self._sound_phase] if self.state.playing else (5, 8, 6, 5)
        tone = WHITE if self.state.playing else MUTED
        for x, height in zip(xs, heights):
            c.create_line(x, y - height / 2, x, y + height / 2,
                          fill=self._fade(tone, fade), width=2)

    def _draw_album(self, c: tk.Canvas, fade: float) -> None:
        if self._artwork_photo is not None:
            c.create_image(26, 46, image=self._artwork_photo, anchor="nw")
            return
        # A restrained placeholder until artwork is supplied by the adapter.
        color = lambda value: self._fade(value, fade)
        c.create_rectangle(26, 46, 99, 119, fill=color("#b6d9ff"), outline="")
        for y in range(51, 117, 9):
            c.create_line(30, y, 96, y, fill=color("#e1ecff"), width=3)
        c.create_oval(38, 58, 88, 108, fill=color("#4f9eff"), outline=color("#eaf6ff"), width=2)
        c.create_line(50, 107, 77, 59, fill=color("#eaf6ff"), width=3)

    @staticmethod
    def _fit(text: str, font: tkfont.Font, width: int) -> str:
        if font.measure(text) <= width:
            return text
        while text and font.measure(text + "…") > width:
            text = text[:-1]
        return text + "…"

    def _enter(self, _event: tk.Event) -> None:
        self._cancel_collapse()
        if self.target_stage != self.FULL:
            self.expand()

    def _leave(self, _event: tk.Event) -> None:
        if self._hover_tag is not None:
            self._hover_tag = None
            if self.target_stage == self.FULL:
                self._draw()
        if self.target_stage == self.FULL and self._task_editor is None:
            self._schedule_collapse(240)

    def _control_at(self, x: int, y: int) -> str | None:
        if self.size[1] < 185:
            return None
        if (x - 486) ** 2 + (y - 27) ** 2 <= 14 ** 2:
            return "quit"
        if self.page == "todo":
            if (x - 25) ** 2 + (y - 99) ** 2 <= 16 ** 2:
                return "page_music"
            if 18 <= x <= 120 and 133 <= y <= 164:
                return "task_add"
            if 128 <= x <= 226 and 133 <= y <= 164 and self._selected_task_id is not None:
                return "task_edit"
            if 238 <= x <= 454 and 48 <= y < 151:
                index = self._task_scroll + (y - 48) // 36
                if index < len(self.tasks.tasks) and y < 48 + (index - self._task_scroll) * 36 + 31:
                    task_id = self.tasks.tasks[index].id
                    return f"task_toggle:{task_id}" if x < 270 else f"task_select:{task_id}"
            return None
        if (x - 484) ** 2 + (y - 168) ** 2 <= 16 ** 2:
            return "page_todo"
        for tag, center, radius in (("previous", 216, 18), ("toggle", 258, 22), ("next", 300, 18)):
            if (x - center) ** 2 + (y - 168) ** 2 <= radius ** 2:
                return tag
        return None

    def _clear_pending_toggle(self) -> None:
        had_pending = self._pending_toggle is not None
        self._pending_toggle = None
        if self._pending_id is not None:
            self.root.after_cancel(self._pending_id)
            self._pending_id = None
        if had_pending:
            self._draw()

    def _release_press(self) -> None:
        self._press_id = None
        self._pressed_tag = None
        self._draw()

    def _click(self, event: tk.Event) -> None:
        if self.target_stage != self.FULL or self.size[1] < 185:
            self.expand()
            return
        action = self._control_at(event.x, event.y)
        self.canvas.focus_set()
        if action == "quit":
            if self.on_quit:
                self.on_quit()
            return
        if action == "page_todo":
            self._switch_page("todo")
            return
        if action == "page_music":
            self._switch_page("music")
            return
        if action == "task_add":
            self._open_task_editor()
            return
        if action == "task_edit":
            self._open_task_editor(self._selected_task_id)
            return
        if action is not None and action.startswith("task_select:"):
            self._selected_task_id = action.split(":", 1)[1]
            self._draw()
            return
        if action is not None and action.startswith("task_toggle:"):
            task_id = action.split(":", 1)[1]
            try:
                self.tasks.toggle(task_id)
                self._selected_task_id = task_id
                self._draw()
            except OSError as exc:
                messagebox.showerror("Unable to save task", str(exc), parent=self.root)
            return
        if action is not None and self.state.available:
            self._pressed_tag = action
            if self._press_id is not None:
                self.root.after_cancel(self._press_id)
            self._press_id = self.root.after(170, self._release_press)
            if action == "toggle":
                self._clear_pending_toggle()
                self._pending_toggle = not self.state.playing
                self.state = replace(self.state, playing=self._pending_toggle)
                self._pending_id = self.root.after(2_500, self._clear_pending_toggle)
            self._draw()
            self.on_command(action)
            self._schedule_collapse(5_000)

    def _motion(self, _event: tk.Event) -> None:
        new_hover = self._control_at(_event.x, _event.y)
        if new_hover != self._hover_tag:
            self._hover_tag = new_hover
            if self.target_stage == self.FULL:
                self._draw()
        interactive = (
            self.target_stage != self.FULL
            or new_hover == "quit"
            or new_hover in ("page_todo", "page_music", "task_add", "task_edit")
            or (new_hover is not None and new_hover.startswith("task_"))
            or (self.state.available and new_hover in ("previous", "toggle", "next"))
        )
        self.canvas.configure(cursor="hand2" if interactive else "arrow")

    def _switch_page(self, page: str) -> None:
        if self.target_stage != self.FULL or self._task_editor is not None or page == self.page:
            return
        self.page = page
        self._hover_tag = None
        self._draw()

    def _key_page(self, page: str) -> None:
        self._switch_page(page)

    def _wheel(self, event: tk.Event) -> None:
        if self.target_stage != self.FULL or self.page != "todo" or self._task_editor is not None:
            return
        direction = -1 if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0 else 1
        if getattr(event, "num", None) not in (4, 5) and not getattr(event, "delta", 0):
            return
        limit = max(0, len(self.tasks.tasks) - 3)
        new_scroll = min(limit, max(0, self._task_scroll + direction))
        if new_scroll != self._task_scroll:
            self._task_scroll = new_scroll
            self._draw()

    def _close_task_editor(self) -> None:
        if self._task_editor is None:
            return
        editor = self._task_editor
        self._task_editor = None
        try:
            editor.grab_release()
        except tk.TclError:
            pass
        editor.destroy()
        if not self._closed:
            self.canvas.focus_set()
            self._schedule_collapse(5_000)

    def _open_task_editor(self, task_id: str | None = None) -> None:
        if self._task_editor is not None:
            return
        task = next((item for item in self.tasks.tasks if item.id == task_id), None)
        if task_id is not None and task is None:
            return
        self._cancel_collapse()
        editor = tk.Toplevel(self.root)
        self._task_editor = editor
        editor.overrideredirect(True)
        editor.attributes("-topmost", True)
        editor.configure(bg="#32373b")
        editor.geometry(f"310x126+{self.root.winfo_rootx() + 103}+{self.root.winfo_rooty() + self.size[1] + 8}")
        panel = tk.Frame(editor, bg="#101318", padx=13, pady=10)
        panel.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(panel, text="Edit Task" if task else "Add Task", bg="#101318", fg=WHITE,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")
        title = tk.StringVar(value=task.title if task else "")
        entry = tk.Entry(panel, textvariable=title, bg="#242930", fg=WHITE, insertbackground=WHITE,
                         relief="flat", font=("Segoe UI", 10))
        entry.pack(fill="x", pady=(7, 5), ipady=4)
        error = tk.Label(panel, text="", bg="#101318", fg="#ff6973", font=("Segoe UI", 8))
        error.pack(anchor="w")
        buttons = tk.Frame(panel, bg="#101318")
        buttons.pack(fill="x")

        def save() -> None:
            try:
                if task is None:
                    added = self.tasks.add(title.get())
                    self._selected_task_id = added.id
                    self._task_scroll = max(0, len(self.tasks.tasks) - 3)
                else:
                    self.tasks.edit(task.id, title.get())
                self._close_task_editor()
                self._draw()
            except (OSError, ValueError) as exc:
                error.configure(text=str(exc))

        def delete() -> None:
            if task is None or not messagebox.askyesno("Delete task", f"Delete ‘{task.title}’?", parent=editor):
                return
            try:
                self.tasks.delete(task.id)
                self._selected_task_id = None
                self._task_scroll = min(self._task_scroll, max(0, len(self.tasks.tasks) - 3))
                self._close_task_editor()
                self._draw()
            except OSError as exc:
                error.configure(text=str(exc))

        if task is not None:
            tk.Button(buttons, text="Delete", command=delete, bg="#311c22", fg="#ff858d",
                      relief="flat", font=("Segoe UI", 9)).pack(side="left")
        tk.Button(buttons, text="Cancel", command=self._close_task_editor, bg="#252930", fg=WHITE,
                  relief="flat", font=("Segoe UI", 9)).pack(side="right", padx=(6, 0))
        tk.Button(buttons, text="Save", command=save, bg=GREEN, fg=BLACK,
                  relief="flat", font=("Segoe UI", 9, "bold")).pack(side="right")
        editor.bind("<Escape>", lambda _event: self._close_task_editor())
        entry.bind("<Return>", lambda _event: save())
        editor.protocol("WM_DELETE_WINDOW", self._close_task_editor)
        editor.update_idletasks()
        editor.grab_set()
        entry.focus_set()

    def _cancel_collapse(self) -> None:
        if self._collapse_id is not None:
            self.root.after_cancel(self._collapse_id)
            self._collapse_id = None

    def _schedule_collapse(self, delay_ms: int) -> None:
        if self._task_editor is not None:
            return
        self._cancel_collapse()

        def maybe_collapse() -> None:
            self._collapse_id = None
            x = self.root.winfo_pointerx() - self.root.winfo_rootx()
            y = self.root.winfo_pointery() - self.root.winfo_rooty()
            w, h = self.size
            if self.target_stage == self.FULL and self._task_editor is None and not (0 <= x < w and 0 <= y < h):
                self.show_half()

        self._collapse_id = self.root.after(delay_ms, maybe_collapse)

    def _clock_tick(self) -> None:
        if self._closed:
            return
        current = datetime.now().strftime("%Y%m%d%H%M")
        if self.target_stage in (self.HALF, self.FULL) and current != self._last_clock:
            self._last_clock = current
            self._draw()
        self._clock_id = self.root.after(15_000, self._clock_tick)
