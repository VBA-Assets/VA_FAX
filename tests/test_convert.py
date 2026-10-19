# -*- coding: utf-8 -*-
"""core/convert.py のテスト（F-07〜F-12・F-14）。"""
from __future__ import annotations

import io
from dataclasses import replace

import pypdf
import pytest
from PIL import Image

from core.convert import (
    ConvertOptions,
    PageCountExceededError,
    UnsupportedPageSizeError,
    choose_dither_method,
    convert,
    has_color_content,
    normalize_page,
)


def _extract_text(pdf_bytes: bytes) -> str:
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    return "".join(page.extract_text() or "" for page in reader.pages)


def _filter_names(xobject) -> list[str]:
    f = xobject.get("/Filter")
    if f is None:
        return []
    if isinstance(f, list):
        return [str(x) for x in f]
    return [str(f)]


def _mediabox_mm(page) -> tuple[float, float]:
    box = page.mediabox
    return float(box.width) / 72.0 * 25.4, float(box.height) / 72.0 * 25.4


def test_rasterize_removes_text_layer(motfax, sample_bytes):
    data = sample_bytes("sample01_a4_text.pdf")
    result = convert(data, motfax, ConvertOptions(mode="binarize"))
    pdf_bytes = result.build_pdf()
    assert _extract_text(pdf_bytes) == ""
    assert len(pypdf.PdfReader(io.BytesIO(pdf_bytes)).pages) == 3


def test_b4_normalized_to_a4_about_82_percent(motfax, sample_bytes):
    data = sample_bytes("sample02_b4_text.pdf")
    result = convert(data, motfax, ConvertOptions(mode="binarize"))
    for log in result.page_logs:
        assert log.normalized is True
        assert log.scale_ratio == pytest.approx(0.816, abs=0.01)


def test_a3_normalized_to_a4_about_71_percent(motfax, sample_bytes):
    data = sample_bytes("sample03_a3_text.pdf")
    result = convert(data, motfax, ConvertOptions(mode="binarize"))
    assert result.page_logs[0].scale_ratio == pytest.approx(0.707, abs=0.01)


def test_landscape_rotated_for_motfax_not_for_movfax(motfax, movfax, sample_bytes):
    data = sample_bytes("sample04_a4_landscape.pdf")

    result_motfax = convert(data, motfax, ConvertOptions(mode="binarize"))
    assert all(log.rotated for log in result_motfax.page_logs)
    assert all(log.scale_ratio == 1.0 and not log.normalized for log in result_motfax.page_logs)

    result_movfax = convert(data, movfax, ConvertOptions(mode="binarize"))
    assert all(not log.rotated for log in result_movfax.page_logs)


def test_tolerance_boundary_synthetic(motfax):
    base = Image.new("RGB", (100, 100), "white")
    within = normalize_page(base, 210.0 + 1.5, 297.0, dpi=100, profile=motfax)
    outside = normalize_page(base, 210.0 + 2.5, 297.0, dpi=100, profile=motfax)
    assert within.scaled is False
    assert outside.scaled is True


def test_undersized_page_is_centered_without_enlarging(motfax):
    """正規化先サイズ（A4）より小さい対応外サイズのページは、あえて拡大せず
    原寸のまま中央配置されること。出力キャンバス自体はA4サイズになること
    （用紙正規化＝正規化先サイズのキャンバスに収める処理であり、拡大は不要）。"""
    dpi = 100
    width_mm, height_mm = 148.0, 210.0  # A5相当。A4より小さい。
    src_px_w = round(width_mm / 25.4 * dpi)
    src_px_h = round(height_mm / 25.4 * dpi)
    small_image = Image.new("RGB", (src_px_w, src_px_h), "white")

    result = normalize_page(small_image, width_mm, height_mm, dpi=dpi, profile=motfax)

    assert result.scaled is True  # 対応外サイズとして正規化処理を経由したこと
    assert result.scale_ratio == pytest.approx(1.0)  # 拡大していないこと

    target = motfax.normalize_target
    expected_w = round(target.width_mm / 25.4 * dpi)
    expected_h = round(target.height_mm / 25.4 * dpi)
    assert result.image.size == (expected_w, expected_h)  # キャンバス自体はA4サイズになること


def test_a5_sample_becomes_a4_output_without_enlarging(motfax, sample_bytes):
    """A5文書を実際に変換すると、出力PDFのページサイズはA4になること（拡大はしない）。"""
    data = sample_bytes("sample13_a5_small.pdf")
    result = convert(data, motfax, ConvertOptions(mode="binarize"))
    assert result.page_logs[0].scale_ratio == pytest.approx(1.0)

    pdf_bytes = result.build_pdf()
    page = pypdf.PdfReader(io.BytesIO(pdf_bytes)).pages[0]
    width_mm = float(page.mediabox.width) / 72 * 25.4
    height_mm = float(page.mediabox.height) / 72 * 25.4
    assert width_mm == pytest.approx(210.0, abs=2.0)
    assert height_mm == pytest.approx(297.0, abs=2.0)


def test_mixed_sizes_normalized_independently(motfax, sample_bytes):
    data = sample_bytes("sample06_mixed_sizes.pdf")
    result = convert(data, motfax, ConvertOptions(mode="binarize"))
    logs = result.page_logs
    assert logs[0].normalized is False and logs[0].rotated is False
    assert logs[1].normalized is True
    assert logs[2].normalized is True
    assert logs[3].normalized is False and logs[3].rotated is True


def test_auto_dither_color_photo_uses_diffusion(motfax, sample_bytes):
    result = convert(sample_bytes("sample07_color_photo.pdf"), motfax, ConvertOptions(mode="binarize"))
    assert result.page_logs[0].dither_method == "diffusion"


def test_auto_dither_scan_like_uses_threshold_and_is_much_smaller_than_diffusion(motfax, sample_bytes):
    data = sample_bytes("sample08_scan_like.pdf")
    auto_result = convert(data, motfax, ConvertOptions(mode="binarize"))
    assert all(log.dither_method == "threshold" for log in auto_result.page_logs)

    forced_diffusion = convert(data, motfax, ConvertOptions(mode="binarize", dither_method="diffusion"))
    auto_total = sum(len(x) for x in auto_result.page_images)
    diffusion_total = sum(len(x) for x in forced_diffusion.page_images)
    assert auto_total < diffusion_total / 2


def test_auto_dither_light_color_lines_uses_diffusion(motfax, sample_bytes):
    """淡色（無彩色でない）罫線は、輝度だけでは白背景と区別できず単純閾値では
    消えてしまうため、自動判定では誤差拡散が選ばれること（実際の銀行帳票PDFで
    確認された不具合の再現・回帰防止）。"""
    result = convert(sample_bytes("sample12_light_color_lines.pdf"), motfax, ConvertOptions(mode="binarize"))
    assert result.page_logs[0].dither_method == "diffusion"


def test_has_color_content_detects_light_pink_but_not_grayscale():
    """輝度では白に近い淡いピンクも色として検出し、真の無彩色（グレー）は検出しないこと。"""
    light_pink = Image.new("RGB", (50, 50), (252, 163, 227))
    assert has_color_content(light_pink) is True

    gray = Image.new("RGB", (50, 50), (200, 200, 200))
    assert has_color_content(gray) is False


def test_choose_dither_method_prefers_diffusion_when_color_present():
    """色（淡色含む）があれば、輝度分布に関わらず誤差拡散を選ぶこと。"""
    image = Image.new("RGB", (100, 100), (255, 255, 255))
    for x in range(100):
        for y in range(4):
            image.putpixel((x, y), (252, 163, 227))  # 淡いピンクの線
    assert choose_dither_method(image) == "diffusion"


def test_threshold_binarize_preserves_already_binarized_image():
    """既に二値化済みの画像（画素が0と255のみ）を単純閾値で再二値化しても
    内容が消えないこと。大津法の閾値が0と算出されるため、比較を>=にすると
    全画素が白になってしまう（二値化済みFAX出力の再入力で実際に発生した不具合の
    回帰防止）。"""
    from core.convert import binarize_page

    image = Image.new("L", (100, 100), 255)
    for x in range(30, 70):
        for y in range(30, 70):
            image.putpixel((x, y), 0)

    bw, method = binarize_page(image.convert("RGB"), "threshold")
    assert method == "threshold"
    histogram = bw.convert("L").histogram()
    assert histogram[0] == 40 * 40  # 黒画素がそのまま残ること
    assert histogram[255] == 100 * 100 - 40 * 40


def test_input_metadata_is_not_carried_over_to_output(motfax, sample_bytes):
    """入力PDFの文書情報（作成者・タイトル等のメタデータ）が出力に一切残らないこと。

    本ツールは画像から新しいPDFを一から再構築するため、メタデータは構造的に
    引き継がれない（守秘設計の一部）。将来、PDF構築方法の変更でこの性質が
    失われた場合に検知するための回帰テスト。
    """
    reader = pypdf.PdfReader(io.BytesIO(sample_bytes("sample01_a4_text.pdf")))
    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.add_metadata({
        "/Title": "機密テスト文書",
        "/Author": "テスト作成者",
        "/Creator": "テスト作成アプリ",
        "/Keywords": "秘密キーワード",
    })
    buf = io.BytesIO()
    writer.write(buf)
    tainted = buf.getvalue()

    for mode in ("binarize", "grayscale"):
        output = convert(tainted, motfax, ConvertOptions(mode=mode)).build_pdf()
        metadata = pypdf.PdfReader(io.BytesIO(output)).metadata or {}
        for key in ("/Title", "/Author", "/Creator", "/Keywords"):
            assert key not in metadata
        # 解析上のメタデータだけでなく、生バイト列にも痕跡が残らないこと
        for needle in ("機密テスト文書", "テスト作成者", "秘密キーワード"):
            assert needle.encode("utf-8") not in output
            assert needle.encode("utf-16-be") not in output


def test_threshold_binarize_reconverted_fax_output_keeps_content(motfax, sample_bytes):
    """単純閾値で変換した出力PDFを再度入力し、再び単純閾値で変換しても
    ページ内容が白紙にならないこと（エンドツーエンドの回帰防止）。"""
    from core.convert import open_document, rasterize_page

    data = sample_bytes("sample01_a4_text.pdf")
    options = ConvertOptions(mode="binarize", dither_method="threshold")
    first_pdf = convert(data, motfax, options).build_pdf()
    second_pdf = convert(first_pdf, motfax, options).build_pdf()

    doc = open_document(second_pdf)
    try:
        histogram = rasterize_page(doc[0], 72).convert("L").histogram()
    finally:
        doc.close()
    nonwhite_ratio = sum(histogram[:250]) / sum(histogram)
    assert nonwhite_ratio > 0.05


def test_auto_dither_varies_per_page_in_mixed_content(motfax, sample_bytes):
    result = convert(sample_bytes("sample09_mixed_content.pdf"), motfax, ConvertOptions(mode="binarize"))
    methods = [log.dither_method for log in result.page_logs]
    assert methods == ["threshold", "diffusion", "threshold", "threshold"]


def test_grayscale_quality_affects_size_monotonically(motfax, sample_bytes):
    data = sample_bytes("sample07_color_photo.pdf")
    ordered_qualities = sorted(motfax.jpeg_quality_steps)
    sizes = []
    for quality in ordered_qualities:
        result = convert(data, motfax, ConvertOptions(mode="grayscale", jpeg_quality=quality))
        sizes.append(sum(len(x) for x in result.page_images))
    assert sizes[0] < sizes[1] < sizes[2]


def test_output_image_filters(motfax, sample_bytes):
    data = sample_bytes("sample01_a4_text.pdf")

    bw_pdf = convert(data, motfax, ConvertOptions(mode="binarize")).build_pdf()
    reader = pypdf.PdfReader(io.BytesIO(bw_pdf))
    xobj = reader.pages[0]["/Resources"]["/XObject"]
    filters = [name for ref in xobj.values() for name in _filter_names(ref.get_object())]
    assert "/CCITTFaxDecode" in filters

    gray_pdf = convert(data, motfax, ConvertOptions(mode="grayscale")).build_pdf()
    reader2 = pypdf.PdfReader(io.BytesIO(gray_pdf))
    xobj2 = reader2.pages[0]["/Resources"]["/XObject"]
    filters2 = [name for ref in xobj2.values() for name in _filter_names(ref.get_object())]
    assert "/DCTDecode" in filters2


def test_page_count_exceeded_raises_and_does_not_convert(sokusoku, sample_bytes):
    with pytest.raises(PageCountExceededError):
        convert(sample_bytes("sample10_photo_heavy.pdf"), sokusoku, ConvertOptions(mode="binarize"))


def test_page_count_exactly_at_limit_is_not_an_error(motfax, sample_bytes):
    """ページ数がプロファイルの上限とちょうど同じ場合はエラーにならないこと（境界値）。"""
    data = sample_bytes("sample01_a4_text.pdf")  # 3ページ
    exactly_at_limit = replace(motfax, page_count_limit=3)
    result = convert(data, exactly_at_limit, ConvertOptions(mode="binarize"))
    assert len(result.page_logs) == 3


def test_unsupported_size_with_normalize_off_raises(motfax, sample_bytes):
    with pytest.raises(UnsupportedPageSizeError):
        convert(
            sample_bytes("sample02_b4_text.pdf"), motfax,
            ConvertOptions(mode="binarize", normalize_enabled=False),
        )


def test_unsupported_size_with_normalize_off_ok_when_size_supported(movfax, sample_bytes):
    result = convert(
        sample_bytes("sample02_b4_text.pdf"), movfax,
        ConvertOptions(mode="binarize", normalize_enabled=False),
    )
    assert len(result.page_logs) == 2


def test_encrypted_pdf_raises_conversion_error(motfax, sample_bytes):
    from core.convert import ConversionError
    with pytest.raises(ConversionError):
        convert(sample_bytes("sample11_encrypted.pdf"), motfax, ConvertOptions(mode="binarize"))


def test_output_pages_match_profile_size_in_actual_pdf(motfax, movfax, sample_bytes):
    """出力PDFの実寸（MediaBox）が、選択中プロファイルの対応サイズであること（許容誤差内）。

    scale_ratio・normalizedフラグ経由の間接検証だけでなく、実際に生成されたPDFの
    ページサイズを読み取って検証する。
    """
    tolerance_mm = 2.5  # dpi丸め誤差込みの許容（±2mm規格＋若干の余裕）

    # 対応外サイズ（B4）→ 正規化されA4になること
    b4_pdf = convert(sample_bytes("sample02_b4_text.pdf"), motfax, ConvertOptions(mode="binarize")).build_pdf()
    reader = pypdf.PdfReader(io.BytesIO(b4_pdf))
    for page in reader.pages:
        width_mm, height_mm = _mediabox_mm(page)
        assert abs(width_mm - motfax.normalize_target.width_mm) <= tolerance_mm
        assert abs(height_mm - motfax.normalize_target.height_mm) <= tolerance_mm

    # 対応サイズ（movfaxのB4）→ 正規化されず原寸のままであること
    b4_pdf_movfax = convert(
        sample_bytes("sample02_b4_text.pdf"), movfax, ConvertOptions(mode="binarize")).build_pdf()
    reader2 = pypdf.PdfReader(io.BytesIO(b4_pdf_movfax))
    b4_size = next(s for s in movfax.supported_page_sizes if s.name == "B4")
    for page in reader2.pages:
        width_mm, height_mm = _mediabox_mm(page)
        a = sorted((width_mm, height_mm))
        b = sorted((b4_size.width_mm, b4_size.height_mm))
        assert abs(a[0] - b[0]) <= tolerance_mm
        assert abs(a[1] - b[1]) <= tolerance_mm
