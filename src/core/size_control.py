# -*- coding: utf-8 -*-
"""ページ単位の適応的容量制御（F-13）。

出力が選択中プロファイルの容量上限を超えた場合、容量超過時の処理方式（F-26で選択）に
従って対処する。
- 「解像度を下げて再実行」（enforce_size_limit）：容量超過の原因ページ（出力サイズの
  大きいページ）のみを対象に、二値化時は解像度を、グレースケール時はJPEG品質→解像度の
  順に段階的に低減する。原因でないページの解像度・品質は変更しない。
- 「分割して再実行」（split_by_capacity）：画質は落とさず、ページ順を維持したまま
  容量上限に収まる単位で複数のPDFに分割する。1ページ単体で上限を超える場合のみ、
  そのページを段階的に縮小する。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Literal, NoReturn, Optional

from config.profiles import Profile
from core import convert
from core.convert import ConversionResult, ConvertOptions, PageConversionLog

RetryCallback = Callable[[int, int, int, int, Optional[int]], None]
ReductionStrategy = Literal["culprit_only", "uniform"]
CapacityExceededMethod = Literal["reduce_resolution", "split"]


class SizeLimitExceededError(Exception):
    """全ページが下限まで低減してもなお容量上限を下回らない場合のエラー。"""


def format_bytes_as_size(size_bytes: int) -> str:
    """バイト数を利用者向けの読みやすい単位（MB／KB）の文字列に整形する。

    容量上限に関する利用者向けメッセージはすべてこの表記に統一する（gui.appも本関数を使う）。
    """
    if size_bytes % (1024 * 1024) == 0:
        return f"{size_bytes // (1024 * 1024)}MB"
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f}MB"
    return f"{size_bytes // 1024}KB"


@dataclass
class _PageState:
    """1ページ分の削減進捗。"""

    reduction_levels: list[tuple[int, Optional[int]]]
    level_index: int = 0

    @property
    def can_reduce_further(self) -> bool:
        return self.level_index < len(self.reduction_levels)


def _build_reduction_levels(profile: Profile, mode: str,
                            initial_quality_index: int) -> list[tuple[int, Optional[int]]]:
    """ページに適用しうる削減段階を、適用順の(dpi, jpeg_quality)リストとして構築する。

    グレースケール時はJPEG品質を初期値より下の段階まで使い切ってから、
    解像度を段階的に下げる（各解像度段階では最低品質を用いる）。
    二値化時は解像度のみを段階的に下げる。
    """
    dpi_steps = profile.resolution_dpi_steps
    if mode == "grayscale":
        quality_steps = profile.jpeg_quality_steps
        levels: list[tuple[int, Optional[int]]] = [
            (dpi_steps[0], quality_steps[q])
            for q in range(initial_quality_index + 1, len(quality_steps))
        ]
        levels.extend((dpi, quality_steps[-1]) for dpi in dpi_steps[1:])
        return levels
    return [(dpi, None) for dpi in dpi_steps[1:]]


def _render_page(pdf_bytes: bytes, page_index: int, profile: Profile, options: ConvertOptions,
                  dpi: int, jpeg_quality: Optional[int]) -> tuple[bytes, PageConversionLog]:
    """指定ページのみを指定dpi・品質で再レンダリングする。"""
    with convert.PDFIUM_LOCK:
        return _render_page_locked(pdf_bytes, page_index, profile, options, dpi, jpeg_quality)


def _render_page_locked(pdf_bytes: bytes, page_index: int, profile: Profile,
                         options: ConvertOptions, dpi: int, jpeg_quality: Optional[int]
                         ) -> tuple[bytes, PageConversionLog]:
    doc = convert.open_document(pdf_bytes)
    try:
        page = doc[page_index]
        width_mm, height_mm = convert.get_page_size_mm(page)
        image = convert.rasterize_page(page, dpi)
        normalization = (
            convert.normalize_page(image, width_mm, height_mm, dpi, profile)
            if options.normalize_enabled
            else convert.PageNormalization(image, width_mm, height_mm, None, False, False, 1.0)
        )

        if options.mode == "binarize":
            bw, dither_used = convert.binarize_page(normalization.image, options.dither_method)
            encoded = convert.encode_binarized_page(bw, dpi)
            log = PageConversionLog(
                page_index=page_index, original_width_mm=width_mm, original_height_mm=height_mm,
                normalized=normalization.scaled, rotated=normalization.rotated,
                scale_ratio=normalization.scale_ratio, dither_method=dither_used,
                resolution_dpi=dpi, jpeg_quality=None,
            )
        else:
            gray = convert.grayscale_page(normalization.image)
            quality = jpeg_quality if jpeg_quality is not None else profile.jpeg_quality_default
            encoded = convert.encode_grayscale_page(gray, dpi, quality)
            log = PageConversionLog(
                page_index=page_index, original_width_mm=width_mm, original_height_mm=height_mm,
                normalized=normalization.scaled, rotated=normalization.rotated,
                scale_ratio=normalization.scale_ratio, dither_method=None,
                resolution_dpi=dpi, jpeg_quality=quality,
            )
        return encoded, log
    finally:
        doc.close()


def build_size_exceeded_suggestions(options: ConvertOptions) -> str:
    """容量超過時の対処案内文言を組み立てる（F-13）。

    二値化時は、写真調ページとの相性が悪いG4圧縮に起因することが多いため、
    代替手段を案内する（誤差拡散はG4との相性が悪くサイズが膨張しやすい）。
    いずれの処理方式でも対処しきれず超過が解消しない場合のエラー文言
    （_raise_size_limit_exceeded）で使う。
    """
    suggestions = ""
    if options.mode == "binarize":
        if options.dither_method != "threshold":
            # 「自動」「誤差拡散」時は、単純閾値に固定すれば（画質は犠牲になるが）
            # G4圧縮が効きやすくなりサイズを縮小できる可能性がある。
            suggestions += (
                "ディザ方式を「単純閾値」に切り替えると、画質は犠牲になりますが"
                "ファイルサイズを小さくできる場合があります。"
            )
        suggestions += (
            "グレースケールを用いると、ファイルサイズを小さくできる場合がありますが、"
            "サービス側で黒つぶれ・白飛びが起こる場合があります。"
        )
    return suggestions


def _raise_size_limit_exceeded(profile: Profile, options: ConvertOptions,
                                page_index: Optional[int] = None) -> NoReturn:
    """全ページ（または1ページ単体）が下限まで削減してもなお超過する場合のエラーを送出する。

    page_indexを指定した場合（split_by_capacityが1ページ単体を縮小してもなお
    そのページ単独で上限を超える場合）は、当該ページを名指しし、既に分割を
    試みた末の失敗であることが伝わる文言にする（「ファイルを分割してください」
    という、全ページ文脈の提案は意味をなさないため）。
    """
    suggestions = build_size_exceeded_suggestions(options)
    limit_text = format_bytes_as_size(profile.max_file_size_bytes)
    if page_index is not None:
        raise SizeLimitExceededError(
            f"{page_index + 1}ページ目を最小設定まで縮小しても、このページ単独で"
            f"{profile.display_name}の容量上限（{limit_text}）を下回りませんでした。"
            f"これ以上分割してもこのページ単体では収まらないため、{suggestions}"
        )
    raise SizeLimitExceededError(
        f"すべてのページを最小設定まで縮小しても、{profile.display_name}の容量上限"
        f"（{limit_text}）を下回りませんでした。"
        f"ファイルを分割するなどの対応をご検討ください。{suggestions}"
    )


def enforce_size_limit(pdf_bytes: bytes, profile: Profile, options: ConvertOptions,
                        result: ConversionResult, on_retry: Optional[RetryCallback] = None,
                        strategy: ReductionStrategy = "culprit_only") -> ConversionResult:
    """出力が容量上限を超えている場合、ページ単位で段階的に削減する（F-13）。

    上限以下の場合は何もせずresultをそのまま返す。全ページが下限に達しても
    なお超過する場合はSizeLimitExceededErrorを送出する。

    容量の収束判定は、PDFコンテナのオーバーヘッドも含めた実際の最終PDFバイト数
    （build_pdf後のサイズ）で行う（ページ画像バイト数の単純合計では、
    最終PDFが上限をわずかに超過したまま「成功」と誤判定する場合があるため）。
    削減対象ページの選定（どのページが容量超過の主因か）は、
    毎回のPDF再構成コストを避けるためページ画像バイト数で行う。

    strategyは削減方式（F-26。既定設定で選択）：
    - "culprit_only"（既定）：容量超過の原因ページ（現時点で最も大きいページ）のみを
      段階的に削減する。原因でないページは画質を落とさない。原因ページが1ページに
      集中している場合、そのページだけで解像度・品質を大きく下げる必要が生じうる。
    - "uniform"：削減可能な全ページを1段階ずつ均等に削減する。原因ページの下げ幅は
      緩和されるが、原因でないページも画質が落ちる。

    on_retryが指定された場合、ページ単位の削減を適用するたびに
    (ページ番号0始まり, 削減前バイト数, 削減後バイト数, 適用dpi, 適用JPEG品質)で呼び出す
    （GUIの処理ログ表示用。F-17・F-24）。
    """
    page_images = list(result.page_images)
    page_logs = list(result.page_logs)

    def total_size() -> int:
        return len(convert.build_pdf(tuple(page_images)))

    if total_size() <= profile.max_file_size_bytes:
        return result

    initial_quality = options.jpeg_quality or profile.jpeg_quality_default
    initial_quality_index = (
        profile.jpeg_quality_steps.index(initial_quality)
        if initial_quality in profile.jpeg_quality_steps else 0
    )
    states = [
        _PageState(reduction_levels=_build_reduction_levels(profile, options.mode, initial_quality_index))
        for _ in page_logs
    ]

    def reduce_page(i: int) -> None:
        state = states[i]
        next_dpi, next_quality = state.reduction_levels[state.level_index]
        state.level_index += 1

        old_size = len(page_images[i])
        encoded, log = _render_page(pdf_bytes, i, profile, options, next_dpi, next_quality)
        page_images[i] = encoded
        page_logs[i] = log

        if on_retry is not None:
            on_retry(i, old_size, len(encoded), next_dpi, next_quality)

    while total_size() > profile.max_file_size_bytes:
        candidates = [i for i, s in enumerate(states) if s.can_reduce_further]
        if not candidates:
            _raise_size_limit_exceeded(profile, options)

        if strategy == "uniform":
            # 削減可能な全ページを1段階ずつ均等に削減する（原因ページへの集中を避ける）。
            for i in candidates:
                reduce_page(i)
        else:
            # 現時点で最も大きいページ（容量超過の主因）のみを削減する。
            reduce_page(max(candidates, key=lambda i: len(page_images[i])))

    return replace(result, page_images=tuple(page_images), page_logs=tuple(page_logs))


def split_by_capacity(pdf_bytes: bytes, profile: Profile, options: ConvertOptions,
                       result: ConversionResult,
                       on_page_reduced: Optional[RetryCallback] = None) -> list[ConversionResult]:
    """出力を複数のPDFに分割することで容量上限を満たす（F-13の「分割して再実行」）。

    ページの順序を維持したまま、各パートの最終PDFバイト数が容量上限以下になるよう
    先頭から貪欲に束ねる（既存の解像度・品質はそのまま維持し、画質は落とさない）。
    全体が既に容量上限以下の場合はパート分けせず1件のみ返す（従来どおり単一ファイル出力）。

    1ページ単体で容量上限を超える場合（情報量の多いページが単独でも収まらない場合）は、
    そのページのみ既存の段階的削減（enforce_size_limitと同じ_build_reduction_levels）を
    適用して縮小してから確定する。全段階を尽くしてもそのページ単体で収まらない場合は
    SizeLimitExceededErrorを送出する（画質を落としても収まらない、という真の限界のため）。

    on_page_reducedが指定された場合、1ページ単体の縮小が発生するたびに
    (ページ番号0始まり, 縮小前バイト数, 縮小後バイト数, 適用dpi, 適用JPEG品質)で呼び出す。

    容量判定は、ページ画像バイト数の合計ではなく、PDFコンテナのオーバーヘッドを含む
    実際のPDFバイト数（build_pdf後のサイズ）で行う。このためパート構築のたびに
    PDFを実構築し、計算量はページ数の2乗に比例するが、上限判定の正確さ
    （enforce_size_limitと同じ方針。C-4）を優先する。FAX用途の文書は
    ページ数上限（秒速FAX送信=10ページ、MOVFAX=255ページ等）の範囲であり許容できる。
    """
    page_images = list(result.page_images)
    page_logs = list(result.page_logs)
    limit = profile.max_file_size_bytes

    def part_size(indices: list[int]) -> int:
        return len(convert.build_pdf(tuple(page_images[i] for i in indices)))

    if part_size(list(range(len(page_images)))) <= limit:
        return [result]

    initial_quality = options.jpeg_quality or profile.jpeg_quality_default
    initial_quality_index = (
        profile.jpeg_quality_steps.index(initial_quality)
        if initial_quality in profile.jpeg_quality_steps else 0
    )

    def shrink_single_page(i: int) -> None:
        state = _PageState(
            reduction_levels=_build_reduction_levels(profile, options.mode, initial_quality_index))
        while part_size([i]) > limit:
            if not state.can_reduce_further:
                _raise_size_limit_exceeded(profile, options, page_index=i)
            next_dpi, next_quality = state.reduction_levels[state.level_index]
            state.level_index += 1

            old_size = len(page_images[i])
            encoded, log = _render_page(pdf_bytes, i, profile, options, next_dpi, next_quality)
            page_images[i] = encoded
            page_logs[i] = log

            if on_page_reduced is not None:
                on_page_reduced(i, old_size, len(encoded), next_dpi, next_quality)

    parts: list[list[int]] = []
    current: list[int] = []
    for i in range(len(page_images)):
        if part_size([i]) > limit:
            shrink_single_page(i)
        trial = current + [i]
        if current and part_size(trial) > limit:
            parts.append(current)
            current = [i]
        else:
            current = trial
    if current:
        parts.append(current)

    return [
        replace(result, page_images=tuple(page_images[i] for i in indices),
                page_logs=tuple(page_logs[i] for i in indices))
        for indices in parts
    ]
