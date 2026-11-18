# -*- coding: utf-8 -*-
"""圧縮方式「何もしない」のときのページ番号（F-11・F-37。Ver210。2026-11-18 班長指示）。

「何もしない」は、ページを画像にせず、材料のページをPDFのまま保存する。このときも、ページ番号は
入れられるようにする（VA FAX で番号を入れて保存したファイルのページを入れ替えたときに、画像を
二重に変換せずに番号を付け直せるように）。ページの中身には手を付けず、番号だけを重ねる。

- 位置・大きさは、画像にする変換（convert.stamp_page_number）と同じ：ページの上端の中央（用紙の
  端から5mm）、10ポイント。下地を白く塗る（前に入れた番号を隠す）。
- 字は、OS のフォント（Arial 等）を PDF に埋め込む（受け手の環境で字が変わらないように。
  見つからなければ PDF の標準の Helvetica）。
- 「何もしない」のときの用紙正規化は、行わない（ページの中身を新しいページに移し替えることになり、
  リンク・注釈が落ちる、表示の範囲の外の部分が出る等のおそれがあるため。2026-11-18 班長決定）。

メモリ内で行い、一時ファイルを作らない（N-06）。Tk に依存しない。
"""
from __future__ import annotations

import io
import threading
from pathlib import Path
from typing import Optional

import pypdf
from pypdf import PageObject, Transformation

from core.convert import MM_PER_INCH, PAGE_NUMBER_POINTS, PAGE_NUMBER_TOP_MM, page_number_text

_PT_PER_MM = 72.0 / MM_PER_INCH
# 番号の字（画像にする変換と同じ順。数字と「/」だけを使う）
_FONT_CANDIDATES = (
    (r"C:\Windows\Fonts\arial.ttf", 0),
    (r"C:\Windows\Fonts\msgothic.ttc", 0),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", 0),
    ("/Library/Fonts/Arial.ttf", 0),
)
_FONT_NAME = "VAFaxNumber"
_registered_font: Optional[str] = None
_FONT_LOCK = threading.Lock()   # プレビューの描画と保存のスレッドから、同時に登録しないように
# 番号の下地の白い部分の幅は、少なくともこの文字列の幅にする（前に入れた番号が、桁の違いで
# はみ出して見えないように。convert.stamp_page_number と同じ）
_GROUND_MIN_TEXT = "000/000"


def _font() -> str:
    """番号に使うフォントの名前（初めて呼んだときに登録する。埋め込める TrueType を優先する）。"""
    global _registered_font
    with _FONT_LOCK:
        if _registered_font is None:
            _registered_font = _register_font()
        return _registered_font


def _register_font() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for path, index in _FONT_CANDIDATES:
        if not Path(path).exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont(_FONT_NAME, path, subfontIndex=index))
            return _FONT_NAME
        except Exception:  # noqa: BLE001 - 読めないフォントは、とばして次を試す
            continue
    return "Helvetica"


def _box(page: PageObject) -> tuple[float, float, float, float]:
    """表示される範囲（cropbox）の左下・右上（ポイント）。"""
    box = page.cropbox
    return float(box.left), float(box.bottom), float(box.right), float(box.top)


def _number_overlay(width: float, height: float, text: str) -> PageObject:
    """番号だけを書いたページ（ページと同じ大きさ。重ねて使う）。"""
    from reportlab.pdfbase.pdfmetrics import getAscentDescent, stringWidth
    from reportlab.pdfgen import canvas

    font = _font()
    size = PAGE_NUMBER_POINTS
    text_width = stringWidth(text, font, size)
    ground_width = max(text_width, stringWidth(_GROUND_MIN_TEXT, font, size))
    _ascent, descent = getAscentDescent(font, size)
    digit_height = size * 0.72          # 数字の高さ（字の大きさのおよそ0.72倍）
    baseline = height - PAGE_NUMBER_TOP_MM * _PT_PER_MM - digit_height
    pad = max(2.0, size / 4)
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(width, height))
    pdf.setTitle("")
    pdf.setFillColorRGB(1, 1, 1)
    pdf.rect((width - ground_width) / 2 - pad, baseline + descent - pad,
             ground_width + 2 * pad, digit_height - descent + 2 * pad, stroke=0, fill=1)
    pdf.setFillColorRGB(0, 0, 0)
    pdf.setFont(font, size)
    pdf.drawCentredString(width / 2, baseline, text)
    pdf.showPage()
    pdf.save()
    return pypdf.PdfReader(io.BytesIO(buffer.getvalue())).pages[0]


def stamp_numbers(pdf_bytes: bytes, only_page: Optional[int] = None) -> bytes:
    """各ページの上端の中央に、通し番号（例：1/12）を重ねたPDFを作る。

    ページの回転の属性は、番号が表示どおりの向きで入るよう、中身に移してから重ねる。
    文書情報（作成者など）は、元のまま残す（「何もしない」は、ページをそのまま写すため）。
    only_page を指定すると、そのページ（0始まり）だけのPDFを返す（プレビュー用。番号は文書全体での
    通し番号）。
    """
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    total = len(reader.pages)
    writer = pypdf.PdfWriter()
    for index in (range(total) if only_page is None else [only_page]):
        page = writer.add_page(reader.pages[index])
        if page.rotation % 360:
            page.transfer_rotation_to_content()
        left, bottom, right, top = _box(page)
        overlay = _number_overlay(right - left, top - bottom, page_number_text(index + 1, total))
        page.merge_transformed_page(overlay, Transformation().translate(left, bottom))
    if reader.metadata:
        writer.add_metadata({k: v for k, v in reader.metadata.items() if isinstance(v, str)})
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
