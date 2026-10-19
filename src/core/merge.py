# -*- coding: utf-8 -*-
"""複数の入力PDFの結合（F-01）。

FAX送信時は送信票PDFに本文PDFを添付するのが通常の運用であり、その結合作業を
本ツール内で行えるようにする。結合はメモリ内（BytesIO）でのみ行い、中間データを
ディスクへ書き出さない（N-06）。入力ファイル自体は変更しない（F-03）。
結合後のPDFは、そのまま既存の診断（F-04）・変換（F-07〜）パイプラインへ渡される
（変換で全ページがラスタライズされるため、結合時にしおり・注釈等を引き継ぐ必要はない）。
"""
from __future__ import annotations

import io
from typing import Sequence

import pypdf


class MergeError(Exception):
    """結合できないファイル（暗号化・破損等）がある場合のエラー。

    file_index（0始まり）で何件目の入力ファイルかを保持し、GUI側でファイル名を
    補って表示する（ログにはファイル名を記録できないため〔F-24〕、例外メッセージ
    自体にはファイル名を含めない）。
    """

    def __init__(self, message: str, file_index: int):
        super().__init__(message)
        self.file_index = file_index


def merge_pdfs(pdf_bytes_list: Sequence[bytes]) -> bytes:
    """複数のPDFバイト列を、リストの順序どおりに1つのPDFへ結合する。

    入力が1件の場合は再構築を行わず、そのバイト列をそのまま返す（従来の
    単一ファイル変換と入力がバイト単位で同一になることを保ち、pypdfによる
    再構築の影響を受けないため）。暗号化・破損ファイルが含まれる場合は
    MergeErrorを送出する。
    """
    if not pdf_bytes_list:
        raise ValueError("結合対象のPDFがありません")

    readers: list[pypdf.PdfReader] = []
    for index, data in enumerate(pdf_bytes_list):
        try:
            reader = pypdf.PdfReader(io.BytesIO(data))
        except Exception as exc:
            raise MergeError(
                "PDFファイルを読み込めませんでした。ファイルが破損している可能性があります。",
                index,
            ) from exc
        if reader.is_encrypted:
            raise MergeError(
                "このPDFはパスワードで保護されているため処理できません。"
                "パスワードを解除したファイルをご用意のうえ、再度お試しください。",
                index,
            )
        readers.append(reader)

    if len(pdf_bytes_list) == 1:
        return bytes(pdf_bytes_list[0])

    writer = pypdf.PdfWriter()
    for index, reader in enumerate(readers):
        try:
            writer.append(reader)
        except Exception as exc:
            raise MergeError(
                "PDFファイルを結合できませんでした。ファイルが破損している可能性があります。",
                index,
            ) from exc

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
