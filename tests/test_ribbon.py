# -*- coding: utf-8 -*-
"""リボン（F-15。VA mints・VA Caseと同じ形）とボタンの説明書きの試験。

Tkのルートウィンドウは作成後ただちにwithdraw()して画面に出さない（自動テストで
GUIを実際に起動しない）。画面のない環境ではスキップする。
"""
from __future__ import annotations

import pytest

pytest.importorskip("tkinter")

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

from gui import theme  # noqa: E402
from gui.icons import BUTTON_ICONS, ICON_DIR, IconSet, icon_name  # noqa: E402
from gui.ribbon import Ribbon  # noqa: E402
from gui.tooltip import Tooltip, _by_sentence  # noqa: E402


@pytest.fixture(scope="module")
def root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("画面がない環境")
    r.withdraw()
    theme.apply_flat_theme(r)
    yield r
    r.destroy()


def test_every_button_icon_has_images():
    """リボンの各ボタンのアイコン（通常・押せないとき）が同梱されている。"""
    for name in set(BUTTON_ICONS.values()):
        assert (ICON_DIR / f"{name}.png").is_file(), name
        assert (ICON_DIR / f"{name}_disabled.png").is_file(), name
    # Fluent UI System Icons（MIT License）のライセンス文も同梱する。
    assert (ICON_DIR / "LICENSE").is_file()


def test_icon_name_ignores_ellipsis():
    """ボタンの文字から引く（末尾の「...」や改行は無視する）。"""
    assert icon_name("入力元...") == "document_add"
    assert icon_name("変換して出力") == "image"
    assert icon_name("出力先...") == "save"
    assert icon_name("出力フォルダを開く") == "folder_open"
    assert icon_name("存在しないボタン") is None


def test_ribbon_groups_and_buttons(root):
    """組ごとに大小のボタンを置ける。大きいボタンはアイコンの下に文字を置く。"""
    icons = IconSet(root)
    ribbon = Ribbon(root, icons)
    ribbon.pack(fill="x")
    g_in = ribbon.group("入力")
    big = g_in.large("入力元...", lambda: None, style=theme.RIBBON_ACCENT_BUTTON_STYLE)
    g_out = ribbon.group("出力")
    small = g_out.small("出力フォルダを開く", lambda: None)
    root.update()

    assert str(big.cget("compound")) == "top"
    assert str(small.cget("compound")) == "left"
    assert big.cget("image") and small.cget("image")
    # ボタンはTabで止まらない（入力ファイル一覧のキー操作を妨げないため）。
    assert str(big.cget("takefocus")) in ("0", "False")
    small.configure(state="disabled")  # 押せないときは灰色のアイコンになる
    assert "disabled" in str(small.cget("image"))
    assert [g.title for g in ribbon.groups] == ["入力", "出力"]

    # 幅が足りないときは、入りきらない組を次の段に送る（update()を挟むと、
    # 画面に出していないウィンドウでは幅1のConfigureで1段に戻るため、直後に確かめる）。
    ribbon.reflow(60)
    assert g_in.winfo_manager() == "grid"
    assert g_out.grid_info()["in"] != g_in.grid_info()["in"]
    ribbon.destroy()


def test_tooltips(root):
    """マウスを重ねたときの説明書き。説明は文字列か、いまの説明を返す関数。"""
    button = ttk.Button(root, text="変換して出力")
    button.pack()
    state = {"ready": False}
    tip = Tooltip(button, lambda: "変換します" if state["ready"] else "入力ファイルを選んでください")
    assert tip.current_text() == "入力ファイルを選んでください"
    state["ready"] = True
    assert tip.current_text() == "変換します"  # 押せる・押せないで説明が変わる
    assert _by_sentence("一つ。二つ。") == "一つ。\n二つ。"  # 文ごとに改行する
    assert Tooltip(ttk.Button(root), "").current_text() == ""  # 空の説明は出さない
    button.destroy()


def test_ribbon_styles(root):
    """リボンのボタンは、ふだんは枠も地もなく、重ねたときだけ淡い青。

    主要な操作（入力元・変換して出力）は、いつもアクセント色の枠を出す。
    """
    style = ttk.Style(root)
    assert style.lookup(theme.RIBBON_BUTTON_STYLE, "background") == theme.COLOR_BG
    assert style.lookup(theme.RIBBON_BUTTON_STYLE, "bordercolor") == theme.COLOR_BG
    assert style.lookup(theme.RIBBON_BUTTON_STYLE, "background", ["active"]) == theme.COLOR_HOVER_BG
    assert style.lookup(
        theme.RIBBON_BUTTON_STYLE, "bordercolor", ["active"]) == theme.COLOR_RIBBON_HOVER_BORDER
    assert style.lookup(
        theme.RIBBON_ACCENT_BUTTON_STYLE, "bordercolor", ["!disabled"]) == theme.COLOR_ACCENT
