# -*- coding: utf-8 -*-
"""core/diagnose.py のテスト（F-04・F-20検出）。"""
from __future__ import annotations

from dataclasses import replace

import pytest

from core.diagnose import CorruptedPdfError, EncryptedPdfError, diagnose, identify_paper_size_name


def test_sample01_basic_text_document(motfax, sample_bytes):
    d = diagnose(sample_bytes("sample01_a4_text.pdf"), motfax)
    assert d.page_count == 3
    assert d.has_text is True
    assert d.unsupported_pages == ()
    assert all(p.is_supported_size for p in d.pages)
    assert all(p.identified_size_name == "A4" for p in d.pages)


def test_sample02_b4_unsupported_for_motfax(motfax, sample_bytes):
    d = diagnose(sample_bytes("sample02_b4_text.pdf"), motfax)
    assert d.unsupported_pages == (1, 2)
    # 対応外サイズでも、既知の用紙規格名（B4）は識別できる（C-1・診断表示用）
    assert all(p.identified_size_name == "B4" for p in d.pages)


def test_identify_paper_size_name_known_and_unknown():
    assert identify_paper_size_name(210.0, 297.0) == "A4"
    assert identify_paper_size_name(297.0, 210.0) == "A4"  # 向き無視
    assert identify_paper_size_name(257.0, 364.0) == "B4"
    assert identify_paper_size_name(215.9, 279.4) == "レター"
    assert identify_paper_size_name(123.0, 456.0) is None


def test_sample02_b4_supported_for_movfax(movfax, sample_bytes):
    d = diagnose(sample_bytes("sample02_b4_text.pdf"), movfax)
    assert d.unsupported_pages == ()


def test_sample03_a3_unsupported_for_motfax(motfax, sample_bytes):
    d = diagnose(sample_bytes("sample03_a3_text.pdf"), motfax)
    assert d.unsupported_pages == (1,)


def test_sample04_landscape_detected_but_size_supported(motfax, movfax, sample_bytes):
    data = sample_bytes("sample04_a4_landscape.pdf")
    d_motfax = diagnose(data, motfax)
    assert all(p.is_landscape for p in d_motfax.pages)
    assert d_motfax.unsupported_pages == ()

    d_movfax = diagnose(data, movfax)
    assert d_movfax.unsupported_pages == ()


def test_sample05_letter_outside_tolerance(motfax, sample_bytes):
    d = diagnose(sample_bytes("sample05_letter.pdf"), motfax)
    assert d.unsupported_pages == (1,)


def test_sample06_mixed_pages_flagged_individually(motfax, sample_bytes):
    d = diagnose(sample_bytes("sample06_mixed_sizes.pdf"), motfax)
    assert d.unsupported_pages == (2, 3)
    assert d.pages[3].is_landscape is True
    assert d.pages[3].is_supported_size is True


def test_sample07_color_photo_content_type(motfax, sample_bytes):
    d = diagnose(sample_bytes("sample07_color_photo.pdf"), motfax)
    assert d.pages[0].content_type == "color"


def test_sample08_scan_like_content_type_mono(motfax, sample_bytes):
    d = diagnose(sample_bytes("sample08_scan_like.pdf"), motfax)
    assert all(p.content_type == "mono" for p in d.pages)


def test_sample09_mixed_content_per_page_classification(motfax, sample_bytes):
    d = diagnose(sample_bytes("sample09_mixed_content.pdf"), motfax)
    assert [p.content_type for p in d.pages] == ["mono", "color", "mono", "mono"]


def test_sample12_light_color_lines_content_type_color(motfax, sample_bytes):
    """淡いピンクの罫線のみのページも、色があるものとして「カラー」判定されること
    （単純閾値では消えてしまう色の存在を、事前診断でも見落とさない）。"""
    d = diagnose(sample_bytes("sample12_light_color_lines.pdf"), motfax)
    assert d.pages[0].content_type == "color"


def test_sample10_page_count_within_movfax_limit(movfax, sample_bytes):
    d = diagnose(sample_bytes("sample10_photo_heavy.pdf"), movfax)
    assert d.page_count == 11
    assert d.page_count_exceeds_limit is False


def test_sample10_page_count_exceeds_sokusoku_limit(sokusoku, sample_bytes):
    d = diagnose(sample_bytes("sample10_photo_heavy.pdf"), sokusoku)
    assert d.page_count_exceeds_limit is True


def test_page_count_exactly_at_limit_is_not_exceeded(motfax, sample_bytes):
    """ページ数がプロファイルの上限とちょうど同じ場合は「上限超過」にならないこと（境界値）。"""
    exactly_at_limit = replace(motfax, page_count_limit=3)
    d = diagnose(sample_bytes("sample01_a4_text.pdf"), exactly_at_limit)  # 3ページ
    assert d.page_count == 3
    assert d.page_count_exceeds_limit is False


def test_sample10_photo_heavy_content_type_mostly_non_mono(motfax, sample_bytes):
    """診断結果のカラー/グレースケール判定（F-04）は、圧縮方式の自動推奨（廃止済み・F-06）ではなく、
    ユーザーがグレースケールを選ぶかどうかの判断材料として画面に表示される（C-1）。
    """
    d = diagnose(sample_bytes("sample10_photo_heavy.pdf"), motfax)
    non_mono = sum(1 for p in d.pages if p.content_type != "mono")
    assert non_mono / d.page_count >= 0.5


def test_encrypted_pdf_raises(motfax, sample_bytes):
    with pytest.raises(EncryptedPdfError):
        diagnose(sample_bytes("sample11_encrypted.pdf"), motfax)


def test_corrupted_pdf_raises(motfax, corrupted_pdf_bytes):
    with pytest.raises(CorruptedPdfError):
        diagnose(corrupted_pdf_bytes, motfax)
