# -*- coding: utf-8 -*-
"""サムネイルでのページの選択・並べ替え・取り除き（F-33）のテスト。

Tkのルートウィンドウは作成後ただちにwithdraw()して画面に出さない（自動テストで
GUIを実際に起動しない）。画面のない環境ではスキップする。マウスの操作は、
プレビュー欄の処理（押す・動かす・離す）を直接呼んで確かめる。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("tkinter")

import tkinter as tk  # noqa: E402

from core.convert import ConvertOptions  # noqa: E402
from gui import theme  # noqa: E402
from gui.preview import PreviewPane, click_selection, drop_index_for_y  # noqa: E402

SHIFT, CTRL = 0x0001, 0x0004


# ---------------------------------------------------------------- 計算の部分

def test_click_selection_plain_shift_ctrl():
    assert click_selection({0, 1}, 0, 3, False, False) == ({3}, 3)
    assert click_selection({1}, 1, 3, True, False) == ({1, 2, 3}, 1)
    assert click_selection({3}, 3, 1, True, False) == ({1, 2, 3}, 3)
    assert click_selection(set(), None, 2, True, False) == ({2}, 2)
    assert click_selection({0, 2}, 0, 4, False, True) == ({0, 2, 4}, 4)
    assert click_selection({0, 2}, 0, 2, False, True) == ({0}, 2)


def test_drop_index_for_y():
    bounds = [(0, 100), (112, 212), (224, 324)]
    assert drop_index_for_y(bounds, -5) == 0
    assert drop_index_for_y(bounds, 49) == 0
    assert drop_index_for_y(bounds, 51) == 1
    assert drop_index_for_y(bounds, 200) == 2
    assert drop_index_for_y(bounds, 300) == 3
    assert drop_index_for_y([], 10) == 0


# ---------------------------------------------------------------- プレビュー欄

@pytest.fixture(scope="module")
def root(tk_root):
    """conftest の1つの Tk を使う（試験のファイルごとに Tk を作らない。標準 02「テスト」）。"""
    yield tk_root
    for child in list(tk_root.winfo_children()):
        child.destroy()


@pytest.fixture
def pane(root, sample_bytes, motfax):
    p = PreviewPane(root)
    p.pack()
    p.set_document(sample_bytes("sample06_mixed_sizes.pdf"), motfax, ConvertOptions(mode="binarize"))
    p.set_editable(True)
    p.events = []
    p.on_delete = lambda pages: p.events.append(("delete", pages))
    p.on_move = lambda pages, drop: p.events.append(("move", pages, drop))
    root.update()
    yield p
    p.destroy()


def _event(pane, state=0, dx=0, dy=0, widget=None):
    return SimpleNamespace(state=state, x_root=pane._canvas.winfo_rootx() + 20 + dx,
                           y_root=pane._canvas.winfo_rooty() + 20 + dy,
                           widget=widget or pane._thumbnail_labels[0])


def _click(pane, index, state=0):
    pane._on_thumb_press(index, _event(pane, state))
    pane._on_thumb_release()


def test_document_opens_with_first_page_selected(pane):
    assert pane._page_count == 4
    assert pane.selected_pages() == [0]


def test_click_shift_ctrl_select_pages(pane):
    _click(pane, 2)
    assert pane.selected_pages() == [2]
    _click(pane, 0, SHIFT)
    assert pane.selected_pages() == [0, 1, 2]
    _click(pane, 1, CTRL)
    assert pane.selected_pages() == [0, 2]
    assert pane._thumbnail_slots[0].cget("style") == theme.THUMBNAIL_FRAME_SELECTED_STYLE
    assert pane._thumbnail_slots[1].cget("style") == theme.THUMBNAIL_FRAME_STYLE
    pane.select_all()
    assert pane.selected_pages() == [0, 1, 2, 3]


def test_click_inside_multi_selection_collapses_only_on_release(pane):
    _click(pane, 0)
    _click(pane, 2, SHIFT)
    pane._on_thumb_press(1, _event(pane))
    assert pane.selected_pages() == [0, 1, 2]      # 押しただけでは保つ（そのまま動かせるように）
    pane._on_thumb_release()
    assert pane.selected_pages() == [1]            # 動かさずに離したら、そのページだけ


def test_keys_extend_selection_and_delete(pane):
    _click(pane, 1)
    pane._extend_selection(1)
    assert pane.selected_pages() == [1, 2]
    pane._extend_selection(-1)
    assert pane.selected_pages() == [1]
    pane._on_delete_key()
    assert pane.events == [("delete", [1])]


def test_delete_is_ignored_when_not_editable(pane):
    pane.set_editable(False)
    _click(pane, 1)
    pane._on_delete_key()
    assert pane.events == []


def test_right_click_selects_page_and_asks_for_menu(pane):
    """右クリック：選択していないページの上なら、そのページだけを選ぶ。選択中のページの上や、
    一覧の余白なら、選択を保つ。いずれもメニューを求める。"""
    asked = []
    pane.on_context_menu = lambda x, y: asked.append((x, y))
    _click(pane, 0)
    _click(pane, 1, SHIFT)
    pane._on_thumb_context(1, SimpleNamespace(x_root=5, y_root=6))
    assert pane.selected_pages() == [0, 1]
    pane._on_thumb_context(3, SimpleNamespace(x_root=7, y_root=8))
    assert pane.selected_pages() == [3]
    pane._on_thumb_context(None, SimpleNamespace(x_root=9, y_root=10))
    assert pane.selected_pages() == [3]
    assert asked == [(5, 6), (7, 8), (9, 10)]


def test_small_movement_is_a_click_not_a_drag(pane):
    pane._on_thumb_press(1, _event(pane))
    pane._on_thumb_motion(_event(pane, dx=2, dy=2))
    assert not pane._dragging
    pane._on_thumb_release()
    assert pane.events == []


def test_drag_inside_moves_selected_pages(pane, root):
    _click(pane, 0)
    _click(pane, 1, SHIFT)
    # 画面に出していないウィンドウでは欄の大きさが取れないので、大きさとサムネイルの位置を与える
    pane._canvas.winfo_width = lambda: 110
    pane._canvas.winfo_height = lambda: 460
    pane._canvas.canvasy = lambda y: y
    pane._slot_bounds = lambda: [(0, 100), (112, 212), (224, 324), (336, 436)]
    pane._on_thumb_press(0, _event(pane))
    pane._on_thumb_motion(_event(pane, dy=260))    # 欄の中のy=280：3ページ目の下半分 → その後ろ
    assert pane._dragging and pane._drag_drop_index == 3
    pane._on_thumb_motion(_event(pane, dy=420))    # y=440：最後のサムネイルより下 → 末尾の後ろ
    root.update()
    assert pane._drag_drop_index == 4
    assert pane._indicator is not None and pane._indicator.winfo_manager() == "place"
    pane._on_thumb_release()
    assert pane.events == [("move", [0, 1], 4)]
    assert not pane._dragging and pane._indicator.winfo_manager() == ""


def test_drag_outside_removes_selected_pages(pane):
    _click(pane, 2)
    pane._on_thumb_press(2, _event(pane))
    pane._on_thumb_motion(_event(pane, dx=-400))
    assert pane._dragging and pane._drag_drop_index is None
    pane._on_thumb_release()
    assert pane.events == [("delete", [2])]


def test_escape_cancels_drag(pane):
    _click(pane, 2)
    pane._on_thumb_press(2, _event(pane))
    pane._on_thumb_motion(_event(pane, dx=-400))
    pane._on_escape()
    pane._on_thumb_release()
    assert pane.events == [] and pane.selected_pages() == [2]


def test_selection_is_restored_after_document_reload(pane, sample_bytes, motfax):
    """ページの編集の後、動かしたページを選択した状態で一覧を作り直せる。"""
    pane.set_document(sample_bytes("sample06_mixed_sizes.pdf"), motfax,
                      ConvertOptions(mode="binarize"), selection=[2, 3], active=3)
    assert pane.selected_pages() == [2, 3]
    assert pane._selected_index == 3
    pane.set_document(sample_bytes("sample01_a4_text.pdf"), motfax,
                      ConvertOptions(mode="binarize"), selection=[7])
    assert pane.selected_pages() == [0]            # 範囲の外の指定は、先頭のページにする
