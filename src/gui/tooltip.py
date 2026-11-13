# -*- coding: utf-8 -*-
"""ボタンの説明書き（マウスを重ねると、少し間をおいて出る。F-15）。

VA mints・VA Case・VA Documentのtooltip.pyと同じ仕組み・同じ見た目とする（Officeと同じく、
白地に細い灰色の枠）。ボタンから離れる・押すと消える。説明は文字列のほか、「いまの説明を返す関数」
も渡せる（押せる・押せないで説明が変わるボタン用）。

説明はボタンの下に出す。aboveを指定した場合は上に出す（画面の下端近くのボタン。下に出すと
タスクバーに隠れることがある）。
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable, Union

from gui import theme

DELAY_MS = 500
# 説明の折り返し幅（ピクセル。拡大率100%のとき）。Tkの折り返しは行頭の禁則を守らないため、
# 文ごとに改行してから渡す。
WRAP = 420

TooltipText = Union[str, Callable[[], str]]


def _by_sentence(text: str) -> str:
    """文ごとに改行する（「。」の後。すでに改行のある所と、文の終わりは除く）。"""
    return text.replace("。", "。\n").replace("。\n\n", "。\n").rstrip("\n")


class Tooltip:
    """ウィジェットにマウスを重ねたときの説明書き。"""

    def __init__(self, widget: tk.Misc, text: TooltipText, above: bool = False) -> None:
        self.widget = widget
        self.text = text
        self.above = above
        self._job: str | None = None
        self._win: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def current_text(self) -> str:
        """いま出すべき説明。"""
        return self.text() if callable(self.text) else self.text

    def _schedule(self, _event=None) -> None:
        self.hide()
        self._job = self.widget.after(DELAY_MS, self.show)

    def show(self) -> None:
        self._job = None
        text = self.current_text()
        # 説明が空のときは出さない（表示されていないウィジェットも同様）。
        if self._win is not None or not text or not self.widget.winfo_viewable():
            return
        x = self.widget.winfo_rootx() + 4
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._win = win = tk.Toplevel(self.widget)
        win.withdraw()  # 位置を決めてから出す（出してからでは位置の指定が効かない）
        win.wm_overrideredirect(True)
        win.configure(background=theme.COLOR_BORDER)  # 1ピクセルの灰色の枠
        tk.Label(
            win, text=_by_sentence(text), justify="left",
            wraplength=theme.px(self.widget, WRAP),
            background=theme.COLOR_SURFACE, foreground=theme.COLOR_TEXT, padx=8, pady=5,
        ).pack(padx=1, pady=1)
        win.update_idletasks()
        # 画面の右端・下端からはみ出さないようにする。
        screen_w, screen_h = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        x = min(x, screen_w - w - 4)
        if self.above or y + h > screen_h:
            y = self.widget.winfo_rooty() - h - 4
        win.wm_geometry(f"{w}x{h}+{x}+{y}")
        win.deiconify()
        win.lift()

    def hide(self, _event=None) -> None:
        if self._job is not None:
            self.widget.after_cancel(self._job)
            self._job = None
        if self._win is not None:
            self._win.destroy()
            self._win = None
