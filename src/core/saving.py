# -*- coding: utf-8 -*-
"""文書の保存（F-02・F-03・F-13）。

ページの表のとおりに組み立てたPDF（メモリ内）を、選択中の処理（圧縮方式・用紙正規化・
容量超過時の処理方式）で送信用の形式にし、ファイルに書く。Tkに依存しない。

・書き込みは、同じフォルダに一時的な名前で書き終えてから、目的の名前に置き換える
  （書いている途中で失敗しても、元のファイルを壊さない）。この一時的なファイルは出力
  そのものであり、N-06 の「中間データ」（ページ画像等）には当たらない。
・「分割して再実行」で実際に分割が起きたときは、連番の付いた名前のファイルだけを書き、
  目的の名前のファイル（開いたファイル）には書かない。
・材料にしたファイル（開いた・追加したファイル）と同じ名前の分割ファイルは作らない（F-03）。
"""
from __future__ import annotations

import io
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

import pypdf

from config.profiles import Profile
from core import as_is, convert, size_control
from core.convert import ConvertOptions, PageConversionLog


class SaveError(Exception):
    """保存できない場合のエラー。メッセージは、そのまま画面に出せる文とする（F-21）。

    kindは、ログに残す種別（メッセージにはファイル名が入りうるので、ログにはkindだけを書く。F-24）。
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class SaveOutcome:
    """保存の結果。"""

    paths: tuple[Path, ...]            # 書いたファイル（分割のときは複数）
    page_counts: tuple[int, ...]
    sizes: tuple[int, ...]
    first_pdf: bytes                   # プレビューに表示する、1つ目のファイルの中身
    split: bool                        # 分割が起きたか（起きたときは、目的の名前には書いていない）


def same_path(a: Path, b: Path) -> bool:
    """2つのパスが同じファイルを指すか（大文字・小文字、相対・絶対の違いをならして比べる）。"""
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def split_output_paths(base_path: Path, count: int) -> list[Path]:
    """分割時のファイル名を決める（例：foo.pdf → foo_1of3.pdf）。count==1なら、元の名前のまま。"""
    if count == 1:
        return [base_path]
    stem, suffix = base_path.stem, base_path.suffix
    return [base_path.with_name(f"{stem}_{i + 1}of{count}{suffix}") for i in range(count)]


def write_atomically(path: Path, data: bytes) -> None:
    """同じフォルダに一時的な名前で書き終えてから、目的の名前に置き換える（F-02）。

    失敗したときは、一時的なファイルを消してからOSErrorを送出する（目的のファイルは元のまま）。
    """
    temp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(temp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    except OSError:
        try:
            temp.unlink()
        except OSError:
            pass
        raise


def save_document(
    doc_bytes: bytes, target: Path, profile: Profile, options: ConvertOptions,
    capacity_method: str, reduction_strategy: str, material_paths: Sequence[Path],
    on_page_done: Optional[Callable[[int, int, PageConversionLog], None]] = None,
    on_retry: Optional[Callable[[int, int, int, int, Optional[int]], None]] = None,
) -> SaveOutcome:
    """文書を変換して、targetに保存する。

    capacity_methodは「reduce_resolution」「split」「none」（何もしない：上限を超えていても、
    そのまま書く）。options.modeが「none」（何もしない）のときは、ページを画像にせず、
    doc_bytes のページをそのまま書く（このとき「reduce_resolution」は効かないので、
    「none」と同じに扱う。F-11）。material_pathsは、材料にしたファイル
    （分割してできるファイルの名前が、これらと同じにならないことを確かめる）。
    変換・容量の検証で止まるとき（F-13・F-14）は、convert.ConversionError・
    size_control.SizeLimitExceededError をそのまま送出する。書き込みに関わる誤りはSaveError。
    """
    if options.mode == "none":
        # ページ番号（F-37）は、ページを画像にせず、PDFのまま重ねる。分割の前に入れるので、
        # ファイルをまたいだ通し番号になる。
        if options.page_numbers:
            doc_bytes = as_is.stamp_numbers(doc_bytes)
        if capacity_method == "split":
            parts = size_control.split_pages_as_is(doc_bytes, profile)
        else:
            parts = [(doc_bytes, len(pypdf.PdfReader(io.BytesIO(doc_bytes)).pages))]
        pdfs = [data for data, _count in parts]
        page_counts = [count for _data, count in parts]
    else:
        result = convert.convert(doc_bytes, profile, options, on_page_done=on_page_done)
        if capacity_method == "split":
            results = size_control.split_by_capacity(
                doc_bytes, profile, options, result, on_page_reduced=on_retry)
        elif capacity_method == "none":
            results = [result]
        else:
            results = [size_control.enforce_size_limit(
                doc_bytes, profile, options, result, on_retry=on_retry, strategy=reduction_strategy)]
        pdfs = [r.build_pdf() for r in results]
        page_counts = [len(r.page_logs) for r in results]

    paths = split_output_paths(target, len(pdfs))
    split = len(pdfs) > 1
    if split:
        for path in paths:
            if any(same_path(path, material) for material in material_paths):
                raise SaveError(
                    "split_name_is_material",
                    f"分割してできるファイルの名前（{path.name}）が、開いた・追加したファイルと同じに"
                    "なってしまいます。［保存］で、別の名前を指定してください。")
        existing = [path.name for path in paths if path.exists()]
        if existing:
            raise SaveError(
                "split_file_exists",
                "分割してできるファイルと同じ名前のファイルが、既にあります：\n" + "、".join(existing)
                + "\n該当のファイルを移動・削除するか、［保存］で別の名前を指定してから、"
                  "もう一度保存してください。")

    written: list[Path] = []
    try:
        for path, data in zip(paths, pdfs):
            write_atomically(path, data)
            written.append(path)
    except OSError as exc:
        message = ("ファイルを書き込めませんでした。保存先のフォルダの権限や空き容量、"
                   "ファイルが他のアプリで開かれていないかを確かめてください。")
        if split:
            # 分割の途中で失敗したとき、一部のファイルだけが残ると、不完全な組と気づかずに
            # 送信するおそれがあるので、今回書いた分は消す（今回新しく作ったファイルだけ）。
            undeleted = []
            for path in written:
                try:
                    path.unlink()
                except OSError:
                    undeleted.append(path.name)
            if undeleted:
                message += "\n書き込み途中のファイルが残っています。削除してください：\n" + "、".join(undeleted)
        raise SaveError(f"write_failed:{type(exc).__name__}", message) from exc

    return SaveOutcome(
        paths=tuple(paths),
        page_counts=tuple(page_counts),
        sizes=tuple(len(data) for data in pdfs),
        first_pdf=pdfs[0],
        split=split,
    )
