# -*- coding: utf-8 -*-
"""送付書（兼受領書）の作成（F-36。Ver210）のテスト：日付・証拠番号・書類の一覧・入力の保存・ページの作成。

名前・住所・番号は、すべて架空のもの。
"""
from __future__ import annotations

import datetime as dt
import io
import json
from pathlib import Path

import pypdf
import pytest

from core import cover_sheet as cs
from core.cover_sheet import CoverInput, DocItem, Sender


# ---------------------------------------------------------------- 日付

def test_wareki():
    assert cs.wareki(dt.date(2026, 11, 18)) == "令和8年11月18日"
    assert cs.wareki(dt.date(2019, 5, 1)) == "令和元年5月1日"
    assert cs.wareki(dt.date(2020, 1, 2)) == "令和2年1月2日"


# ---------------------------------------------------------------- 証拠番号（VA mints の命名）

@pytest.mark.parametrize("stem, expected", [
    ("甲001 売買契約書", cs.Evidence("甲", 1)),
    ("甲001", cs.Evidence("甲", 1)),
    ("乙012_領収書", cs.Evidence("乙", 12)),
    ("甲005-2 写真", cs.Evidence("甲", 5, 2)),
    ("甲005_1~20 写真", cs.Evidence("甲", 5, 1, 20)),
    ("甲005-1～3", cs.Evidence("甲", 5, 1, 3)),
    ("乙A003 陳述書", cs.Evidence("乙A", 3)),
    ("乙ａ003", cs.Evidence("乙A", 3)),
    ("丙100", cs.Evidence("丙", 100)),
    ("甲００１－２", cs.Evidence("甲", 1, 2)),     # 全角も受け付ける（VA mints と同じ）
    ("甲20240401_領収書", None),                 # 3桁を超える数字は証拠番号ではない
    ("甲01 契約書", None),
    ("準備書面（1）", None),
    ("証拠説明書（2）", None),
])
def test_parse_evidence(stem, expected):
    assert cs.parse_evidence(stem) == expected


def test_documents_from_materials_merges_consecutive_evidence():
    items = [("準備書面（1）", 5), ("証拠説明書（2）", 1),
             ("甲003 売買契約書", 2), ("甲004 領収書", 1),        # 続く番号は1行に
             ("甲005-1 写真", 1), ("甲005-2 写真", 1),            # 同じ番号の枝番は1行に
             ("甲006 メール", 2), ("甲008 メモ", 1),              # 番号が飛ぶと分ける
             ("乙001 反論書面", 3), ("写真", 1)]
    assert cs.documents_from_materials(items) == [
        DocItem("準備書面（1）", "5"), DocItem("証拠説明書（2）", "1"),
        DocItem("甲第3号証〜第4号証", "3"), DocItem("甲第5号証の1〜2", "2"),
        DocItem("甲第6号証", "2"), DocItem("甲第8号証", "1"),
        DocItem("乙第1号証", "3"), DocItem("写真", "1"),
    ]


def test_documents_from_materials_branch_ranges_and_mixed():
    items = [("甲005_1~3", 3), ("甲005-4", 1), ("甲006", 1), ("甲007-1", 1), ("乙A001", 1), ("乙B002", 1)]
    assert cs.documents_from_materials(items) == [
        DocItem("甲第5号証の1〜4", "4"),     # 範囲の枝番に、続く枝番がつながる
        DocItem("甲第6号証", "1"),           # 枝番のないものと枝番のあるものは、つなげない
        DocItem("甲第7号証の1", "1"),
        DocItem("乙A第1号証", "1"), DocItem("乙B第2号証", "1"),   # 添字が違えば分ける
    ]


# ---------------------------------------------------------------- 入力の保存・読み込み

def _input() -> CoverInput:
    return CoverInput(
        date="令和8年11月18日", court="架空地方裁判所民事第1部ろ係", court_fax="00-0000-0001",
        other="被告訴訟代理人弁護士　乙山次郎", other_honorific="先生", other_fax="00-0000-0002",
        sender_title="原告訴訟代理人弁護士", case_number="令和8年（ワ）第123号", case_name="売買代金請求事件",
        parties="原告　甲山一郎　被告　乙川商事株式会社",
        documents=[DocItem("準備書面（1）", "5"), DocItem("甲第1号証〜第3号証", "4")],
        message="次回期日：令和8年12月1日午前10時\n2行目", receipt=True)


def _sender() -> Sender:
    return Sender(office="架空法律事務所", address="〒000-0000\n架空県架空市1-2-3",
                  tel="00-0000-0000", fax="00-0000-0009", lawyer="甲野太郎")


def test_save_and_load_input(tmp_path):
    pdf = tmp_path / "送付用_fax.pdf"
    path = cs.save_input(pdf, _input(), 1, 10)
    assert path == tmp_path / "FAX送付書データ" / "送付用_fax.json"
    saved = cs.load_input(pdf)
    assert saved == cs.SavedCover(_input(), 1, 10)
    # 送信者の事務所名などは書かない（settings.json にある。F-23）
    assert "架空法律事務所" not in path.read_text(encoding="utf-8")
    assert not list((tmp_path / "FAX送付書データ").glob("*.tmp"))


def test_load_input_missing_or_broken(tmp_path):
    pdf = tmp_path / "a.pdf"
    assert cs.load_input(pdf) is None
    cs.data_path(pdf).parent.mkdir()
    cs.data_path(pdf).write_text("{壊れている", encoding="utf-8")
    assert cs.load_input(pdf) is None
    cs.data_path(pdf).write_text(json.dumps({"input": "形が違う"}), encoding="utf-8")
    assert cs.load_input(pdf) is None


def test_from_json_tolerates_hand_edited_values():
    cover = CoverInput.from_json({"court": "架空地方裁判所", "other_honorific": "御前", "receipt": 0,
                                  "documents": [{"name": "準備書面", "pages": 3}, "壊れた行"], "unknown": 1})
    assert cover.court == "架空地方裁判所" and cover.other_honorific == "殿" and cover.receipt is False
    assert cover.documents == [DocItem("準備書面", "3")] and cover.case_number == ""


def test_latest_input_in_folder_excludes_own_file(tmp_path):
    import os
    first = _input()
    first.case_number = "令和8年（ワ）第1号"
    second = _input()
    second.case_number = "令和8年（ワ）第2号"
    cs.save_input(tmp_path / "前の送付.pdf", first, 1, 3)
    cs.save_input(tmp_path / "今の送付.pdf", second, 1, 3)
    older = cs.data_path(tmp_path / "前の送付.pdf")
    os.utime(older, (1, 1))
    assert cs.latest_input_in_folder(tmp_path).case_number == "令和8年（ワ）第2号"
    assert cs.latest_input_in_folder(tmp_path, exclude=tmp_path / "今の送付.pdf").case_number == "令和8年（ワ）第1号"
    assert cs.latest_input_in_folder(tmp_path / "ない") is None


def _write_mints(folder: Path, header: dict) -> None:
    data = folder / "証拠説明書データ"
    data.mkdir(parents=True)
    (data / "証拠説明書.json").write_text(json.dumps({"version": 1, "header": header, "files": {}},
                                                     ensure_ascii=False), encoding="utf-8")


def test_read_mints_header_from_folder_or_parent(tmp_path):
    header = {"court": "架空地方裁判所", "case_number": "令和8年（ワ）第123号", "number": "2",
              "date": "", "party": "原告", "name": "原告訴訟代理人弁護士　甲野太郎"}
    _write_mints(tmp_path / "事件", header)
    expected = {"court": "架空地方裁判所", "case_number": "令和8年（ワ）第123号", "party": "原告",
                "name": "原告訴訟代理人弁護士　甲野太郎"}
    assert cs.read_mints_header([tmp_path / "事件" / "甲001 契約書.pdf"]) == expected
    # VA mints の「提出用_01」から追加したとき（親のフォルダ）
    assert cs.read_mints_header([tmp_path / "事件" / "提出用_01" / "甲001 契約書.pdf"]) == expected
    assert cs.read_mints_header([tmp_path / "ほか" / "a.pdf"]) is None


def test_sender_title_from_mints():
    assert cs.sender_title_from_mints({"name": "原告訴訟代理人弁護士　甲野太郎", "party": "原告"}) == "原告訴訟代理人弁護士"
    assert cs.sender_title_from_mints({"name": "甲野太郎", "party": "被告"}) == "被告訴訟代理人弁護士"
    assert cs.sender_title_from_mints({"name": "", "party": ""}) == ""


def test_initial_input_precedence():
    today = dt.date(2026, 11, 18)
    docs = [DocItem("準備書面（1）", "5")]
    # 1. この文書の入力（日付だけは今日にする）
    previous = _input()
    result = cs.initial_input(today, docs, previous=previous)
    assert result.court == previous.court and result.documents == previous.documents
    assert result.date == "令和8年11月18日" and previous.date == "令和8年11月18日"
    # 2. 同じフォルダのほかの入力：宛先・事件・肩書だけ（書類の一覧・通信欄は使わない）
    result = cs.initial_input(today, docs, folder_latest=_input())
    assert result.court == previous.court and result.case_number == previous.case_number
    assert result.documents == docs and result.message == ""
    # 3. VA mints の証拠説明書
    mints = {"court": "架空地方裁判所", "case_number": "令和8年（ワ）第9号", "party": "被告", "name": "乙山次郎"}
    result = cs.initial_input(today, docs, mints=mints)
    assert (result.court, result.case_number, result.sender_title) == (
        "架空地方裁判所", "令和8年（ワ）第9号", "被告訴訟代理人弁護士")
    # 何もなければ、日付と書類の一覧だけ
    assert cs.initial_input(today, docs) == CoverInput(date="令和8年11月18日", documents=docs)


def test_sender_from_dict():
    assert cs.Sender.from_dict({"office": "架空法律事務所", "tel": 1, "x": "y"}) == Sender(office="架空法律事務所", tel="1")
    assert cs.Sender.from_dict("壊れた値") == Sender()


# ---------------------------------------------------------------- ページの作成

def _text(data: bytes) -> str:
    reader = pypdf.PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages).replace(" ", "")


def test_build_cover_with_receipt():
    data, pages, total = cs.build_cover(_input(), _sender(), other_pages=9)
    assert (pages, total) == (1, 10)
    reader = pypdf.PdfReader(io.BytesIO(data))
    box = reader.pages[0].mediabox
    assert round(float(box.width) * 25.4 / 72) == 210 and round(float(box.height) * 25.4 / 72) == 297
    text = _text(data)
    for expected in ("送付書（兼受領書）", "架空地方裁判所民事第1部ろ係", "御中", "先生", "架空法律事務所",
                     "原告訴訟代理人弁護士", "甲野太郎", "令和8年（ワ）第123号", "本書を含め", "10",
                     "準備書面（1）", "甲第1号証〜第3号証", "通信欄", "次回期日", "受領書",
                     "上記書類を受領しました", "当職及び裁判所", "印"):
        assert expected in text, expected
    # 字は埋め込んだフォント（明朝体）で書く（受け手の環境で字が変わらないように）
    fonts = reader.pages[0]["/Resources"]["/Font"]
    embedded = [f for f in fonts.values() if "/DescendantFonts" in f.get_object()
                or "/FontFile2" in f.get_object().get("/FontDescriptor", {})]
    assert embedded


def test_build_cover_without_receipt_or_court():
    cover = _input()
    cover.receipt = False
    cover.court = ""
    text = _text(cs.build_cover(cover, Sender(), other_pages=2)[0])
    assert "送付書" in text and "兼受領書" not in text and "受領書" not in text
    assert "御中" not in text and "本書を含め3枚" in text


def test_empty_message_hides_the_message_box():
    """通信欄に何も書いていないときは、通信欄そのものを書かない（2026-11-18 班長指示）。"""
    cover = _input()
    assert "通信欄" in _text(cs.build_cover(cover, _sender(), other_pages=2)[0])
    cover.message = " \n "
    assert "通信欄" not in _text(cs.build_cover(cover, _sender(), other_pages=2)[0])


def test_build_cover_receipt_without_court_asks_only_sender():
    cover = _input()
    cover.court = ""
    text = _text(cs.build_cover(cover, _sender(), other_pages=2)[0])
    assert "当職あてにFAX" in text and "当職及び裁判所" not in text


def test_long_document_list_makes_two_pages_and_counts_them():
    cover = _input()
    cover.documents = [DocItem(f"甲第{i}号証", "1") for i in range(1, 60)]
    data, pages, total = cs.build_cover(cover, _sender(), other_pages=59)
    assert pages >= 2 and total == 59 + pages
    assert len(pypdf.PdfReader(io.BytesIO(data)).pages) == pages
    assert f"本書を含め{total}枚" in _text(data)


def test_build_cover_does_not_touch_the_network(monkeypatch):
    """送付書の作成の間に、通信しない（N-01。reportlab には URL から読む機能があるが、使わない）。"""
    import socket

    def refuse(*_args, **_kwargs):
        raise AssertionError("通信しようとした")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    data, pages, _total = cs.build_cover(_input(), _sender(), other_pages=3)
    assert pages == 1 and data.startswith(b"%PDF")
