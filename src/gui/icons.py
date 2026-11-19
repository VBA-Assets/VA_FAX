# -*- coding: utf-8 -*-
"""リボンのボタンのアイコン（VA mints・VA Case・VA Documentと同じ形）。

アイコンはMicrosoftのFluent UI System Icons（MIT License。icons/LICENSE）で、
2色の形（塗りつぶし版を薄い青で下に敷き、その上に線画を濃い色で重ねたもの）とする。
build/make_ribbon_icons.pyでSVGから大きめのPNG（icons/<名前>.png・<名前>_disabled.png・
<名前>_large.png）にしたものを同梱し、起動したときにPillowで画面の拡大率に合った大きさへ
縮める（拡大してもぼやけない）。

画像はすべて同梱ファイルであり、外部通信は行わない（N-01）。
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from typing import Optional


def _icon_dir() -> Path:
    """アイコンの置き場。

    PyInstallerのonefile実行時は展開先（sys._MEIPASS）のgui/icons（build/va_fax.specのdatasで
    同梱）、開発時はこのファイルの隣のicons。
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        return base / "gui" / "icons"
    return Path(__file__).resolve().parent / "icons"


ICON_DIR = _icon_dir()
ICON_SIZE = 16   # 小さいアイコンの大きさ（拡大率100%のとき。ピクセル）
LARGE_ICON = 32  # 大きいボタンのアイコンの大きさ

# ボタンの文字（改行・「…」・「...」を除く）→ アイコンの名前（icons/<名前>.png）
BUTTON_ICONS = {
    # 「ファイル」の組（F-32）
    "新規作成": "document_text",
    "開く": "folder_open",
    "保存": "save",
    "名前を付けて保存": "save",
    # 「ページ」の組（F-33。回転・元に戻す・やり直すは、VA mintsと同じ自作のアイコン）
    "追加": "document_add",
    "削除": "delete",
    "回転": "arrow_rotate",
    "元に戻す": "arrow_undo",
    "やり直す": "arrow_redo",
    # 「送付書」の組（F-36。Ver210）。文書に送付書があるときは、文字が「編集」になる
    "作成": "document_header",
    "編集": "document_header",
}


def icon_name(text: str) -> Optional[str]:
    """ボタンの文字に対応するアイコンの名前（対応がなければNone）。"""
    key = str(text).replace("\n", "").replace("…", "").replace("...", "")
    return BUTTON_ICONS.get(key)


class IconSet:
    """アイコンの画像を作って保持する（Tkの画像は参照がなくなると破棄されるため）。"""

    def __init__(self, root: tk.Misc) -> None:
        self.root = root
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)  # 画面の拡大率
        self._cache: dict[tuple, object] = {}

    def image(self, name: str, disabled: bool = False, large: bool = False):
        """アイコンの画像（ファイルがなければNone）。

        largeのときは大きいボタンの大きさとし、色付きのアイコン（<名前>_large.png）があれば
        それを使う（押せないときは、灰色の2色のアイコンを大きくする）。
        """
        size = round((LARGE_ICON if large else ICON_SIZE) * self.scale)
        key = (name, disabled, large, size)
        if key not in self._cache:
            candidates = [f"{name}_large.png"] if large and not disabled else []
            candidates.append(f"{name}_disabled.png" if disabled else f"{name}.png")
            path = next((ICON_DIR / c for c in candidates if (ICON_DIR / c).is_file()), None)
            if path is None:
                return None
            from PIL import Image, ImageTk

            with Image.open(path) as img:
                resized = img.convert("RGBA").resize((size, size), Image.LANCZOS)
            self._cache[key] = ImageTk.PhotoImage(resized, master=self.root)
        return self._cache[key]
