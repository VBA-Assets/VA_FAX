# -*- coding: utf-8 -*-
"""report.py のテスト（F-25）。"""
from __future__ import annotations

from pathlib import Path

from core.convert import PageConversionLog
from report import ReportData, build_report_text, report_path_for, save_report


def _page_log(page_index: int, dpi: int = 200, dither: str | None = "threshold",
              quality: int | None = None) -> PageConversionLog:
    return PageConversionLog(
        page_index=page_index, original_width_mm=210.0, original_height_mm=297.0,
        normalized=False, rotated=False, scale_ratio=1.0, dither_method=dither,
        resolution_dpi=dpi, jpeg_quality=quality,
    )


def _report_data(**overrides) -> ReportData:
    base = dict(
        input_filename="input.pdf",
        input_size_bytes=1024 * 1024,
        output_filename="input_fax.pdf",
        profile_display_name="MOT/FAX",
        mode="binarize",
        normalize_enabled=True,
        capacity_exceeded_method="reduce_resolution",
        page_logs=(_page_log(0), _page_log(1)),
        final_output_size_bytes=500 * 1024,
    )
    base.update(overrides)
    return ReportData(**base)


def test_report_contains_basic_fields_and_capacity_method_label():
    text = build_report_text(_report_data())
    assert "元ファイル名: input.pdf" in text
    assert "出力ファイル名: input_fax.pdf" in text
    assert "対象サービス: MOT/FAX" in text
    assert "ページ数: 2" in text
    assert "圧縮方式: 二値化（白黒）" in text
    # UI・要件定義書（F-13）の選択肢表記と一致すること
    assert "容量超過時の処理方式: 解像度・品質を下げて再実行" in text


def test_report_split_method_label():
    text = build_report_text(_report_data(capacity_exceeded_method="split"))
    assert "容量超過時の処理方式: 分割して再実行" in text


def test_report_split_part_lists_only_its_own_pages_with_original_numbers():
    """分割後のパートごとレポートは、そのパートに含まれるページのみを、
    元文書基準のページ番号で記載すること（F-25）。"""
    part2 = _report_data(
        output_filename="input_fax_2of2.pdf",
        page_logs=(_page_log(2), _page_log(3)),
    )
    text = build_report_text(part2)
    assert "出力ファイル名: input_fax_2of2.pdf" in text
    assert "ページ数: 2" in text
    assert "3ページ目" in text and "4ページ目" in text
    assert "1ページ目" not in text


def test_report_grayscale_includes_quality():
    text = build_report_text(_report_data(
        mode="grayscale", jpeg_quality_requested=75,
        page_logs=(_page_log(0, dither=None, quality=75),),
    ))
    assert "圧縮方式: グレースケール" in text
    assert "JPEG品質（初期設定）: 75" in text
    assert "JPEG品質: 75" in text


def test_report_path_and_save(tmp_path):
    output_pdf = tmp_path / "foo_fax_1of2.pdf"
    assert report_path_for(output_pdf) == tmp_path / "foo_fax_1of2_変換レポート.txt"

    saved = save_report(output_pdf, _report_data(output_filename=output_pdf.name))
    assert saved == tmp_path / "foo_fax_1of2_変換レポート.txt"
    content = Path(saved).read_text(encoding="utf-8")
    assert "foo_fax_1of2.pdf" in content
