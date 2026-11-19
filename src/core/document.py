# -*- coding: utf-8 -*-
"""文書（ページの表）と、ページの編集（F-01・F-33）。

文書の中身は「ページの表」（どの材料の何ページ目を、どの向きで、どの順に並べるか）として持つ。
ページの編集（追加・削除・並べ替え・回転）は、この表を書き換えるだけで、材料のデータは変えない。
材料（開いたファイル・追加したファイルの中身）は、読み込んだ時点でメモリに持ち、ファイルの
名前や場所はここでは扱わない（画面の側がメモリ内に持つ。N-06・F-24）。

組み立て（assemble）で、ページの表のとおりに並べた1つのPDF（メモリ内）を作る。これを、既存の
事前診断（F-04）・変換（F-07〜）・プレビュー（F-30）にそのまま渡す。回転はPDFの回転の属性で行う
（ページの内容は変えない）。Tkに依存しない。
"""
from __future__ import annotations

import io
from dataclasses import dataclass, replace
from typing import Iterable, Literal, Optional, Sequence

import pypdf

from core import images
from core.convert import PRODUCER_MARK

SourceKind = Literal["pdf", "image"]
# 元に戻せる回数の上限（ページの表は小さいので、十分に大きく取る）
UNDO_LIMIT = 200


class SourceError(Exception):
    """材料にできないファイル（暗号化・破損・対応していない形式）の場合のエラー（F-20）。

    メッセージにファイル名は含めない（ログに名前を残さないため〔F-24〕。画面の側で補う）。
    """


class EncryptedSourceError(SourceError):
    """パスワード付き（暗号化）PDFの場合。"""


class CorruptedSourceError(SourceError):
    """破損している・読めないファイルの場合。"""


@dataclass(frozen=True)
class Source:
    """材料（1つのファイルの中身）。画像は、読み込んだ時点で1ページのPDFにしてある。"""

    data: bytes
    page_count: int
    kind: SourceKind
    # 本ツールで二値化・グレースケールにして保存したファイルか（F-02の印。F-06）
    made_by_va_fax: bool = False


@dataclass(frozen=True)
class PageRef:
    """ページの表の1行：材料source（文書の中の番号）のpageページ目（0始まり）を、rotation度回して置く。

    rotationは、利用者が回した分（時計回り。0・90・180・270）。材料のページが元から持つ
    回転の属性には、これを足す。
    """

    source: int
    page: int
    rotation: int = 0


def load_source(data: bytes, paper_mm: tuple[float, float]) -> Source:
    """ファイルの中身から材料を作る。PDFでなければ、画像（JPEG・PNG）として読む（F-34）。

    paper_mmは、画像のページの大きさを決めるのに使う用紙（プロファイルの正規化先）。
    暗号化・破損・対応していない形式は、SourceErrorの派生例外を送出する。
    """
    if not images.is_pdf(data):
        try:
            pdf = images.image_to_pdf(data, paper_mm)
        except images.ImageLoadError as exc:
            raise CorruptedSourceError(
                "このファイルを読み込めませんでした。PDF・JPEG・PNG以外の形式か、"
                "ファイルが破損している可能性があります。") from exc
        return Source(data=pdf, page_count=1, kind="image")

    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise EncryptedSourceError(
                "このPDFはパスワードで保護されているため処理できません。"
                "パスワードを解除したファイルをご用意のうえ、再度お試しください。")
        page_count = len(reader.pages)
        producer = (reader.metadata.producer if reader.metadata else None) or ""
    except SourceError:
        raise
    except Exception as exc:
        raise CorruptedSourceError(
            "PDFファイルを読み込めませんでした。ファイルが破損している可能性があります。") from exc
    if page_count == 0:
        raise CorruptedSourceError("このPDFにはページがありません。")
    return Source(data=bytes(data), page_count=page_count, kind="pdf",
                  made_by_va_fax=producer.strip() == PRODUCER_MARK)


def move_indices(count: int, selected: Iterable[int], drop_index: int) -> tuple[list[int], int]:
    """並べ替えの計算。selected（選んだ位置）を、境界drop_index（0＝先頭の前、count＝末尾の後。
    移動する前の並びでの位置）へ移す。

    戻り値は (新しい並び〔元の位置の番号を並べたもの〕, 移した塊の先頭の新しい位置)。
    選んだものの順序は保つ。
    """
    chosen = sorted(set(i for i in selected if 0 <= i < count))
    drop_index = max(0, min(count, drop_index))
    chosen_set = set(chosen)
    rest = [i for i in range(count) if i not in chosen_set]
    insert_at = drop_index - sum(1 for i in chosen if i < drop_index)
    return rest[:insert_at] + chosen + rest[insert_at:], insert_at


class Document:
    """1つの文書。材料の一覧と、ページの表と、元に戻す・やり直すの履歴を持つ。"""

    def __init__(self) -> None:
        self._sources: list[Source] = []
        self._pages: tuple[PageRef, ...] = ()
        self._undo: list[tuple[PageRef, ...]] = []
        self._redo: list[tuple[PageRef, ...]] = []
        self._assembled: Optional[tuple[tuple[PageRef, ...], bytes]] = None

    # ------------------------------------------------------------ 参照

    @property
    def sources(self) -> tuple[Source, ...]:
        return tuple(self._sources)

    @property
    def pages(self) -> tuple[PageRef, ...]:
        return self._pages

    @property
    def page_count(self) -> int:
        return len(self._pages)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def used_source_count(self) -> int:
        """いまのページの表が使っている材料の数（事前診断の表示用。F-04）。"""
        return len({ref.source for ref in self._pages})

    def all_made_by_va_fax(self) -> bool:
        """いまのページが、すべてVA FAX で保存したファイルのものか（F-06）。"""
        return bool(self._pages) and all(self._sources[ref.source].made_by_va_fax for ref in self._pages)

    def is_untouched_single_source(self) -> bool:
        """1つの材料の全ページを、元の順・元の向きのまま並べただけか（中身が変わっていないか。F-03）。"""
        if not self._pages:
            return False
        source = self._pages[0].source
        return self._pages == tuple(PageRef(source, i) for i in range(self._sources[source].page_count))

    # ------------------------------------------------------------ 編集

    def _commit(self, pages: Sequence[PageRef]) -> bool:
        new = tuple(pages)
        if new == self._pages:
            return False
        self._undo.append(self._pages)
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()
        self._pages = new
        return True

    def add_source(self, source: Source, at: Optional[int] = None) -> range:
        """材料を足し、その全ページを、境界at（省くと末尾）に入れる。入れた位置の範囲を返す。"""
        return self.add_sources([source], at)

    def add_sources(self, sources: Sequence[Source], at: Optional[int] = None) -> range:
        """複数の材料を、順に、境界atに入れる（元に戻すときは、まとめて1回で戻る）。"""
        at = len(self._pages) if at is None else max(0, min(len(self._pages), at))
        refs: list[PageRef] = []
        for source in sources:
            self._sources.append(source)
            index = len(self._sources) - 1
            refs.extend(PageRef(index, i) for i in range(source.page_count))
        self._commit(self._pages[:at] + tuple(refs) + self._pages[at:])
        return range(at, at + len(refs))

    def put_front(self, source: Source, replacing: Iterable[tuple[int, int]] = ()) -> range:
        """材料sourceの全ページを先頭に入れ、replacing（（材料の番号, ページ）の組）に当たるページを
        取り除く（送付書の作成・差し替え。F-36）。元に戻すときは、まとめて1回で戻る。
        入れた位置の範囲を返す。"""
        remove = set(replacing)
        self._sources.append(source)
        index = len(self._sources) - 1
        rest = tuple(ref for ref in self._pages if (ref.source, ref.page) not in remove)
        self._commit(tuple(PageRef(index, i) for i in range(source.page_count)) + rest)
        return range(0, source.page_count)

    def delete(self, indices: Iterable[int]) -> bool:
        """選んだ位置のページを取り除く。すべてのページを取り除くことはできない（F-33）。"""
        chosen = {i for i in indices if 0 <= i < len(self._pages)}
        if not chosen or len(chosen) >= len(self._pages):
            return False
        return self._commit([ref for i, ref in enumerate(self._pages) if i not in chosen])

    def move(self, indices: Iterable[int], drop_index: int) -> Optional[range]:
        """選んだ位置のページを、境界drop_indexへ移す。並びが変わらなければNone、変われば新しい位置の範囲。"""
        chosen = sorted({i for i in indices if 0 <= i < len(self._pages)})
        order, insert_at = move_indices(len(self._pages), chosen, drop_index)
        if not self._commit([self._pages[i] for i in order]):
            return None
        return range(insert_at, insert_at + len(chosen))

    def rotate(self, indices: Iterable[int], degrees: int) -> bool:
        """選んだ位置のページを、時計回りにdegrees度（90の倍数。左回りは負）回す。"""
        if degrees % 90:
            raise ValueError("回転は90度の倍数で指定します")
        chosen = {i for i in indices if 0 <= i < len(self._pages)}
        return self._commit([
            replace(ref, rotation=(ref.rotation + degrees) % 360) if i in chosen else ref
            for i, ref in enumerate(self._pages)])

    def clear_history(self) -> None:
        """元に戻す・やり直すの履歴を捨てる（ファイルを開いた直後に呼ぶ。開く前には戻さない）。"""
        self._undo.clear()
        self._redo.clear()

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._pages)
        self._pages = self._undo.pop()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._pages)
        self._pages = self._redo.pop()
        return True

    # ------------------------------------------------------------ 組み立て

    def assemble(self) -> bytes:
        """ページの表のとおりに並べた1つのPDFを作る（メモリ内）。

        1つのPDFの全ページを、元の順・元の向きのまま並べただけのときは、作り直さずに、その
        材料のデータをそのまま返す（開いたファイルとバイト単位で同じになることを保つ）。
        """
        if not self._pages:
            raise ValueError("文書にページがありません")
        if self._assembled is not None and self._assembled[0] == self._pages:
            return self._assembled[1]
        data = assemble(self._sources, self._pages)
        self._assembled = (self._pages, data)
        return data


def assemble(sources: Sequence[Source], pages: Sequence[PageRef]) -> bytes:
    """材料とページの表から、1つのPDFを作る（Document.assembleの中身。材料とページの表の
    写しを渡せば、画面とは別のスレッドからも呼べる）。

    1つのPDFの全ページを、元の順・元の向きのまま並べただけのときは、その材料のデータを
    そのまま返す。
    """
    if not pages:
        raise ValueError("文書にページがありません")
    first = pages[0].source
    if tuple(pages) == tuple(PageRef(first, i) for i in range(sources[first].page_count)):
        return sources[first].data
    return assemble_pages(sources, pages)


def assemble_pages(sources: Sequence[Source], pages: Sequence[PageRef]) -> bytes:
    """材料とページの表から、1つのPDFを作る。回転は、PDFの回転の属性に足す。"""
    readers: dict[int, pypdf.PdfReader] = {}
    writer = pypdf.PdfWriter()
    for ref in pages:
        reader = readers.get(ref.source)
        if reader is None:
            reader = readers[ref.source] = pypdf.PdfReader(io.BytesIO(sources[ref.source].data))
        page = writer.add_page(reader.pages[ref.page])
        if ref.rotation:
            page.rotate(ref.rotation)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
