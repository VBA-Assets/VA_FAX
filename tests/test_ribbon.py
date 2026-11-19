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
def root(tk_root):
    """conftest の1つの Tk を使う（試験のファイルごとに Tk を作らない。標準 02「テスト」）。"""
    yield tk_root
    for child in list(tk_root.winfo_children()):
        child.destroy()


def test_every_button_icon_has_images():
    """リボンの各ボタンのアイコン（通常・押せないとき）が同梱されている。"""
    for name in set(BUTTON_ICONS.values()):
        assert (ICON_DIR / f"{name}.png").is_file(), name
        assert (ICON_DIR / f"{name}_disabled.png").is_file(), name
    # Fluent UI System Icons（MIT License）のライセンス文も同梱する。
    assert (ICON_DIR / "LICENSE").is_file()


def test_icon_name_ignores_ellipsis():
    """ボタンの文字から引く（末尾の「…」「...」や改行は無視する）。"""
    assert icon_name("開く...") == "folder_open"
    assert icon_name("保存") == "save"
    assert icon_name("名前を付けて保存…") == "save"
    assert icon_name("追加") == "document_add"
    assert icon_name("回転") == "arrow_rotate"
    assert icon_name("存在しないボタン") is None


def test_ribbon_groups_and_buttons(root):
    """組ごとに大小のボタンを置ける。大きいボタンはアイコンの下に文字を置く。"""
    icons = IconSet(root)
    ribbon = Ribbon(root, icons)
    ribbon.pack(fill="x")
    g_in = ribbon.group("ファイル")
    big = g_in.large("開く", lambda: None, style=theme.RIBBON_ACCENT_BUTTON_STYLE)
    g_out = ribbon.group("ページ")
    small = g_out.small("削除", lambda: None)
    root.update()

    assert str(big.cget("compound")) == "top"
    assert str(small.cget("compound")) == "left"
    assert big.cget("image") and small.cget("image")
    # ボタンはTabで止まらない（ページの一覧のキー操作を妨げないため）。
    assert str(big.cget("takefocus")) in ("0", "False")
    small.configure(state="disabled")  # 押せないときは灰色のアイコンになる
    assert "disabled" in str(small.cget("image"))
    assert [g.title for g in ribbon.groups] == ["ファイル", "ページ"]

    # 幅が足りないときは、入りきらない組を次の段に送る（update()を挟むと、
    # 画面に出していないウィンドウでは幅1のConfigureで1段に戻るため、直後に確かめる）。
    ribbon.reflow(60)
    assert g_in.winfo_manager() == "grid"
    assert g_out.grid_info()["in"] != g_in.grid_info()["in"]
    ribbon.destroy()


def test_tooltips(root):
    """マウスを重ねたときの説明書き。説明は文字列か、いまの説明を返す関数。"""
    button = ttk.Button(root, text="保存")
    button.pack()
    state = {"ready": False}
    tip = Tooltip(button, lambda: "保存します" if state["ready"] else "ファイルを開いてください")
    assert tip.current_text() == "ファイルを開いてください"
    state["ready"] = True
    assert tip.current_text() == "保存します"  # 押せる・押せないで説明が変わる
    assert _by_sentence("一つ。二つ。") == "一つ。\n二つ。"  # 文ごとに改行する
    assert Tooltip(ttk.Button(root), "").current_text() == ""  # 空の説明は出さない
    button.destroy()


def test_ribbon_styles(root):
    """リボンのボタンは、ふだんは枠も地もなく、重ねたときだけ淡い青。

    主要な操作（開く・保存）は、いつもアクセント色の枠を出す。
    """
    style = ttk.Style(root)
    assert style.lookup(theme.RIBBON_BUTTON_STYLE, "background") == theme.WINDOW_BG
    assert style.lookup(theme.RIBBON_BUTTON_STYLE, "bordercolor") == theme.WINDOW_BG
    assert style.lookup(theme.RIBBON_BUTTON_STYLE, "background", ["active"]) == theme.HOVER_BG
    assert style.lookup(
        theme.RIBBON_BUTTON_STYLE, "bordercolor", ["active"]) == theme.RIBBON_HOVER_BORDER
    assert style.lookup(
        theme.RIBBON_ACCENT_BUTTON_STYLE, "bordercolor", ["!disabled"]) == theme.ACCENT
