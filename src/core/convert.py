# -*- coding: utf-8 -*-
"""ラスタライズ・用紙正規化・二値化／グレースケール・PDF再構成（F-07〜F-12・F-14）。

中間データ（ラスタライズ画像等）は一切ディスクに書き出さず、メモリ内（BytesIO等）で処理する（N-06）。
"""
from __future__ import annotations

import io
import threading
from dataclasses import dataclass
from typing import Callable, Literal, Optional

import img2pdf
import pypdfium2 as pdfium
from PIL import Image

from config.profiles import Profile

MM_PER_INCH = 25.4
DEFAULT_TOLERANCE_MM = 2.0

# pdfium（ネイティブライブラリ）はスレッドセーフではないため、複数スレッド
# （事前診断・プレビュー描画・変換）から同時にアクセスするとアクセス違反で
# プロセスごと落ちる。pdfiumに触れる一連の処理（open〜render〜close）は、
# このロックを取得して直列化すること。再入可能ロック（RLock）のため、
# 同一スレッド内でのネストした取得は問題ない。
PDFIUM_LOCK = threading.RLock()

BinarizeMethod = Literal["auto", "threshold", "diffusion"]
CompressionMode = Literal["binarize", "grayscale"]


class ConversionError(Exception):
    """変換処理全般のエラー。"""


class PageCountExceededError(ConversionError):
    """ページ数がプロファイルの上限を超えている場合のエラー（F-14）。"""


class UnsupportedPageSizeError(ConversionError):
    """用紙正規化OFFの状態でプロファイル対応外サイズのページが含まれる場合のエラー（F-14）。"""


@dataclass(frozen=True)
class PageNormalization:
    """1ページの用紙正規化結果（F-08）。"""

    image: Image.Image
    original_width_mm: float
    original_height_mm: float
    matched_size_name: Optional[str]
    rotated: bool
    scaled: bool
    scale_ratio: float


@dataclass(frozen=True)
class PageConversionLog:
    """1ページの処理結果（F-17処理ログ・F-25変換レポート用の構造化データ）。"""

    page_index: int
    original_width_mm: float
    original_height_mm: float
    normalized: bool
    rotated: bool
    scale_ratio: float
    dither_method: Optional[str]
    resolution_dpi: int
    jpeg_quality: Optional[int]


@dataclass(frozen=True)
class ConversionResult:
    """変換結果（PDF再構成前の中間データを保持し、size_control.pyでの再処理に使う）。"""

    page_images: tuple[bytes, ...]
    page_logs: tuple[PageConversionLog, ...]
    mode: CompressionMode

    def build_pdf(self) -> bytes:
        return build_pdf(self.page_images)


def open_document(pdf_bytes: bytes) -> pdfium.PdfDocument:
    """PDFバイト列からドキュメントを開く。"""
    try:
        return pdfium.PdfDocument(pdf_bytes)
    except pdfium.PdfiumError as exc:
        raise ConversionError(f"PDFを開けませんでした：{exc}") from exc


def get_page_size_mm(page: pdfium.PdfPage) -> tuple[float, float]:
    """ページの用紙サイズをミリメートル単位で返す（幅・高さ、向きはそのまま）。"""
    width_pt, height_pt = page.get_size()
    return width_pt / 72.0 * MM_PER_INCH, height_pt / 72.0 * MM_PER_INCH


def validate_before_convert(doc: pdfium.PdfDocument, profile: Profile, normalize_enabled: bool) -> None:
    """変換実行前の検証（F-14）。違反時はConversionErrorの派生例外を送出し、変換を行わない。"""
    page_count = len(doc)
    if profile.page_count_limit is not None and page_count > profile.page_count_limit:
        raise PageCountExceededError(
            f"{profile.display_name}のページ数上限（{profile.page_count_limit}ページ）を"
            f"超えています（{page_count}ページ）。ファイルを分割してください。"
        )

    if not normalize_enabled:
        unsupported_pages = []
        for i in range(page_count):
            width_mm, height_mm = get_page_size_mm(doc[i])
            if profile.find_supported_page_size(width_mm, height_mm) is None:
                unsupported_pages.append(i + 1)
        if unsupported_pages:
            pages_text = "、".join(str(p) for p in unsupported_pages)
            raise UnsupportedPageSizeError(
                f"{profile.display_name}の対応外サイズのページが含まれるため送信できません"
                f"（{pages_text}ページ目）。用紙の正規化を有効にしてください。"
            )


def rasterize_page(page: pdfium.PdfPage, dpi: int) -> Image.Image:
    """ページを指定dpiでビットマップ化する（F-07）。"""
    bitmap = page.render(scale=dpi / 72.0)
    return bitmap.to_pil().convert("RGB")


def _is_landscape(width_mm: float, height_mm: float) -> bool:
    return width_mm > height_mm


def _fit_and_center(image: Image.Image, target_px_w: int, target_px_h: int) -> tuple[Image.Image, float]:
    """正規化先サイズに収まるよう画像を配置する。

    正規化先サイズより大きい画像は縮小するが、小さい画像はあえて拡大しない
    （原寸のまま中央配置する）。文書の実寸を不必要に拡大する必要はないため。
    """
    scale = min(target_px_w / image.width, target_px_h / image.height, 1.0)
    new_w = max(1, round(image.width * scale))
    new_h = max(1, round(image.height * scale))
    resized = image.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGB", (target_px_w, target_px_h), "white")
    offset = ((target_px_w - new_w) // 2, (target_px_h - new_h) // 2)
    canvas.paste(resized, offset)
    return canvas, scale


def normalize_page(image: Image.Image, width_mm: float, height_mm: float, dpi: int,
                    profile: Profile, tolerance_mm: float = DEFAULT_TOLERANCE_MM) -> PageNormalization:
    """用紙正規化（プロファイル準拠。F-08）。

    プロファイルの対応用紙サイズに含まれるページは寸法を変更せずそのまま通す。
    横向きページは、プロファイルの横向き受理可否に従い、非対応の場合のみ90度回転する
    （原寸維持。縮小配置は行わない）。対応サイズに含まれないページのみ、
    正規化先サイズのキャンバスに中央配置する（アスペクト比維持）。正規化先サイズより
    大きいページは縮小するが、小さいページはあえて拡大しない（原寸のまま中央配置する）。
    """
    matched = profile.find_supported_page_size(width_mm, height_mm, tolerance_mm=tolerance_mm)
    if matched is not None:
        if _is_landscape(width_mm, height_mm) and not profile.landscape_accepted:
            rotated_image = image.rotate(-90, expand=True)
            return PageNormalization(
                image=rotated_image, original_width_mm=width_mm, original_height_mm=height_mm,
                matched_size_name=matched.name, rotated=True, scaled=False, scale_ratio=1.0,
            )
        return PageNormalization(
            image=image, original_width_mm=width_mm, original_height_mm=height_mm,
            matched_size_name=matched.name, rotated=False, scaled=False, scale_ratio=1.0,
        )

    target = profile.normalize_target
    target_px_w = round(target.width_mm / MM_PER_INCH * dpi)
    target_px_h = round(target.height_mm / MM_PER_INCH * dpi)
    resized, scale = _fit_and_center(image, target_px_w, target_px_h)
    return PageNormalization(
        image=resized, original_width_mm=width_mm, original_height_mm=height_mm,
        matched_size_name=None, rotated=False, scaled=True, scale_ratio=scale,
    )


def _luminance_extreme_ratio(gray: Image.Image, low: int = 32, high: int = 224) -> float:
    """輝度ヒストグラムのうち、低輝度・高輝度の両端に集中する画素の割合を返す。"""
    histogram = gray.histogram()
    total = sum(histogram)
    if total == 0:
        return 1.0
    extremes = sum(histogram[: low + 1]) + sum(histogram[high:])
    return extremes / total


COLOR_CHANNEL_DIFF_THRESHOLD = 12


def has_color_content(image: Image.Image, threshold: int = COLOR_CHANNEL_DIFF_THRESHOLD) -> bool:
    """画素にカラー要素（無彩色ではないもの）があるかを判定する。

    淡い色のみで印刷された罫線・見出し等（銀行帳票等に多い）も検出できるよう、
    輝度ではなくRGB各チャンネルの差で判定する（core.diagnose.classify_page_contentと共通）。
    判定は縮小画像に対して行い、処理速度を確保する。
    """
    rgb = image.convert("RGB")
    small = rgb.resize((min(rgb.width, 200), min(rgb.height, 200)))
    for r, g, b in small.get_flattened_data():
        if max(r, g, b) - min(r, g, b) > threshold:
            return True
    return False


def choose_dither_method(image: Image.Image) -> Literal["threshold", "diffusion"]:
    """色の有無・輝度分布から二値化方式を自動選択する（F-09自動判定）。

    淡い色のみで印刷された罫線・図表等を含むページは、単純閾値では色付き要素が
    白背景と判別できず消えてしまうため（輝度だけでは白に近いため）、誤差拡散
    ディザを適用する。無彩色のページは、文字・線画調（輝度が白黒二極に集中）
    には単純閾値、写真・グラデーション調には誤差拡散ディザを適用する。
    """
    if has_color_content(image):
        return "diffusion"
    gray = image.convert("L")
    return "threshold" if _luminance_extreme_ratio(gray) >= 0.6 else "diffusion"


def _otsu_threshold(gray: Image.Image) -> int:
    """大津の判別分析法による二値化閾値の算出。"""
    histogram = gray.histogram()
    total = sum(histogram)
    sum_all = sum(i * h for i, h in enumerate(histogram))
    weight_b = 0
    sum_b = 0
    best_threshold = 0
    best_variance = -1.0
    for t in range(256):
        weight_b += histogram[t]
        if weight_b == 0:
            continue
        weight_f = total - weight_b
        if weight_f == 0:
            break
        sum_b += t * histogram[t]
        mean_b = sum_b / weight_b
        mean_f = (sum_all - sum_b) / weight_f
        variance = weight_b * weight_f * (mean_b - mean_f) ** 2
        if variance > best_variance:
            best_variance = variance
            best_threshold = t
    return best_threshold


def binarize_page(image: Image.Image, method: BinarizeMethod = "auto") -> tuple[Image.Image, str]:
    """二値化（F-09）。戻り値は (二値化画像, 実際に適用した方式)。"""
    gray = image.convert("L")
    chosen = choose_dither_method(image) if method == "auto" else method

    if chosen == "threshold":
        threshold = _otsu_threshold(gray)
        # 大津法のクラス分けは「閾値以下＝黒側／閾値超＝白側」のため、比較は>とする。
        # >=にすると、既に二値化済みの画像（画素が0と255のみで閾値が0と算出される）で
        # 全画素が白になってしまう（二値化済みFAX出力を再入力した場合等）。
        bw = gray.point(lambda p: 255 if p > threshold else 0).convert("1", dither=Image.Dither.NONE)
    elif chosen == "diffusion":
        bw = gray.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    else:
        raise ValueError(f"不明な二値化方式です：{chosen}")

    return bw, chosen


def grayscale_page(image: Image.Image) -> Image.Image:
    """グレースケール変換（F-10）。JPEG圧縮はencode_grayscale_pageで行う。"""
    return image.convert("L")


def encode_binarized_page(image: Image.Image, dpi: int) -> bytes:
    """二値化画像をCCITT G4圧縮のTIFFとしてエンコードする（F-12）。"""
    buf = io.BytesIO()
    image.save(buf, format="TIFF", compression="group4", dpi=(dpi, dpi))
    return buf.getvalue()


def encode_grayscale_page(image: Image.Image, dpi: int, quality: int) -> bytes:
    """グレースケール画像をJPEG圧縮でエンコードする（F-12）。"""
    buf = io.BytesIO()
    image.convert("L").save(buf, format="JPEG", quality=quality, dpi=(dpi, dpi))
    return buf.getvalue()


def build_pdf(page_images: tuple[bytes, ...]) -> bytes:
    """エンコード済みページ画像群からPDFを再構成する（F-12）。

    各画像にDPI情報を付与しているため、img2pdfはページごとのDPIから
    物理サイズを算出する（解像度混在時も正しい用紙サイズを維持する）。
    """
    if not page_images:
        raise ConversionError("出力対象のページがありません")
    return img2pdf.convert(*page_images)


@dataclass(frozen=True)
class ConvertOptions:
    """変換オプション（GUIのチェックボックス・ラジオボタンに対応。F-11：modeは排他選択）。"""

    mode: CompressionMode
    normalize_enabled: bool = True
    dither_method: BinarizeMethod = "auto"
    jpeg_quality: Optional[int] = None
    resolution_dpi: Optional[int] = None


def convert(pdf_bytes: bytes, profile: Profile, options: ConvertOptions,
            on_page_done: Optional[Callable[[int, int, PageConversionLog], None]] = None
            ) -> ConversionResult:
    """PDF全体を変換する（F-07〜F-12のオーケストレーション、F-14の検証を含む）。

    on_page_doneが指定された場合、ページ処理完了ごとに
    (処理済ページ数, 総ページ数, 当該ページのPageConversionLog)で呼び出す
    （GUIの進捗バー・処理ログ表示用。指定しなければ従来どおり呼び出さない）。
    """
    with PDFIUM_LOCK:
        return _convert_locked(pdf_bytes, profile, options, on_page_done)


def _convert_locked(pdf_bytes: bytes, profile: Profile, options: ConvertOptions,
                    on_page_done: Optional[Callable[[int, int, PageConversionLog], None]]
                    ) -> ConversionResult:
    doc = open_document(pdf_bytes)
    try:
        validate_before_convert(doc, profile, options.normalize_enabled)

        dpi = options.resolution_dpi or profile.resolution_dpi_default
        quality = options.jpeg_quality or profile.jpeg_quality_default

        page_images: list[bytes] = []
        page_logs: list[PageConversionLog] = []
        page_count = len(doc)

        for i in range(page_count):
            page = doc[i]
            width_mm, height_mm = get_page_size_mm(page)
            image = rasterize_page(page, dpi)

            normalization = (
                normalize_page(image, width_mm, height_mm, dpi, profile)
                if options.normalize_enabled
                else PageNormalization(image, width_mm, height_mm, None, False, False, 1.0)
            )

            if options.mode == "binarize":
                bw, dither_used = binarize_page(normalization.image, options.dither_method)
                encoded = encode_binarized_page(bw, dpi)
                page_logs.append(PageConversionLog(
                    page_index=i, original_width_mm=width_mm, original_height_mm=height_mm,
                    normalized=normalization.scaled, rotated=normalization.rotated,
                    scale_ratio=normalization.scale_ratio, dither_method=dither_used,
                    resolution_dpi=dpi, jpeg_quality=None,
                ))
            elif options.mode == "grayscale":
                gray = grayscale_page(normalization.image)
                encoded = encode_grayscale_page(gray, dpi, quality)
                page_logs.append(PageConversionLog(
                    page_index=i, original_width_mm=width_mm, original_height_mm=height_mm,
                    normalized=normalization.scaled, rotated=normalization.rotated,
                    scale_ratio=normalization.scale_ratio, dither_method=None,
                    resolution_dpi=dpi, jpeg_quality=quality,
                ))
            else:
                raise ValueError(f"不明な圧縮方式です：{options.mode}")

            page_images.append(encoded)
            if on_page_done is not None:
                on_page_done(i + 1, page_count, page_logs[-1])

        return ConversionResult(
            page_images=tuple(page_images), page_logs=tuple(page_logs), mode=options.mode,
        )
    finally:
        doc.close()
