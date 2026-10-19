# -*- coding: utf-8 -*-
"""入力ファイル一覧のドラッグによる並べ替え（F-15）のテスト。

画面は起動せず、並べ替えの計算部分（_move_input_paths）のみを検証する。
挿入位置drop_indexは「行と行の境界」であり、移動前の一覧における位置で与える
（0が先頭の前、len(paths)が末尾の後）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from gui.app import _move_input_paths


def _paths(*names: str) -> list[Path]:
    return [Path(f"C:/x/{n}.pdf") for n in names]


def _names(paths: list[Path]) -> list[str]:
    return [p.stem for p in paths]


def test_move_single_line_downward():
    """1行を下方向へ移動する（a b c d → b c a d）。"""
    result, insert_at = _move_input_paths(_paths("a", "b", "c", "d"), 0, 0, 3)
    assert _names(result) == ["b", "c", "a", "d"]
    assert insert_at == 2


def test_move_single_line_upward():
    """1行を上方向へ移動する（a b c d → c a b d）。"""
    result, insert_at = _move_input_paths(_paths("a", "b", "c", "d"), 2, 2, 0)
    assert _names(result) == ["c", "a", "b", "d"]
    assert insert_at == 0


def test_move_to_end():
    """末尾（境界＝件数）へのドロップで最後尾へ移動する。"""
    result, insert_at = _move_input_paths(_paths("a", "b", "c"), 0, 0, 3)
    assert _names(result) == ["b", "c", "a"]
    assert insert_at == 2


def test_move_block_of_multiple_lines():
    """複数行の塊を移動しても、塊の中の順序は保たれる。"""
    result, insert_at = _move_input_paths(_paths("a", "b", "c", "d", "e"), 1, 2, 5)
    assert _names(result) == ["a", "d", "e", "b", "c"]
    assert insert_at == 3


def test_move_block_upward():
    result, insert_at = _move_input_paths(_paths("a", "b", "c", "d", "e"), 2, 3, 1)
    assert _names(result) == ["a", "c", "d", "b", "e"]
    assert insert_at == 1


@pytest.mark.parametrize("drop_index", [1, 2])
def test_drop_inside_own_range_is_noop(drop_index):
    """自分自身の範囲内（前後の境界を含む）へのドロップは一覧を変えない。"""
    original = _paths("a", "b", "c", "d")
    result, insert_at = _move_input_paths(original, 1, 1, drop_index)
    assert _names(result) == ["a", "b", "c", "d"]
    assert insert_at == 1


def test_move_does_not_lose_or_duplicate_files():
    """並べ替えでファイルが増減しない（結合対象の取り違え防止）。"""
    original = _paths("a", "b", "c", "d", "e")
    result, _ = _move_input_paths(original, 0, 1, 4)
    assert sorted(_names(result)) == sorted(_names(original))
    assert len(result) == len(original)
