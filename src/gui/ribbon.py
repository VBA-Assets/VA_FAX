# -*- coding: utf-8 -*-
"""Wordのリボンと同じ形のボタンの並び（VA mints・VA Case・VA Documentのribbon.pyと同じ形。F-15）。

・ボタンを組（ファイル・ページ）に分け、組の名前をボタンの下に小さく表示する。組と組の間は縦線で区切る。
・主要なボタンは大きいアイコンの下に文字（large）、ほかは小さいアイコンの右に文字を縦に3つまで積む（small）。
・ボタンは、ふだんは枠も地もなく、マウスを重ねたときだけ淡い青の地と枠を出す（Wordのリボンと同じ。
  スタイルはtheme.pyのRibbon*）。主要な操作（開く・保存）だけは、いつも枠を出す
  （theme.pyのRibbonAccent）。
・組の名前は、どの組も下端にそろえる。
・ボタンはTabで止まらない（ttkのボタンは押すと入力先を取るため、takefocusを切る。入力ファイル一覧の
  キー操作が、ボタンを押した後も効くように）。
・幅に組が入りきらないときは、入りきらない組を次の段に送る（Wordのように組を畳んで隠すことはしない）。
・マウスを重ねると、少し間をおいて説明書きを出す（tip。tooltip.py）。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional

from gui import theme
from gui.icons import IconSet, icon_name
from gui.tooltip import Tooltip, TooltipText

ROWS = 3  # 小さいボタンを縦に積む数


class RibbonGroup(ttk.Frame):
    """リボンの1つの組。ボタンは左から列ごとに置く。"""

    def __init__(self, master: tk.Misc, title: str, icons: Optional[IconSet]) -> None:
        super().__init__(master, style=theme.RIBBON_FRAME_STYLE)
        self.title = title
        self.icons = icons
        self.body = ttk.Frame(self, style=theme.RIBBON_FRAME_STYLE)
        self.body.grid(row=0, column=0, sticky="nsew", padx=2, pady=(2, 0))
        ttk.Label(
            self, text=title, style=theme.RIBBON_GROUP_LABEL_STYLE, anchor="center"
        ).grid(row=1, column=0, sticky="sew")
        ttk.Separator(self, orient="vertical").grid(
            row=0, column=1, rowspan=2, sticky="ns", pady=(4, 4))
        self.columnconfigure(0, weight=1)
        # 組の高さがそろったとき、余りはボタンの側に回し、組の名前を下端にそろえる。
        self.rowconfigure(0, weight=1)
        self._column = 0
        self._stack = 0  # 今の列に積んだ小さいボタンの数

    def _images(self, text: str, icon: Optional[str], large: bool) -> dict:
        name = icon or icon_name(text)
        if self.icons is None or not name:
            return {}
        normal = self.icons.image(name, large=large)
        disabled = self.icons.image(name, disabled=True, large=large)
        if normal is None:
            return {}
        return {
            "image": (normal, "disabled", disabled or normal),
            "compound": "top" if large else "left",
        }

    def large(self, text: str, command, icon: Optional[str] = None,
              style: str = theme.RIBBON_LARGE_BUTTON_STYLE,
              tip: Optional[TooltipText] = None, **kwargs) -> ttk.Button:
        """大きいボタン（アイコンの下に文字）。1列を使う。tipはマウスを重ねたときの説明書き。"""
        button = ttk.Button(self.body, text=text, command=command, style=style, takefocus=False,
                            **self._images(text, icon, True), **kwargs)
        if tip:
            button.tooltip = Tooltip(button, tip)  # type: ignore[attr-defined]
        self._next_column()
        button.grid(row=0, column=self._column, rowspan=ROWS, sticky="ns", padx=1, pady=0)
        self._column += 1
        return button

    def small(self, text: str, command, icon: Optional[str] = None,
              tip: Optional[TooltipText] = None, **kwargs) -> ttk.Button:
        """小さいボタン（アイコンの右に文字）。今の列に縦に積み、ROWS個で次の列へ移る。"""
        button = ttk.Button(self.body, text=text, command=command,
                            style=theme.RIBBON_BUTTON_STYLE, takefocus=False,
                            **self._images(text, icon, False), **kwargs)
        if tip:
            button.tooltip = Tooltip(button, tip)  # type: ignore[attr-defined]
        if self._stack >= ROWS:
            self._next_column()
        button.grid(row=self._stack, column=self._column, sticky="w", padx=1, pady=0)
        self._stack += 1
        return button

    def dropdown(self, text: str, items: list[tuple[str, object]], icon: Optional[str] = None,
                 tip: Optional[TooltipText] = None, **kwargs) -> ttk.Menubutton:
        """小さいボタンの形の、押すとメニューが開くボタン（Wordの［回転］と同じ）。

        itemsは（メニューの文字, 実行する処理）の並び。置き方はsmallと同じ。
        """
        button = ttk.Menubutton(self.body, text=text, style=theme.RIBBON_MENUBUTTON_STYLE,
                                takefocus=False, direction="below",
                                **self._images(text, icon, False), **kwargs)
        menu = tk.Menu(button, tearoff=False)
        for label, command in items:
            menu.add_command(label=label, command=command)
        button.configure(menu=menu)
        button.menu = menu  # type: ignore[attr-defined]
        if tip:
            button.tooltip = Tooltip(button, tip)  # type: ignore[attr-defined]
        if self._stack >= ROWS:
            self._next_column()
        button.grid(row=self._stack, column=self._column, sticky="w", padx=1, pady=0)
        self._stack += 1
        return button

    def new_column(self) -> None:
        """次の小さいボタンを、新しい列の上から置く。"""
        self._next_column()

    def block(self, widget: tk.Misc) -> tk.Misc:
        """部品（入力欄など）を、新しい列の上から置く。"""
        self._next_column()
        widget.grid(in_=self.body, row=0, column=self._column, rowspan=ROWS,
                    sticky="nw", padx=(1, 6), pady=(1, 0))
        self._column += 1
        return widget

    def _next_column(self) -> None:
        if self._stack:
            self._column += 1
            self._stack = 0


class Ribbon(ttk.Frame):
    """組を左から並べる。幅に入りきらない組は次の段に送る。"""

    def __init__(self, master: tk.Misc, icons: Optional[IconSet]) -> None:
        super().__init__(master, style=theme.RIBBON_FRAME_STYLE)
        self.icons = icons
        self.groups: list[RibbonGroup] = []
        self._lines: list[ttk.Frame] = []  # 段ごとの枠
        self._layout: Optional[list[list[int]]] = None
        self.bind("<Configure>", lambda e: self.reflow(e.width))

    def group(self, title: str) -> RibbonGroup:
        group = RibbonGroup(self, title, self.icons)
        self.groups.append(group)
        self.reflow(self.winfo_width())
        return group

    def reflow(self, width: int) -> None:
        """幅widthに入るように、組を段に分けて置く。

        同じ並びになる場合は置き直さない（Configureイベントが続けて起きないように）。
        """
        if width <= 1:
            width = 10 ** 6
        rows: list[list[RibbonGroup]] = [[]]
        used = 0
        for group in self.groups:
            w = group.winfo_reqwidth()
            if rows[-1] and used + w > width:
                rows.append([])
                used = 0
            rows[-1].append(group)
            used += w
        layout = [[id(g) for g in row] for row in rows]
        if layout == self._layout:
            return
        self._layout = layout
        # 段ごとに枠を分け、組を左から詰めて置く（1つの表に段と列で置くと、2段目の組が
        # 1段目の同じ列の幅に合わせて広げられてしまうため）。
        while len(self._lines) < len(rows):
            self._lines.append(ttk.Frame(self, style=theme.RIBBON_FRAME_STYLE))
        for r, line in enumerate(self._lines):
            if r < len(rows):
                line.grid(row=r, column=0, sticky="w")
            else:
                line.grid_remove()
        for r, row in enumerate(rows):
            for c, group in enumerate(row):
                group.grid(in_=self._lines[r], row=0, column=c, sticky="nsw")
                group.lift(self._lines[r])  # 組は段の枠より先に作るため、枠の上に出す
