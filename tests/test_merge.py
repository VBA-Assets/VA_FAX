# -*- coding: utf-8 -*-
"""core.merge（複数入力PDFのメモリ内結合。F-01）のテスト。"""
from __future__ import annotations

import pytest

from core import convert, diagnose
from core.merge import MergeError, merge_pdfs


def _page_count(pdf_bytes: bytes) -> int:
    doc = convert.open_document(pdf_bytes)
    try:
        return len(doc)
    finally:
        doc.close()


def _page_sizes_mm(pdf_bytes: bytes) -> list[tuple[float, float]]:
    doc = convert.open_document(pdf_bytes)
    try:
        return [convert.get_page_size_mm(doc[i]) for i in range(len(doc))]
    finally:
        doc.close()


def test_merge_preserves_order_and_page_count(sample_bytes):
    """結合後の総ページ数が全入力の合計となり、ページ順が入力順に維持されること。"""
    first = sample_bytes("sample01_a4_text.pdf")   # A4 3ページ
    second = sample_bytes("sample13_a5_small.pdf")  # A5 1ページ

    merged = merge_pdfs([first, second])

    assert _page_count(merged) == _page_count(first) + _page_count(second)
    sizes = _page_sizes_mm(merged)
    # 先頭3ページはA4（210×297mm）、末尾1ページはA5（148×210mm）の順であること
    for width_mm, height_mm in sizes[:3]:
        assert width_mm == pytest.approx(210.0, abs=2.0)
        assert height_mm == pytest.approx(297.0, abs=2.0)
    assert sizes[3][0] == pytest.approx(148.0, abs=2.0)
    assert sizes[3][1] == pytest.approx(210.0, abs=2.0)


def test_merge_single_file_returns_bytes_unchanged(sample_bytes):
    """入力1件の場合は再構築せず、バイト単位で同一の内容を返すこと。"""
    data = sample_bytes("sample01_a4_text.pdf")
    assert merge_pdfs([data]) == data


def test_merge_result_is_diagnosable(sample_bytes, motfax):
    """結合後のPDFが既存の診断パイプライン（F-04）にそのまま渡せること。"""
    merged = merge_pdfs([
        sample_bytes("sample01_a4_text.pdf"),
        sample_bytes("sample02_b4_text.pdf"),
    ])
    result = diagnose.diagnose(merged, motfax)
    assert result.page_count == 5  # 3ページ＋2ページ
    # 結合後の通し番号で対応外サイズ（B4）ページが検出されること（4・5ページ目）
    assert result.unsupported_pages == (4, 5)


def test_merge_encrypted_file_raises_with_index(sample_bytes):
    """暗号化PDFが含まれる場合、何件目かを保持したMergeErrorとなること（F-20）。"""
    with pytest.raises(MergeError) as exc_info:
        merge_pdfs([
            sample_bytes("sample01_a4_text.pdf"),
            sample_bytes("sample11_encrypted.pdf"),
        ])
    assert exc_info.value.file_index == 1
    assert "パスワード" in str(exc_info.value)


def test_merge_single_encrypted_file_raises(sample_bytes):
    """入力1件でも暗号化PDFは検出されること（パススルーで素通りしないこと）。"""
    with pytest.raises(MergeError) as exc_info:
        merge_pdfs([sample_bytes("sample11_encrypted.pdf")])
    assert exc_info.value.file_index == 0


def test_merge_corrupted_file_raises_with_index(sample_bytes, corrupted_pdf_bytes):
    """破損PDFが含まれる場合、何件目かを保持したMergeErrorとなること（F-20）。"""
    with pytest.raises(MergeError) as exc_info:
        merge_pdfs([corrupted_pdf_bytes, sample_bytes("sample01_a4_text.pdf")])
    assert exc_info.value.file_index == 0
    assert "破損" in str(exc_info.value)


def test_merge_empty_list_raises():
    with pytest.raises(ValueError):
        merge_pdfs([])
