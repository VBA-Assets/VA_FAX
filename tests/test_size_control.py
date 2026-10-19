# -*- coding: utf-8 -*-
"""core/size_control.py のテスト（F-13）。段階値はプロファイル経由で与える（F-29）。"""
from __future__ import annotations

from dataclasses import replace

import pytest

from core.convert import ConvertOptions, convert
from core.size_control import SizeLimitExceededError, enforce_size_limit, split_by_capacity


def test_no_reduction_when_already_within_limit(motfax, sample_bytes):
    data = sample_bytes("sample01_a4_text.pdf")
    options = ConvertOptions(mode="binarize")
    result = convert(data, motfax, options)
    final = enforce_size_limit(data, motfax, options, result)
    assert final is result


def test_only_culprit_page_is_reduced_others_untouched(motfax, sample_bytes):
    """写真ページのみが容量超過の原因である場合、他ページのdpi・出力は変化しない。"""
    tiny = replace(motfax, max_file_size_bytes=600_000)
    data = sample_bytes("sample09_mixed_content.pdf")
    options = ConvertOptions(mode="binarize")

    result = convert(data, tiny, options)
    initial_sizes = [len(x) for x in result.page_images]

    final = enforce_size_limit(data, tiny, options, result)

    assert len(final.build_pdf()) <= tiny.max_file_size_bytes
    # page1（カラー写真）が容量超過の原因であり、解像度が下げられていること
    assert final.page_logs[1].resolution_dpi < motfax.resolution_dpi_steps[0]
    # 原因でないページ（0, 2, 3）は解像度・出力とも変化しないこと
    for i in (0, 2, 3):
        assert final.page_logs[i].resolution_dpi == motfax.resolution_dpi_steps[0]
        assert len(final.page_images[i]) == initial_sizes[i]


def test_binarize_resolution_cascade_matches_profile_steps(motfax, sample_bytes):
    """再試行段階はプロファイルのresolution_dpi_stepsに従うこと（ハードコード禁止・F-29）。"""
    custom = replace(motfax, max_file_size_bytes=50_000, resolution_dpi_steps=(300, 100, 40))
    data = sample_bytes("sample07_color_photo.pdf")
    options = ConvertOptions(mode="binarize", resolution_dpi=300)

    result = convert(data, custom, options)
    final = enforce_size_limit(data, custom, options, result)

    used_dpis = {log.resolution_dpi for log in final.page_logs}
    assert used_dpis <= {300, 100, 40}
    assert 300 not in used_dpis or len(final.build_pdf()) <= custom.max_file_size_bytes


def test_grayscale_quality_reduced_before_resolution(motfax, sample_bytes):
    """グレースケール時はJPEG品質を先に下げ、品質下限到達後に解像度を下げること。"""
    tiny = replace(motfax, max_file_size_bytes=250_000)
    data = sample_bytes("sample07_color_photo.pdf")
    options = ConvertOptions(mode="grayscale")

    result = convert(data, tiny, options)
    final = enforce_size_limit(data, tiny, options, result)
    log = final.page_logs[0]

    assert len(final.build_pdf()) <= tiny.max_file_size_bytes
    if log.resolution_dpi == motfax.resolution_dpi_steps[0]:
        # 解像度が初期値のままなら、品質は初期値より下がっているはず
        assert log.jpeg_quality < motfax.jpeg_quality_default
    else:
        # 解像度まで下げたのであれば、品質は最低段階に達しているはず
        assert log.jpeg_quality == min(motfax.jpeg_quality_steps)


def test_uniform_strategy_reduces_all_candidate_pages_not_just_culprit(motfax, sample_bytes):
    """均等削減方式（strategy="uniform"）では、原因でないページも含めて
    削減可能な全ページが対象になる（F-13・F-26の削減方式選択）。"""
    tiny = replace(motfax, max_file_size_bytes=600_000)
    data = sample_bytes("sample09_mixed_content.pdf")
    options = ConvertOptions(mode="binarize")

    result = convert(data, tiny, options)
    final = enforce_size_limit(data, tiny, options, result, strategy="uniform")

    assert len(final.build_pdf()) <= tiny.max_file_size_bytes
    changed = [i for i in range(4) if final.page_logs[i].resolution_dpi < motfax.resolution_dpi_steps[0]]
    assert len(changed) > 1  # 原因ページ（page1）だけでなく複数ページが対象になっていること


def test_culprit_only_is_the_default_strategy(motfax, sample_bytes):
    """strategy省略時は既定のculprit_onlyと同じ結果になること（後方互換）。"""
    tiny = replace(motfax, max_file_size_bytes=600_000)
    data = sample_bytes("sample09_mixed_content.pdf")
    options = ConvertOptions(mode="binarize")

    result = convert(data, tiny, options)
    default_result = enforce_size_limit(data, tiny, options, result)
    explicit_result = enforce_size_limit(data, tiny, options, result, strategy="culprit_only")

    assert default_result.page_logs == explicit_result.page_logs


def test_uniform_strategy_on_retry_reports_multiple_pages(motfax, sample_bytes):
    """均等削減方式では、on_retryが原因ページ以外のページ番号でも呼び出されること。"""
    tiny = replace(motfax, max_file_size_bytes=600_000)
    data = sample_bytes("sample09_mixed_content.pdf")
    options = ConvertOptions(mode="binarize")
    result = convert(data, tiny, options)

    calls = []
    enforce_size_limit(data, tiny, options, result, on_retry=lambda *args: calls.append(args),
                        strategy="uniform")

    page_indices = {page_index for page_index, *_ in calls}
    assert len(page_indices) > 1


def test_uniform_strategy_still_raises_when_floor_insufficient(motfax, sample_bytes):
    """均等削減方式でも、全ページ下限到達後になお超過する場合はエラーとなること。"""
    impossible = replace(motfax, max_file_size_bytes=1000)
    data = sample_bytes("sample01_a4_text.pdf")
    options = ConvertOptions(mode="binarize")
    result = convert(data, impossible, options)
    with pytest.raises(SizeLimitExceededError):
        enforce_size_limit(data, impossible, options, result, strategy="uniform")


def test_size_limit_exceeded_error_when_floor_still_over(motfax, sample_bytes):
    impossible = replace(motfax, max_file_size_bytes=1000)
    data = sample_bytes("sample01_a4_text.pdf")
    options = ConvertOptions(mode="binarize")
    result = convert(data, impossible, options)
    with pytest.raises(SizeLimitExceededError):
        enforce_size_limit(data, impossible, options, result)


def test_size_limit_exceeded_message_suggests_grayscale_when_binarize(motfax, sample_bytes):
    """二値化モードでの容量超過エラーには、グレースケールへの切替を提案する文言を含める。"""
    impossible = replace(motfax, max_file_size_bytes=1000)
    data = sample_bytes("sample01_a4_text.pdf")
    options = ConvertOptions(mode="binarize")
    result = convert(data, impossible, options)
    with pytest.raises(SizeLimitExceededError, match="グレースケール"):
        enforce_size_limit(data, impossible, options, result)


def test_size_limit_exceeded_message_omits_grayscale_suggestion_when_already_grayscale(
        motfax, sample_bytes):
    """既にグレースケールモードで超過した場合は、グレースケールへの切替提案は不要。"""
    impossible = replace(motfax, max_file_size_bytes=1000)
    data = sample_bytes("sample01_a4_text.pdf")
    options = ConvertOptions(mode="grayscale")
    result = convert(data, impossible, options)
    try:
        enforce_size_limit(data, impossible, options, result)
        assert False, "SizeLimitExceededErrorが発生するはず"
    except SizeLimitExceededError as exc:
        assert "グレースケール" not in str(exc)
        assert "ディザ方式" not in str(exc)


def test_size_limit_exceeded_message_suggests_threshold_when_auto_or_diffusion(
        motfax, sample_bytes):
    """二値化＋自動／誤差拡散での容量超過時は、単純閾値への切替も提案する。"""
    impossible = replace(motfax, max_file_size_bytes=1000)
    data = sample_bytes("sample01_a4_text.pdf")
    for dither_method in ("auto", "diffusion"):
        options = ConvertOptions(mode="binarize", dither_method=dither_method)
        result = convert(data, impossible, options)
        with pytest.raises(SizeLimitExceededError, match="ディザ方式"):
            enforce_size_limit(data, impossible, options, result)


def test_size_limit_exceeded_message_omits_threshold_suggestion_when_already_threshold(
        motfax, sample_bytes):
    """既に単純閾値を使っている場合は、これ以上ディザ方式で改善できないため提案しない。"""
    impossible = replace(motfax, max_file_size_bytes=1000)
    data = sample_bytes("sample01_a4_text.pdf")
    options = ConvertOptions(mode="binarize", dither_method="threshold")
    result = convert(data, impossible, options)
    try:
        enforce_size_limit(data, impossible, options, result)
        assert False, "SizeLimitExceededErrorが発生するはず"
    except SizeLimitExceededError as exc:
        assert "ディザ方式" not in str(exc)
        assert "グレースケール" in str(exc)


def test_photo_heavy_document_binarize_mode_may_exceed_even_at_floor(motfax, sample_bytes):
    """誤差拡散＋G4は写真調ページで圧縮効率が悪く、下限dpiでも上限を満たせない場合がある
    （F-13の想定するエラー経路。写真中心文書ではF-06推奨に従いグレースケールを選ぶべきケース）。
    """
    data = sample_bytes("sample10_photo_heavy.pdf")
    options = ConvertOptions(mode="binarize")
    result = convert(data, motfax, options)
    with pytest.raises(SizeLimitExceededError):
        enforce_size_limit(data, motfax, options, result)


def test_photo_heavy_document_converges_in_grayscale_mode(motfax, sample_bytes):
    """同じ文書でも、F-06推奨どおりグレースケールを選べばMOT/FAXの3MB上限に収まること。"""
    data = sample_bytes("sample10_photo_heavy.pdf")
    options = ConvertOptions(mode="grayscale")
    result = convert(data, motfax, options)
    final = enforce_size_limit(data, motfax, options, result)
    assert len(final.build_pdf()) <= motfax.max_file_size_bytes


def test_final_pdf_size_used_for_convergence_not_just_image_bytes(motfax, sample_bytes):
    """収束判定はページ画像バイト合計ではなく、実際のPDFバイト数（build_pdf後）で行うこと（F-13）。

    合計ぎりぎりで収束させると、PDFコンテナのオーバーヘッド分だけ最終ファイルが
    上限を超過しうるため、build_pdf後のサイズで判定する必要がある。
    """
    tiny = replace(motfax, max_file_size_bytes=600_000)
    data = sample_bytes("sample09_mixed_content.pdf")
    options = ConvertOptions(mode="binarize")
    result = convert(data, tiny, options)

    final = enforce_size_limit(data, tiny, options, result)

    assert len(final.build_pdf()) <= tiny.max_file_size_bytes
    # 画像バイト合計だけで見ても収束していること（オーバーヘッド分の余裕があるはず）
    assert sum(len(x) for x in final.page_images) <= tiny.max_file_size_bytes


def test_on_retry_callback_reports_each_reduction(motfax, sample_bytes):
    """on_retryが、削減が適用されるたびに正しい引数で呼び出されること（F-17・F-24）。"""
    tiny = replace(motfax, max_file_size_bytes=600_000)
    data = sample_bytes("sample09_mixed_content.pdf")
    options = ConvertOptions(mode="binarize")
    result = convert(data, tiny, options)

    calls = []
    enforce_size_limit(data, tiny, options, result,
                        on_retry=lambda *args: calls.append(args))

    assert len(calls) >= 1
    for page_index, old_size, new_size, dpi, quality in calls:
        assert page_index == 1  # 写真ページ（page1）のみが削減対象
        assert new_size < old_size
        assert dpi in tiny.resolution_dpi_steps
        assert quality is None  # 二値化モードなのでJPEG品質は使わない


# ---- split_by_capacity（F-13「分割して再実行」） -------------------------------------


def test_split_not_needed_when_already_within_limit(motfax, sample_bytes):
    """全体が既に容量上限以下の場合は分割せず、resultをそのまま1件で返すこと。"""
    data = sample_bytes("sample01_a4_text.pdf")
    options = ConvertOptions(mode="binarize")
    result = convert(data, motfax, options)
    parts = split_by_capacity(data, motfax, options, result)
    assert len(parts) == 1
    assert parts[0] is result


def test_split_into_multiple_parts_without_quality_loss(motfax, sample_bytes):
    """個々のページはいずれも単体で容量上限に収まる場合、画質を一切落とさず
    ページ順を維持したまま複数ファイルに分割すること。"""
    tiny = replace(motfax, max_file_size_bytes=1_050_000)
    data = sample_bytes("sample09_mixed_content.pdf")
    options = ConvertOptions(mode="binarize")
    result = convert(data, tiny, options)

    parts = split_by_capacity(data, tiny, options, result)

    assert len(parts) > 1
    # 全ページが過不足なく、元の順序どおりに分配されていること
    all_indices = [log.page_index for part in parts for log in part.page_logs]
    assert all_indices == list(range(4))
    # いずれのパートも容量上限以下であること
    for part in parts:
        assert len(part.build_pdf()) <= tiny.max_file_size_bytes
    # 画質（解像度）はどのページも変化しないこと（分割のみで解決できているため）
    for part in parts:
        for log in part.page_logs:
            assert log.resolution_dpi == motfax.resolution_dpi_steps[0]


def test_split_reduces_only_the_page_that_cannot_fit_alone(motfax, sample_bytes):
    """1ページ単体で容量上限を超えるページのみ解像度を下げ、他のページは変化しないこと。"""
    tiny = replace(motfax, max_file_size_bytes=600_000)
    data = sample_bytes("sample09_mixed_content.pdf")
    options = ConvertOptions(mode="binarize")
    result = convert(data, tiny, options)

    parts = split_by_capacity(data, tiny, options, result)

    for part in parts:
        assert len(part.build_pdf()) <= tiny.max_file_size_bytes
    all_logs = {log.page_index: log for part in parts for log in part.page_logs}
    assert len(all_logs) == 4
    # page1（写真ページ）のみ解像度が下げられ、他ページは初期解像度のまま
    assert all_logs[1].resolution_dpi < motfax.resolution_dpi_steps[0]
    for i in (0, 2, 3):
        assert all_logs[i].resolution_dpi == motfax.resolution_dpi_steps[0]


def test_split_raises_when_single_page_cannot_fit_even_at_floor(motfax, sample_bytes):
    """1ページを最小設定まで縮小してもなお容量上限に収まらない場合はエラーとなること。

    メッセージは「すべてのページを」ではなく該当ページを名指しし、既に分割を
    試みた末の失敗であることが伝わる内容（「ファイルを分割してください」という
    無意味な提案を含まない）であること。
    """
    impossible = replace(motfax, max_file_size_bytes=1000)
    data = sample_bytes("sample01_a4_text.pdf")
    options = ConvertOptions(mode="binarize")
    result = convert(data, impossible, options)
    with pytest.raises(SizeLimitExceededError, match="1ページ目") as excinfo:
        split_by_capacity(data, impossible, options, result)
    assert "すべてのページ" not in str(excinfo.value)
    assert "ファイルを分割する" not in str(excinfo.value)


def test_split_on_page_reduced_callback_reports_the_oversized_page(motfax, sample_bytes):
    """on_page_reducedが、単体超過ページの縮小時のみ正しい引数で呼び出されること。"""
    tiny = replace(motfax, max_file_size_bytes=600_000)
    data = sample_bytes("sample09_mixed_content.pdf")
    options = ConvertOptions(mode="binarize")
    result = convert(data, tiny, options)

    calls = []
    split_by_capacity(data, tiny, options, result,
                       on_page_reduced=lambda *args: calls.append(args))

    assert len(calls) >= 1
    for page_index, old_size, new_size, dpi, quality in calls:
        assert page_index == 1
        assert new_size < old_size
        assert dpi in tiny.resolution_dpi_steps
        assert quality is None


def test_split_boundary_at_exact_limit(motfax, sample_bytes):
    """上限がちょうど全体サイズと等しい場合は分割せず、1バイトでも下回ると分割すること。"""
    data = sample_bytes("sample09_mixed_content.pdf")
    options = ConvertOptions(mode="binarize")
    result = convert(data, motfax, options)
    total = len(result.build_pdf())

    exact = replace(motfax, max_file_size_bytes=total)
    assert len(split_by_capacity(data, exact, options, result)) == 1

    just_under = replace(motfax, max_file_size_bytes=total - 1)
    parts = split_by_capacity(data, just_under, options, result)
    assert len(parts) > 1
    for part in parts:
        assert len(part.build_pdf()) <= just_under.max_file_size_bytes


def _single_page_pdf(sample_bytes) -> bytes:
    """既存サンプルの1ページ目だけを取り出した、真の1ページPDFを作る（メモリ内）。"""
    import io

    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(sample_bytes("sample01_a4_text.pdf")))
    writer = pypdf.PdfWriter()
    writer.add_page(reader.pages[0])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_split_single_page_document_shrinks_into_one_file(motfax, sample_bytes):
    """1ページのみの文書はそもそも分割できないため、単体超過時は縮小のみで対処し、
    サフィックスなしの単一パートとして返ること。"""
    data = _single_page_pdf(sample_bytes)
    options = ConvertOptions(mode="binarize")
    result = convert(data, motfax, options)
    full_size = len(result.build_pdf())

    tiny = replace(motfax, max_file_size_bytes=full_size - 1)
    parts = split_by_capacity(data, tiny, options, result)

    assert len(parts) == 1
    assert len(parts[0].build_pdf()) <= tiny.max_file_size_bytes
    # 縮小によって収めたこと（解像度が初期値より下がっている）
    assert parts[0].page_logs[0].resolution_dpi < motfax.resolution_dpi_steps[0]


def test_split_single_page_document_raises_when_floor_insufficient(motfax, sample_bytes):
    """1ページのみの文書で、下限まで縮小しても収まらない場合はエラーとなること。"""
    data = _single_page_pdf(sample_bytes)
    options = ConvertOptions(mode="binarize")
    result = convert(data, motfax, options)
    impossible = replace(motfax, max_file_size_bytes=1000)
    with pytest.raises(SizeLimitExceededError):
        split_by_capacity(data, impossible, options, result)


def test_split_grayscale_uses_jpeg_quality_steps_for_oversized_page(motfax, sample_bytes):
    """グレースケール時の単体超過ページは、JPEG品質の段階を使って縮小されること。"""
    data = sample_bytes("sample07_color_photo.pdf")
    options = ConvertOptions(mode="grayscale")
    result = convert(data, motfax, options)
    page_size = len(result.page_images[0])

    tiny = replace(motfax, max_file_size_bytes=page_size // 2)
    calls = []
    parts = split_by_capacity(data, tiny, options, result,
                               on_page_reduced=lambda *args: calls.append(args))

    for part in parts:
        assert len(part.build_pdf()) <= tiny.max_file_size_bytes
    # 縮小はJPEG品質の段階（プロファイル定義値）を経由していること
    assert len(calls) >= 1
    assert any(quality is not None and quality in motfax.jpeg_quality_steps
               for *_rest, quality in calls)
