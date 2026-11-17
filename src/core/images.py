# -*- coding: utf-8 -*-
"""画像（JPEG・PNG）を、1ページのPDFにする（F-34）。

・JPEGは再圧縮せず、そのまま載せる（画質を変えない）。ただし、位置情報等のメタデータ
  （Exif・XMP・コメント）の入った区画は、PDFに載せる前に取り除く（圧縮済みの画像のデータには触れない）。
・PNGは、画素を取り出して、画質を損なわない圧縮（Flate）で載せる。透けている所は白地にする。
・画像の向きの情報（Exif の Orientation）は、画素を並べ替えずに、PDFの上での置き方で反映する。
・ページの大きさは、画像に解像度の情報（dpi）があり、その実寸が用紙に収まるときは実寸、
  それ以外は、用紙（縦置き・横置きのうち画像の縦横に合う方）に、縦横比を保って収まる最大の大きさとする。
・すべてメモリ内で行い、一時ファイルを作らない（N-06）。
"""
from __future__ import annotations

import io
import zlib

import pypdf
from PIL import Image, UnidentifiedImageError
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, NameObject,
                           NumberObject)

MM_PER_INCH = 25.4
POINTS_PER_INCH = 72.0
# これより小さい解像度の情報は、実寸の根拠にしない（0や1が入っている画像がある）
_MIN_TRUSTED_DPI = 30.0
_EXIF_ORIENTATION_TAG = 0x0112

SUPPORTED_FORMATS = ("JPEG", "PNG")


class ImageLoadError(Exception):
    """画像を読めない・対応していない形式である場合のエラー（F-20）。"""


def is_pdf(data: bytes) -> bool:
    """PDFのデータかどうか（先頭付近の「%PDF-」で見分ける）。"""
    return b"%PDF-" in data[:1024]


def strip_jpeg_metadata(data: bytes) -> bytes:
    """JPEGから、メタデータの区画（Exif・XMP・コメント等）を取り除く。画像のデータは変えない。

    残すのは、JFIF（APP0）・色の情報（APP2〔ICCプロファイル〕・APP14〔Adobe。CMYKの解釈に要る〕）と、
    画像そのものに要る区画だけとする。区画の並びを読めないときは、そのまま返す。
    """
    if data[:2] != b"\xff\xd8":
        return data
    out = bytearray(b"\xff\xd8")
    pos = 2
    size = len(data)
    while pos + 4 <= size:
        if data[pos] != 0xFF:
            return data
        marker = data[pos + 1]
        if marker == 0xFF:  # 区画の間の詰め物
            pos += 1
            continue
        if marker == 0xDA:  # SOS：ここから後ろは画像のデータ。そのまま写す
            out += data[pos:]
            return bytes(out)
        if marker in (0x01,) or 0xD0 <= marker <= 0xD7:  # 長さを持たない区画
            out += data[pos:pos + 2]
            pos += 2
            continue
        length = int.from_bytes(data[pos + 2:pos + 4], "big")
        if length < 2 or pos + 2 + length > size:
            return data
        is_app = 0xE0 <= marker <= 0xEF
        keep = not (marker == 0xFE or (is_app and marker not in (0xE0, 0xE2, 0xEE)))
        if keep:
            out += data[pos:pos + 2 + length]
        pos += 2 + length
    return data


def _orientation(image: Image.Image) -> int:
    try:
        value = image.getexif().get(_EXIF_ORIENTATION_TAG, 1)
    except Exception:
        return 1
    return value if value in range(1, 9) else 1


def _placement_matrix(orientation: int, width: float, height: float) -> tuple[float, ...]:
    """画像を、向きの情報のとおりに、幅width・高さheightのページいっぱいに置く行列（cm）。"""
    w, h = width, height
    return {
        1: (w, 0, 0, h, 0, 0),
        2: (-w, 0, 0, h, w, 0),      # 左右を反転
        3: (-w, 0, 0, -h, w, h),     # 180°
        4: (w, 0, 0, -h, 0, h),      # 上下を反転
        5: (0, -h, -w, 0, w, h),     # 対角で反転
        6: (0, -h, w, 0, 0, h),      # 右に90°
        7: (0, h, w, 0, 0, 0),       # 逆の対角で反転
        8: (0, h, -w, 0, w, 0),      # 左に90°
    }[orientation]


def _page_size_pt(shown_px: tuple[int, int], dpi: tuple[float, float] | None,
                  paper_mm: tuple[float, float]) -> tuple[float, float]:
    """ページの大きさ（pt）を決める。shown_pxは、向きを直した後の画素数。"""
    short_mm, long_mm = sorted(paper_mm)
    landscape = shown_px[0] > shown_px[1]
    paper_w = (long_mm if landscape else short_mm) / MM_PER_INCH * POINTS_PER_INCH
    paper_h = (short_mm if landscape else long_mm) / MM_PER_INCH * POINTS_PER_INCH
    if dpi is not None:
        width = shown_px[0] / dpi[0] * POINTS_PER_INCH
        height = shown_px[1] / dpi[1] * POINTS_PER_INCH
        if width <= paper_w + 0.5 and height <= paper_h + 0.5:
            return width, height
    scale = min(paper_w / shown_px[0], paper_h / shown_px[1])
    return shown_px[0] * scale, shown_px[1] * scale


def _trusted_dpi(image: Image.Image, orientation: int) -> tuple[float, float] | None:
    raw = image.info.get("dpi")
    try:
        dpi_x, dpi_y = float(raw[0]), float(raw[1])
    except (TypeError, ValueError, IndexError):
        return None
    if dpi_x < _MIN_TRUSTED_DPI or dpi_y < _MIN_TRUSTED_DPI:
        return None
    return (dpi_y, dpi_x) if orientation >= 5 else (dpi_x, dpi_y)


def _jpeg_stream(data: bytes, image: Image.Image) -> DecodedStreamObject:
    colorspace = {"L": "/DeviceGray", "RGB": "/DeviceRGB", "CMYK": "/DeviceCMYK"}.get(image.mode)
    if colorspace is None:
        raise ImageLoadError("このJPEG画像の色の形式には対応していません。")
    stream = DecodedStreamObject()
    stream.set_data(strip_jpeg_metadata(data))
    stream[NameObject("/Filter")] = NameObject("/DCTDecode")
    stream[NameObject("/ColorSpace")] = NameObject(colorspace)
    stream[NameObject("/BitsPerComponent")] = NumberObject(8)
    if image.mode == "CMYK":
        # CMYKのJPEG（Adobe形式）は、値が反転して入っている
        stream[NameObject("/Decode")] = ArrayObject([NumberObject(v) for v in (1, 0, 1, 0, 1, 0, 1, 0)])
    return stream


def _png_stream(image: Image.Image) -> DecodedStreamObject:
    image.load()
    has_alpha = image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info
    if has_alpha:
        rgba = image.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        pixels = flat
    elif image.mode in ("1", "L", "I", "I;16", "F"):
        pixels = image.convert("L")
    else:
        pixels = image.convert("RGB")
    if pixels.mode == "RGB":
        # 3色の値がどの画素でも同じ（無彩色）なら、グレーで持つ（大きさを抑える）
        r, g, b = pixels.split()
        if r.tobytes() == g.tobytes() == b.tobytes():
            pixels = r
    stream = DecodedStreamObject()
    stream.set_data(zlib.compress(pixels.tobytes(), 6))
    stream[NameObject("/Filter")] = NameObject("/FlateDecode")
    stream[NameObject("/ColorSpace")] = NameObject("/DeviceGray" if pixels.mode == "L" else "/DeviceRGB")
    stream[NameObject("/BitsPerComponent")] = NumberObject(8)
    return stream


def image_to_pdf(data: bytes, paper_mm: tuple[float, float]) -> bytes:
    """画像（JPEG・PNG）のデータを、1ページのPDFにする。

    paper_mmは、解像度の情報がない・実寸が収まらないときに合わせる用紙の大きさ
    （プロファイルの正規化先。F-29により、呼び出し側がプロファイルから与える）。
    """
    try:
        image = Image.open(io.BytesIO(data))
        image_format = image.format
        width_px, height_px = image.size
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageLoadError("画像を読み込めませんでした。ファイルが壊れているか、対応していない形式です。") from exc
    if image_format not in SUPPORTED_FORMATS or width_px <= 0 or height_px <= 0:
        raise ImageLoadError("対応している画像は、JPEGとPNGです。")

    try:
        orientation = _orientation(image)
        dpi = _trusted_dpi(image, orientation)
        stream = _jpeg_stream(data, image) if image_format == "JPEG" else _png_stream(image)
    except ImageLoadError:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ImageLoadError("画像を読み込めませんでした。ファイルが壊れている可能性があります。") from exc
    finally:
        image.close()

    stream[NameObject("/Type")] = NameObject("/XObject")
    stream[NameObject("/Subtype")] = NameObject("/Image")
    stream[NameObject("/Width")] = NumberObject(width_px)
    stream[NameObject("/Height")] = NumberObject(height_px)

    shown_px = (height_px, width_px) if orientation >= 5 else (width_px, height_px)
    page_width, page_height = _page_size_pt(shown_px, dpi, paper_mm)

    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=page_width, height=page_height)
    matrix = " ".join(f"{v:.4f}" for v in _placement_matrix(orientation, page_width, page_height))
    content = DecodedStreamObject()
    content.set_data(f"q {matrix} cm /Im0 Do Q".encode("ascii"))
    # ストリームは間接オブジェクトでなければならない（pypdfに、公開の登録の口はない）
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): writer._add_object(stream)})})
    page[NameObject("/Contents")] = writer._add_object(content)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()
