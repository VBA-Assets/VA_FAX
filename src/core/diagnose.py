# -*- coding: utf-8 -*-
"""入力PDFの事前診断（F-04）。暗号化・破損PDFの検出も行う（F-20）。

圧縮方式（二値化／グレースケール）の既定は常に二値化とし、文書内容による
自動推奨は行わない（F-06）。二値化はG4圧縮との相性がよくFAXで最も一般的な
原稿形式である一方、グレースケールは送信時にサービス側の自動変換で
黒つぶれ・白飛びが生じうるため（根拠はdocs/primary-sources.md第4.7章）。
本モジュールはページ別のカラー／グレースケール／白黒の判定結果を提供し、
ユーザー自身がグレースケールを選ぶかどうかの判断材料とする（F-04）。

ページサイズ・向きの判定は、/Rotate属性を考慮した実効サイズを返すpypdfium2側
（core.convert.get_page_size_mm）に委ね、pypdfはテキスト抽出・暗号化判定にのみ用いる
（pypdfの page.mediabox は/Rotateを反映しないため）。
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Literal, Optional

import pypdf
from PIL import Image

from config.profiles import Profile
from core import convert

PREVIEW_DPI = 72
COLOR_CHANNEL_DIFF_THRESHOLD = convert.COLOR_CHANNEL_DIFF_THRESHOLD

ContentType = Literal["color", "grayscale", "mono"]

# 表示用の一般的な用紙規格名（JIS/ISO寸法）。プロファイルの対応可否とは無関係な
# 幾何学的事実であり、サービス制約値ではないためF-29のハードコード禁止の対象外。
# 事前診断欄に「8ページ中2ページがB4です」のように規格名を示すためだけに用いる。
_KNOWN_PAPER_SIZES: tuple[tuple[str, float, float], ...] = (
    ("A3", 297.0, 420.0),
    ("A4", 210.0, 297.0),
    ("A5", 148.0, 210.0),
    ("B4", 257.0, 364.0),
    ("B5", 182.0, 257.0),
    ("レター", 215.9, 279.4),
)


def identify_paper_size_name(width_mm: float, height_mm: float,
                              tolerance_mm: float = 2.0) -> Optional[str]:
    """既知の用紙規格名を推定する（表示用。向き無視・プロファイルの対応可否とは独立）。"""
    a = sorted((width_mm, height_mm))
    for name, w, h in _KNOWN_PAPER_SIZES:
        b = sorted((w, h))
        if abs(a[0] - b[0]) <= tolerance_mm and abs(a[1] - b[1]) <= tolerance_mm:
            return name
    return None


class DiagnosisError(Exception):
    """診断処理全般のエラー。"""


class EncryptedPdfError(DiagnosisError):
    """パスワード付き（暗号化）PDFの場合のエラー（F-20）。"""


class CorruptedPdfError(DiagnosisError):
    """破損PDFの場合のエラー（F-20）。"""


@dataclass(frozen=True)
class PageDiagnosis:
    """1ページ分の診断結果。"""

    page_index: int
    width_mm: float
    height_mm: float
    is_supported_size: bool
    matched_size_name: Optional[str]
    identified_size_name: Optional[str]
    is_landscape: bool
    content_type: ContentType


@dataclass(frozen=True)
class Diagnosis:
    """PDF全体の診断結果（F-04）。"""

    page_count: int
    file_size_bytes: int
    has_text: bool
    pages: tuple[PageDiagnosis, ...]
    unsupported_pages: tuple[int, ...]
    page_count_exceeds_limit: bool


def _open_reader(pdf_bytes: bytes) -> pypdf.PdfReader:
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    except pypdf.errors.PdfReadError as exc:
        raise CorruptedPdfError(
            f"PDFファイルを読み込めませんでした。ファイルが破損している可能性があります：{exc}"
        ) from exc

    if reader.is_encrypted:
        raise EncryptedPdfError(
            "このPDFはパスワードで保護されているため処理できません。"
            "パスワードを解除したファイルをご用意のうえ、再度お試しください。"
        )
    return reader


def classify_page_content(image: Image.Image,
                           color_threshold: int = COLOR_CHANNEL_DIFF_THRESHOLD) -> ContentType:
    """ページのカラー／グレースケール／白黒の別を判定する（F-04）。

    まずRGB各チャンネルの差からカラーページか判定し（convert.has_color_content。
    淡い色のみで印刷された罫線等も検出する）、非カラーの場合は輝度分布の
    二極集中度（convert.choose_dither_method）により白黒（文字・線画調）か
    グレースケール（写真・階調調）かを判定する。
    """
    if convert.has_color_content(image, color_threshold):
        return "color"
    return "mono" if convert.choose_dither_method(image) == "threshold" else "grayscale"


def diagnose(pdf_bytes: bytes, profile: Profile, preview_dpi: int = PREVIEW_DPI) -> Diagnosis:
    """入力PDFを診断する（F-04）。暗号化・破損PDFはDiagnosisErrorの派生例外を送出する。"""
    reader = _open_reader(pdf_bytes)
    with convert.PDFIUM_LOCK:
        return _diagnose_locked(pdf_bytes, profile, preview_dpi, reader)


def _diagnose_locked(pdf_bytes: bytes, profile: Profile, preview_dpi: int,
                     reader: pypdf.PdfReader) -> Diagnosis:
    doc = convert.open_document(pdf_bytes)
    try:
        page_count = len(doc)
        pages: list[PageDiagnosis] = []
        unsupported_pages: list[int] = []

        for i in range(page_count):
            width_mm, height_mm = convert.get_page_size_mm(doc[i])
            matched = profile.find_supported_page_size(width_mm, height_mm)
            preview = convert.rasterize_page(doc[i], preview_dpi)
            content_type = classify_page_content(preview)

            if matched is None:
                unsupported_pages.append(i + 1)

            pages.append(PageDiagnosis(
                page_index=i, width_mm=width_mm, height_mm=height_mm,
                is_supported_size=matched is not None,
                matched_size_name=matched.name if matched else None,
                identified_size_name=identify_paper_size_name(width_mm, height_mm),
                is_landscape=width_mm > height_mm,
                content_type=content_type,
            ))

        has_text = any(bool((page.extract_text() or "").strip()) for page in reader.pages)

        page_count_exceeds_limit = (
            profile.page_count_limit is not None and page_count > profile.page_count_limit
        )

        return Diagnosis(
            page_count=page_count,
            file_size_bytes=len(pdf_bytes),
            has_text=has_text,
            pages=tuple(pages),
            unsupported_pages=tuple(unsupported_pages),
            page_count_exceeds_limit=page_count_exceeds_limit,
        )
    finally:
        doc.close()
