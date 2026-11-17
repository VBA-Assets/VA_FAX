# -*- coding: utf-8 -*-
"""文書（ページの表）とページの編集（F-01・F-33）、画像の取り込み（F-34）のテスト。

画面は起動しない。材料は samples/ の生成済みサンプルPDFと、テストの中で作る画像を使う。
用紙の大きさ（A4等）は、プロファイル経由で与える（F-29）。
"""
from __future__ import annotations

import io

import pypdf
import pytest
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageStat

from core import convert, images
from core.document import (CorruptedSourceError, Document, EncryptedSourceError, load_source,
                           move_indices)


@pytest.fixture
def paper(motfax):
    target = motfax.normalize_target
    return (target.width_mm, target.height_mm)


@pytest.fixture
def load(sample_bytes, paper):
    def _load(name: str):
        return load_source(sample_bytes(name), paper)
    return _load


def _page_sizes(pdf: bytes) -> list[tuple[int, int]]:
    """各ページの大きさ（mm。回転の属性を反映したもの）。"""
    with convert.PDFIUM_LOCK:
        doc = convert.open_document(pdf)
        try:
            return [tuple(round(v) for v in convert.get_page_size_mm(doc[i])) for i in range(len(doc))]
        finally:
            doc.close()


def _render(pdf: bytes, page: int = 0, dpi: int = 50) -> Image.Image:
    with convert.PDFIUM_LOCK:
        doc = convert.open_document(pdf)
        try:
            return convert.rasterize_page(doc[page], dpi)
        finally:
            doc.close()


def _similar(a: Image.Image, b: Image.Image) -> bool:
    """2つの絵が同じ見た目か。画素数の端数の丸めによる1画素ほどのずれは、違いに数えない
    （少しぼかしてから比べる）。"""
    if abs(a.width - b.width) > 1 or abs(a.height - b.height) > 1:
        return False
    box = (0, 0, min(a.width, b.width), min(a.height, b.height))
    blur = ImageFilter.GaussianBlur(3)
    diff = ImageChops.difference(a.crop(box).convert("L").filter(blur), b.crop(box).convert("L").filter(blur))
    return ImageStat.Stat(diff).mean[0] < 1.5


def _refs(doc: Document) -> list[tuple[int, int, int]]:
    return [(r.source, r.page, r.rotation) for r in doc.pages]


# ---------------------------------------------------------------- 材料の読み込み

def test_load_pdf_source(load):
    source = load("sample01_a4_text.pdf")
    assert (source.kind, source.page_count, source.made_by_va_fax) == ("pdf", 3, False)


def test_encrypted_and_corrupted_pdf_are_rejected(load, corrupted_pdf_bytes, paper):
    with pytest.raises(EncryptedSourceError):
        load("sample11_encrypted.pdf")
    with pytest.raises(CorruptedSourceError):
        load_source(corrupted_pdf_bytes, paper)
    with pytest.raises(CorruptedSourceError):
        load_source(b"this is not a pdf nor an image", paper)


def test_file_saved_by_this_tool_is_recognized(sample_bytes, motfax, paper):
    """二値化・グレースケールで作ったPDFには印があり、開いたときに見分けられる（F-02・F-06）。"""
    result = convert.convert(sample_bytes("sample01_a4_text.pdf"), motfax,
                             convert.ConvertOptions(mode="binarize"))
    source = load_source(result.build_pdf(), paper)
    assert source.made_by_va_fax
    doc = Document()
    doc.add_source(source)
    assert doc.all_made_by_va_fax()
    doc.add_source(load_source(sample_bytes("sample02_b4_text.pdf"), paper))
    assert not doc.all_made_by_va_fax()


# ---------------------------------------------------------------- ページの編集

def test_untouched_single_source_is_returned_as_is(load, sample_bytes):
    """開いただけの文書は、開いたファイルとバイト単位で同じ。"""
    doc = Document()
    doc.add_source(load("sample01_a4_text.pdf"))
    assert doc.is_untouched_single_source()
    assert doc.assemble() == sample_bytes("sample01_a4_text.pdf")


def test_add_inserts_pages_at_boundary(load):
    doc = Document()
    doc.add_source(load("sample01_a4_text.pdf"))          # A4×3
    added = doc.add_source(load("sample02_b4_text.pdf"), at=1)  # B4×2 を、1ページ目の後ろへ
    assert list(added) == [1, 2]
    assert _refs(doc) == [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 2, 0)]
    assert _page_sizes(doc.assemble()) == [(210, 297), (257, 364), (257, 364), (210, 297), (210, 297)]
    assert doc.used_source_count() == 2
    assert not doc.is_untouched_single_source()


def test_add_several_sources_is_one_undo_step(load):
    doc = Document()
    doc.add_source(load("sample01_a4_text.pdf"))
    doc.add_sources([load("sample02_b4_text.pdf"), load("sample03_a3_text.pdf")])
    assert doc.page_count == 6
    assert doc.undo()
    assert doc.page_count == 3


def test_delete_pages_and_keep_at_least_one(load):
    doc = Document()
    doc.add_source(load("sample01_a4_text.pdf"))
    assert doc.delete([0, 2])
    assert _refs(doc) == [(0, 1, 0)]
    assert len(pypdf.PdfReader(io.BytesIO(doc.assemble())).pages) == 1
    assert not doc.delete([0])          # 最後の1ページは削除できない
    assert doc.page_count == 1
    assert not doc.delete([5])          # 範囲の外は何もしない


@pytest.mark.parametrize("selected, drop, expected, start", [
    ([0], 3, [1, 2, 0, 3], 2),          # 1つを下へ
    ([2], 0, [2, 0, 1, 3], 0),          # 1つを上へ
    ([0], 4, [1, 2, 3, 0], 3),          # 末尾へ
    ([1, 2], 0, [1, 2, 0, 3], 0),       # 塊を上へ
    ([0, 2], 4, [1, 3, 0, 2], 2),       # 離れた2つを末尾へ（順序は保つ）
    ([1, 2], 2, [0, 1, 2, 3], 1),       # 塊の中へ落とす（変わらない）
])
def test_move_indices(selected, drop, expected, start):
    assert move_indices(4, selected, drop) == (expected, start)


def test_move_pages(load):
    doc = Document()
    doc.add_source(load("sample06_mixed_sizes.pdf"))
    doc.clear_history()                 # 開いた直後（開く前には戻さない）
    before = _page_sizes(doc.assemble())
    moved = doc.move([0], 4)
    assert list(moved) == [3]
    assert _page_sizes(doc.assemble()) == before[1:] + before[:1]
    assert doc.move([3], 4) is None     # 動かない並べ替えは、履歴に積まない
    assert doc.undo() and not doc.can_undo


def test_rotate_changes_only_the_attribute(load):
    doc = Document()
    doc.add_source(load("sample01_a4_text.pdf"))
    assert doc.rotate([1], 90)
    assert _page_sizes(doc.assemble()) == [(210, 297), (297, 210), (210, 297)]
    assert doc.rotate([1], -90)
    assert _refs(doc)[1] == (0, 1, 0)
    assert doc.rotate([0, 2], 180)
    assert _page_sizes(doc.assemble()) == [(210, 297)] * 3
    # 180°回したページは、元のページを画像にして180°回したものと同じ見た目になる
    original = _render(doc.sources[0].data, 0)
    rotated = _render(doc.assemble(), 0)
    assert _similar(rotated, original.rotate(180))
    assert not _similar(rotated, original)
    with pytest.raises(ValueError):
        doc.rotate([0], 45)


def test_rotation_adds_to_existing_attribute(load):
    """材料のページが元から回転の属性を持っていても、その上に足す（横向きのサンプルで確かめる）。"""
    doc = Document()
    doc.add_source(load("sample04_a4_landscape.pdf"))
    before = _page_sizes(doc.assemble())
    doc.rotate([0], 90)
    after = _page_sizes(doc.assemble())
    assert after[0] == (before[0][1], before[0][0])
    assert after[1:] == before[1:]


def test_undo_and_redo(load):
    doc = Document()
    doc.add_source(load("sample01_a4_text.pdf"))
    opened = doc.pages
    doc.delete([0])
    doc.rotate([0], 90)
    edited = doc.pages
    assert doc.undo() and doc.undo()
    assert doc.pages == opened
    assert doc.redo() and doc.redo() and not doc.redo()
    assert doc.pages == edited
    doc.undo()
    doc.move([0], 2)                    # 新しい編集をすると、やり直しの履歴は捨てる
    assert not doc.can_redo


def test_assembled_pdf_goes_through_convert_pipeline(load, motfax):
    """組み立てたPDFを、そのまま変換に渡せる（回したページは、回した向きで画像になる）。"""
    doc = Document()
    doc.add_source(load("sample01_a4_text.pdf"))
    doc.add_source(load("sample02_b4_text.pdf"))
    doc.delete([1])
    doc.rotate([0], 180)
    result = convert.convert(doc.assemble(), motfax, convert.ConvertOptions(mode="binarize"))
    output = result.build_pdf()
    target = motfax.normalize_target
    assert _page_sizes(output) == [(round(target.width_mm), round(target.height_mm))] * 4


# ---------------------------------------------------------------- 画像の取り込み（F-34）

def _test_image(size=(300, 200)) -> Image.Image:
    """向きの違いが分かる絵（左上に黒、右上に灰色の四角）。"""
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([10, 10, size[0] // 3, size[1] // 3], fill="black")
    draw.rectangle([size[0] * 2 // 3, 10, size[0] - 10, size[1] // 4], fill=(128, 128, 128))
    return image


def _encode(image: Image.Image, fmt: str, **kwargs) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


def test_png_becomes_one_page_and_keeps_pixels(paper):
    image = _test_image()
    pdf = images.image_to_pdf(_encode(image, "PNG", dpi=(100, 100)), paper)
    # 100dpiで300×200画素 → 3×2インチ（実寸が用紙に収まるので、実寸のページになる）
    assert _page_sizes(pdf) == [(76, 51)]
    rendered = _render(pdf, dpi=100)
    assert _similar(rendered, image)


def test_jpeg_is_embedded_without_recompression(paper):
    data = _encode(_test_image(), "JPEG", quality=90)
    pdf = images.image_to_pdf(data, paper)
    page = pypdf.PdfReader(io.BytesIO(pdf)).pages[0]
    stream = page["/Resources"]["/XObject"]["/Im0"]
    assert stream["/Filter"] == "/DCTDecode"
    assert stream._data == images.strip_jpeg_metadata(data)


def test_image_without_dpi_fits_paper(paper, motfax):
    """解像度の情報がない画像は、用紙（画像の縦横に合う向き）に収まる最大の大きさにする。"""
    target = motfax.normalize_target
    wide = images.image_to_pdf(_encode(_test_image((400, 200)), "PNG"), paper)
    (w, h), = _page_sizes(wide)
    assert w == round(target.height_mm) and abs(h - target.height_mm / 2) <= 1
    tall = images.image_to_pdf(_encode(_test_image((200, 400)), "PNG"), paper)
    (w, h), = _page_sizes(tall)
    assert h <= round(target.height_mm) and w <= round(target.width_mm)
    assert w == round(target.width_mm) or h == round(target.height_mm)


def test_image_larger_than_paper_is_fitted(paper, motfax):
    """実寸が用紙に収まらない画像（72dpiの大きい写真など）は、用紙に収める。"""
    target = motfax.normalize_target
    pdf = images.image_to_pdf(_encode(_test_image((3000, 4000)), "JPEG", dpi=(72, 72)), paper)
    (w, h), = _page_sizes(pdf)
    assert (w, h) == (round(target.width_mm), round(target.width_mm * 4 / 3))


@pytest.mark.parametrize("orientation", range(1, 9))
def test_exif_orientation_is_applied(orientation, paper):
    """向きの情報（Exif の Orientation）のとおりに置く（Pillow で向きを直した絵と比べる）。"""
    image = _test_image((240, 160))
    exif = Image.Exif()
    exif[0x0112] = orientation
    data = _encode(image, "PNG", exif=exif, dpi=(80, 80))
    expected = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    rendered = _render(images.image_to_pdf(data, paper), dpi=80)
    assert _similar(rendered, expected)
    if orientation != 1:
        assert not _similar(rendered, image.resize(rendered.size))


def test_jpeg_metadata_is_not_carried_into_pdf(paper):
    """Exif（位置情報等）やコメントは、PDFに載せない。画像のデータは変えない。"""
    exif = Image.Exif()
    exif[0x010F] = "SECRET-CAMERA-MAKER"
    data = _encode(_test_image(), "JPEG", exif=exif, comment=b"SECRET-COMMENT")
    assert b"SECRET-CAMERA-MAKER" in data and b"SECRET-COMMENT" in data
    stripped = images.strip_jpeg_metadata(data)
    assert b"SECRET" not in stripped
    assert ImageChops.difference(Image.open(io.BytesIO(stripped)).convert("RGB"),
                                 Image.open(io.BytesIO(data)).convert("RGB")).getbbox() is None
    assert b"SECRET" not in images.image_to_pdf(data, paper)


def test_transparent_png_is_put_on_white(paper):
    image = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle([20, 20, 60, 60], fill=(0, 0, 0, 255))
    rendered = _render(images.image_to_pdf(_encode(image, "PNG", dpi=(100, 100)), paper), dpi=100)
    assert rendered.getpixel((5, 5)) == (255, 255, 255)
    assert rendered.getpixel((40, 40)) == (0, 0, 0)


def test_unsupported_or_broken_image_is_rejected(paper):
    with pytest.raises(images.ImageLoadError):
        images.image_to_pdf(_encode(_test_image(), "BMP"), paper)
    with pytest.raises(images.ImageLoadError):
        images.image_to_pdf(_encode(_test_image(), "PNG")[:60], paper)


def test_image_source_joins_document_and_converts(sample_bytes, paper, motfax):
    """画像を追加した文書を、二値化して送信用のPDFにできる。"""
    doc = Document()
    doc.add_source(load_source(sample_bytes("sample01_a4_text.pdf"), paper))
    image_source = load_source(_encode(_test_image((1200, 800)), "JPEG"), paper)
    assert (image_source.kind, image_source.page_count) == ("image", 1)
    doc.add_source(image_source, at=0)
    result = convert.convert(doc.assemble(), motfax, convert.ConvertOptions(mode="binarize"))
    assert len(result.page_images) == 4
    target = motfax.normalize_target
    assert _page_sizes(result.build_pdf()) == [(round(target.width_mm), round(target.height_mm))] * 4
