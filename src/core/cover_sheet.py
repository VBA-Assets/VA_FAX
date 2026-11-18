# -*- coding: utf-8 -*-
"""送付書（兼受領書）の作成（F-36。Ver210。2026-11-18 班長指示）。

訴訟で相手方に書面をFAXで直送するとき（民事訴訟規則47条2項）の送付書を作る。受領書の欄は、
直送を受けた相手方が、受領した旨を書いて直送し、裁判所に出す書面（同47条の2第3項。ただし書に
より、直送した側が、相手方が受領した旨を書き込んだ書類を裁判所に出せば足りる）に使う。法令・
裁判所が定めた様式はないため、実務でよく使われる項目による。

- 書類の一覧の初めの値は、ページの材料のファイルの名前から作る。VA mints の命名による証拠の
  ファイル（「甲001 売買契約書」「甲005-2」）は「甲第1号証」「甲第5号証の2」と書き、続く番号は
  1行にまとめる（判定は VA mints の naming/evidence_number.py と同じ）。
- VA mints の証拠説明書の入力（証拠説明書データ\\証拠説明書.json）があれば、裁判所・事件番号・
  提出者の名前を、初めの値に使う。
- 入力は、保存したPDFと同じフォルダの「FAX送付書データ」フォルダに、<PDFの名前>.json として
  保存する（VA mints の 証拠説明書.json と同じ形。2026-11-18 班長決定。N-06 の例外）。送信者の
  事務所名などは、ここには書かない（settings.json にある。F-23）。
- ページは reportlab で、メモリ内に作る（一時ファイルを作らない。N-06）。字は明朝体の TrueType を
  PDF に埋め込む（VA mints の証拠説明書と同じ）。

Tk に依存しない。ログに入力の中身・ファイルの名前を書かない（F-24）。
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence
from xml.sax.saxutils import escape

DATA_DIRNAME = "FAX送付書データ"     # 入力を置くフォルダ（保存したPDFと同じフォルダの中）
_VERSION = 1
# VA mints の証拠説明書の入力（VA mints の processors/evidence_description.py と同じ置き場）
MINTS_DATA_DIRNAME = "証拠説明書データ"
MINTS_DATA_NAME = "証拠説明書.json"

HONORIFICS = ("殿", "先生", "様")
COURT_HONORIFIC = "御中"


@dataclass
class Sender:
    """送信者（事務所自身の情報。settings.json に保存する。F-23・F-35）。"""

    office: str = ""     # 事務所名
    address: str = ""    # 住所（複数行でよい）
    tel: str = ""
    fax: str = ""
    lawyer: str = ""     # 弁護士名（例：甲野太郎）

    @classmethod
    def from_dict(cls, raw: Any) -> "Sender":
        if not isinstance(raw, dict):
            return cls()
        return cls(**{k: str(raw.get(k) or "") for k in cls.__dataclass_fields__})


@dataclass
class DocItem:
    """書類の一覧の1行。"""

    name: str = ""
    pages: str = ""      # 枚数（空でよい。利用者が直せるよう文字で持つ）


@dataclass
class CoverInput:
    """送付書の入力（文書ごと。「FAX送付書データ」に保存する）。"""

    date: str = ""                 # 送信日（例：令和8年11月18日）
    court: str = ""                # 裁判所の宛名（例：東京地方裁判所民事第1部ろ係）。「御中」を付ける
    court_fax: str = ""
    other: str = ""                # 相手方の宛名（例：被告訴訟代理人弁護士　乙山次郎）
    other_honorific: str = HONORIFICS[0]
    other_fax: str = ""
    sender_title: str = ""         # 送信者の肩書（例：原告訴訟代理人弁護士）
    case_number: str = ""
    case_name: str = ""
    parties: str = ""              # 当事者（例：原告　甲山一郎　被告　乙川商事株式会社）
    documents: list[DocItem] = field(default_factory=list)
    message: str = ""              # 通信欄
    receipt: bool = True           # 受領書の欄を付けるか

    def copy(self) -> "CoverInput":
        return dataclasses.replace(self, documents=[dataclasses.replace(d) for d in self.documents])

    def to_json(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> "CoverInput":
        """読めない値は、項目ごとに初めの値にする（手で直されたファイルにも耐える）。"""
        if not isinstance(raw, dict):
            raise ValueError("送付書の入力の形が違います")
        values: dict[str, Any] = {}
        for name, f in cls.__dataclass_fields__.items():
            if name not in raw:
                continue
            value = raw[name]
            if name == "documents":
                items = []
                for item in value if isinstance(value, list) else []:
                    if isinstance(item, dict):
                        items.append(DocItem(str(item.get("name") or ""), str(item.get("pages") or "")))
                values[name] = items
            elif name == "receipt":
                values[name] = bool(value)
            elif isinstance(value, (str, int, float)):
                values[name] = str(value)
        result = cls(**values)
        if result.other_honorific not in HONORIFICS:
            result.other_honorific = HONORIFICS[0]
        return result


# ---------------------------------------------------------------------- 日付

def wareki(day: _dt.date) -> str:
    """和暦の日付（例：令和8年11月18日。令和元年は「元年」とする）。"""
    year = day.year - 2018
    if year < 1:
        return f"{day.year}年{day.month}月{day.day}日"
    return f"令和{'元' if year == 1 else year}年{day.month}月{day.day}日"


# ---------------------------------------------------------------------- 証拠番号

# VA mints の naming/evidence_number.py と同じ判定（符号は甲・乙・丙、被告が複数のときの添字、
# 3桁の番号、枝番〔ハイフン・アンダースコア。範囲は「~」「〜」「～」〕。全角も受け付ける）。
_EVIDENCE = re.compile(
    r"^(?P<symbol>甲|乙|丙)(?P<sub>[A-Za-zＡ-Ｚａ-ｚ]?)(?P<number>\d{3})(?!\d)"
    r"(?:[-_－＿](?P<branch_from>\d+)(?:[~〜～](?P<branch_to>\d+))?)?"
)
_HALF = str.maketrans({**{0xFF21 + i: 0x41 + i for i in range(26)}, **{0xFF41 + i: 0x61 + i for i in range(26)}})


@dataclass(frozen=True)
class Evidence:
    symbol: str          # 甲・乙・丙（添字を含む。例：乙A）
    number: int
    branch_from: Optional[int] = None
    branch_to: Optional[int] = None


def parse_evidence(stem: str) -> Optional[Evidence]:
    """ファイル名（拡張子を除く）の先頭が証拠番号なら、その番号（違えばNone）。"""
    m = _EVIDENCE.match(stem.strip())
    if not m:
        return None
    sub = m.group("sub").translate(_HALF).upper()
    branch_from = int(m.group("branch_from")) if m.group("branch_from") else None
    branch_to = int(m.group("branch_to")) if m.group("branch_to") else None
    return Evidence(m.group("symbol") + sub, int(m.group("number")), branch_from, branch_to)


def _evidence_text(first: Evidence, last: Evidence) -> str:
    """号証の書き方（例：甲第1号証、甲第1号証〜第3号証、甲第5号証の1〜3）。"""
    head = f"{first.symbol}第{first.number}号証"
    if first.branch_from is None:
        return head if last.number == first.number else f"{head}〜第{last.number}号証"
    end = last.branch_to if last.branch_to is not None else last.branch_from
    if end == first.branch_from:
        return f"{head}の{first.branch_from}"
    return f"{head}の{first.branch_from}〜{end}"


def _continues(prev: Evidence, cur: Evidence) -> bool:
    """curが、prevの次の番号か（枝番のないもの同士は番号が続く、同じ番号の枝番同士は枝番が続く）。"""
    if prev.symbol != cur.symbol:
        return False
    if prev.branch_from is None and cur.branch_from is None:
        return cur.number == prev.number + 1
    if prev.branch_from is None or cur.branch_from is None or prev.number != cur.number:
        return False
    prev_end = prev.branch_to if prev.branch_to is not None else prev.branch_from
    return cur.branch_from == prev_end + 1


def documents_from_materials(items: Sequence[tuple[str, int]]) -> list[DocItem]:
    """書類の一覧の初めの値。itemsは（ファイルの名前〔拡張子を除く〕, いまの文書に残っているページ数）を、
    ページの表で最初に現れる順に並べたもの。証拠のファイルは号証で書き、続く番号は1行にまとめる。"""
    rows: list[DocItem] = []
    run: list[tuple[Evidence, int]] = []

    def flush() -> None:
        if run:
            rows.append(DocItem(_evidence_text(run[0][0], run[-1][0]), str(sum(p for _, p in run))))
            run.clear()

    for stem, pages in items:
        evidence = parse_evidence(stem)
        if evidence is not None and run and _continues(run[-1][0], evidence):
            run.append((evidence, pages))
            continue
        flush()
        if evidence is not None:
            run.append((evidence, pages))
        else:
            rows.append(DocItem(stem, str(pages)))
    flush()
    return rows


# ---------------------------------------------------------------------- 入力の保存・読み込み

def data_path(pdf_path: Path) -> Path:
    """保存したPDFの、送付書の入力の置き場（同じフォルダの「FAX送付書データ」の <名前>.json）。"""
    return pdf_path.parent / DATA_DIRNAME / f"{pdf_path.stem}.json"


@dataclass(frozen=True)
class SavedCover:
    """保存した送付書の入力と、そのときの送付書のページ数・文書の総ページ数。"""

    input: CoverInput
    cover_pages: int = 1
    total_pages: int = 0


def save_input(pdf_path: Path, cover: CoverInput, cover_pages: int, total_pages: int) -> Path:
    """入力を保存する（一時的な名前で書き終えてから置き換える）。OSErrorは呼び出し側で扱う。"""
    # config.settings が Sender をこのモジュールから読むため、保存の部品はここで読み込む（循環を避ける）
    from core.saving import write_atomically

    path = data_path(pdf_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": _VERSION, "cover_pages": cover_pages, "total_pages": total_pages,
            "input": cover.to_json()}
    write_atomically(path, json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"))
    return path


def _read(path: Path) -> SavedCover:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("形が違います")
        cover = CoverInput.from_json(raw.get("input"))
        cover_pages = int(raw.get("cover_pages", 1))
        total_pages = int(raw.get("total_pages", 0))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("送付書の入力を読めません") from exc
    return SavedCover(cover, max(1, cover_pages), max(0, total_pages))


def load_input(pdf_path: Path) -> Optional[SavedCover]:
    """保存したPDFの送付書の入力（ない・読めないときはNone）。"""
    path = data_path(pdf_path)
    if not path.is_file():
        return None
    try:
        return _read(path)
    except ValueError:
        return None


def latest_input_in_folder(folder: Path, exclude: Optional[Path] = None) -> Optional[CoverInput]:
    """フォルダの送付書の入力のうち、いちばん新しいもの（excludeのPDFの入力は除く。なければNone）。"""
    data_dir = folder / DATA_DIRNAME
    try:
        candidates = [p for p in data_dir.glob("*.json") if p.is_file()]
    except OSError:
        return None
    if exclude is not None:
        own = data_path(exclude)
        candidates = [p for p in candidates if p.name.lower() != own.name.lower()]
    for path in sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            return _read(path).input
        except ValueError:
            continue
    return None


def read_mints_header(material_paths: Iterable[Path]) -> Optional[dict[str, str]]:
    """材料のファイルのフォルダ（又はその親。VA mints の「提出用_NN」から追加したとき）にある、
    VA mints の証拠説明書の入力の表題の欄（裁判所・事件番号・提出者の別・提出者の名前）。"""
    seen: set[Path] = set()
    for material in material_paths:
        for folder in (material.parent, material.parent.parent):
            if folder in seen:
                continue
            seen.add(folder)
            path = folder / MINTS_DATA_DIRNAME / MINTS_DATA_NAME
            if not path.is_file():
                continue
            try:
                header = json.loads(path.read_text(encoding="utf-8")).get("header") or {}
            except (OSError, ValueError, AttributeError):
                continue
            if isinstance(header, dict):
                return {k: str(header.get(k) or "").strip() for k in ("court", "case_number", "party", "name")}
    return None


def sender_title_from_mints(header: dict[str, str]) -> str:
    """VA mints の提出者の名前（例：原告訴訟代理人弁護士　甲野太郎）から、送信者の肩書を取る。"""
    name = header.get("name", "").split("\n")[0]
    index = name.find("弁護士")
    if index >= 0:
        return name[: index + len("弁護士")].strip()
    party = header.get("party", "")
    return f"{party}訴訟代理人弁護士" if party else ""


def initial_input(today: _dt.date, documents: list[DocItem],
                  previous: Optional[CoverInput] = None,
                  folder_latest: Optional[CoverInput] = None,
                  mints: Optional[dict[str, str]] = None) -> CoverInput:
    """入力の画面の初めの値（F-36の4）。previousは、この文書の送付書の入力（あれば、そのまま使う）。"""
    if previous is not None:
        result = previous.copy()
        result.date = wareki(today)
        if not result.documents:
            result.documents = documents
        return result
    result = CoverInput(date=wareki(today), documents=documents)
    if folder_latest is not None:
        for name in ("court", "court_fax", "other", "other_honorific", "other_fax", "sender_title",
                     "case_number", "case_name", "parties", "receipt"):
            setattr(result, name, getattr(folder_latest, name))
    elif mints is not None:
        result.court = mints.get("court", "")
        result.case_number = mints.get("case_number", "")
        result.sender_title = sender_title_from_mints(mints)
    return result


# ---------------------------------------------------------------------- ページの作成

# 明朝体（PDF に埋め込む TrueType）。VA mints の証拠説明書と同じ順。PostScript の字形の OpenType
# （Mac のヒラギノなど）は reportlab で埋め込めないため、見つからなければ組み込みの CID フォント
# （HeiseiMin-W3。埋め込まない）にする。
_FONT_CANDIDATES = (
    (r"C:\Windows\Fonts\msmincho.ttc", 0),
    (r"C:\Windows\Fonts\BIZ-UDMinchoM.ttc", 0),
    (r"C:\Windows\Fonts\yumin.ttf", 0),
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
    ("/Library/Fonts/Arial Unicode.ttf", 0),
)
_FONT_NAME = "VAFaxMincho"
_registered_font: Optional[str] = None


def _font() -> str:
    """PDF に使うフォントの名前（初めて呼んだときに登録する）。"""
    global _registered_font
    if _registered_font is not None:
        return _registered_font
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    for path, index in _FONT_CANDIDATES:
        if not Path(path).exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont(_FONT_NAME, path, subfontIndex=index))
            _registered_font = _FONT_NAME
            return _registered_font
        except Exception:  # noqa: BLE001 - 読めないフォントは、とばして次を試す
            continue
    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiMin-W3"))
    _registered_font = "HeiseiMin-W3"
    return _registered_font


_FONT_SIZE = 11
_LEADING = 16
_MM = 72 / 25.4


def _lines(text: str) -> list[str]:
    return [line.rstrip() for line in text.strip().split("\n")] if text.strip() else []


def _markup(text: str) -> str:
    """段落の文の書き方にする。全角の空白は、詰められないよう、改行しない空白2つにする
    （reportlab は空白の並びを1つに詰めるため。「令和　　年　　月　　日」の欄が消えないように）。"""
    return escape(text).replace("　", "&nbsp;&nbsp;")


def _para(text: str, style):
    """欄の文。改行は行ごとの段落にする（空の行は全角の空白1字にして高さを残す）。"""
    from reportlab.platypus import Paragraph

    lines = text.split("\n")
    if len(lines) == 1:
        return Paragraph(_markup(text), style)
    return [Paragraph(_markup(line or "　"), style) for line in lines]


def _fax_text(fax: str) -> str:
    return f"（FAX　{fax.strip()}）" if fax.strip() else ""


def _story(cover: CoverInput, sender: Sender, total_pages: int, width: float) -> list:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import HRFlowable, Spacer, Table, TableStyle

    font = _font()
    base = ParagraphStyle("vf", fontName=font, fontSize=_FONT_SIZE, leading=_LEADING,
                          wordWrap="CJK", alignment=TA_LEFT)
    right = ParagraphStyle("vf_r", parent=base, alignment=TA_RIGHT)
    center = ParagraphStyle("vf_c", parent=base, alignment=TA_CENTER)
    big = ParagraphStyle("vf_b", parent=base, fontSize=12, leading=18)
    title = ParagraphStyle("vf_t", parent=center, fontSize=18, leading=24, spaceBefore=10, spaceAfter=10)
    receipt_title = ParagraphStyle("vf_rt", parent=center, fontSize=14, leading=20, spaceBefore=4, spaceAfter=6)

    story: list = []

    def add(item) -> None:
        story.extend(item if isinstance(item, list) else [item])

    def plain(style, rows, col_widths, align="LEFT", valign="TOP"):
        table = Table(rows, colWidths=col_widths, hAlign=align)
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font), ("VALIGN", (0, 0), (-1, -1), valign),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ]))
        return table

    # 送信日
    if cover.date.strip():
        add(_para(cover.date.strip(), right))
        story.append(Spacer(1, 6))

    # 宛先
    for name, honorific, fax in ((cover.court, COURT_HONORIFIC, cover.court_fax),
                                 (cover.other, cover.other_honorific, cover.other_fax)):
        if name.strip():
            add(_para(f"{name.strip()}　{honorific}{_fax_text(fax)}", big))

    # 送信者（右寄せの塊。中の行は左寄せ）
    sender_lines: list[str] = []
    if sender.office.strip():
        sender_lines.append(sender.office.strip())
    sender_lines += _lines(sender.address)
    contact = "　".join(s for s in (f"TEL　{sender.tel.strip()}" if sender.tel.strip() else "",
                                    f"FAX　{sender.fax.strip()}" if sender.fax.strip() else "") if s)
    if contact:
        sender_lines.append(contact)
    who = "　".join(s for s in (cover.sender_title.strip(), sender.lawyer.strip()) if s)
    if who:
        sender_lines.append(who)
    if sender_lines:
        longest = max(stringWidth(line, font, _FONT_SIZE) for line in sender_lines)
        block = min(width * 0.62, max(width * 0.42, longest + 4))
        story.append(Spacer(1, 6))
        story.append(plain(base, [[_para(line, base)] for line in sender_lines], [block], align="RIGHT"))

    # 表題
    add(_para("送　付　書（兼受領書）" if cover.receipt else "送　付　書", title))

    # 事件の表示
    case_rows = [(label, value.strip()) for label, value in (
        ("事件番号", cover.case_number), ("事件名", cover.case_name), ("当事者", cover.parties)) if value.strip()]
    if case_rows:
        story.append(plain(base, [[_para(label, base), _para(value, base)] for label, value in case_rows],
                           [22 * _MM, width - 22 * _MM]))
        story.append(Spacer(1, 6))

    # 送付の文と送信枚数
    story.append(plain(base, [[_para("下記の書類を送付します。", base),
                               _para(f"送信枚数　本書を含め　{total_pages}　枚", right)]],
                       [width * 0.5, width * 0.5], valign="BOTTOM"))
    story.append(Spacer(1, 4))

    # 書類の一覧
    documents = [d for d in cover.documents if d.name.strip() or d.pages.strip()]
    count_width = 22 * _MM
    rows = [[_para("書　類", center), _para("枚数", center)]]
    rows += [[_para(d.name.strip(), base), _para(d.pages.strip(), center)] for d in documents]
    if not documents:
        rows.append([_para("", base), _para("", center)])
    grid = Table(rows, colWidths=[width - count_width, count_width], repeatRows=1)
    grid.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font), ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(grid)
    story.append(Spacer(1, 8))

    # 通信欄（枠で囲む）。何も書いていないときは、欄そのものを書かない（2026-11-18 班長指示）
    if cover.message.strip():
        message = [_para("通信欄", base)]
        for line in _lines(cover.message):
            message.append(_para(line or "　", base))
        box = Table([[message]], colWidths=[width], rowHeights=[None])
        box.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.6, colors.black), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(box)

    if not cover.receipt:
        return story

    # 受領書の欄（返送の頼み・点線・受領書）
    back_to = "当職及び裁判所" if cover.court.strip() else "当職"
    story.append(Spacer(1, 8))
    add(_para(f"お手数ですが、下記の受領書に記名押印のうえ、{back_to}あてにFAXでご返送ください。", base))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=0.6, color=colors.black, dash=(4, 3),
                            spaceBefore=2, spaceAfter=6))
    add(_para("受　領　書", receipt_title))
    if cover.court.strip():
        add(_para(f"{cover.court.strip()}　{COURT_HONORIFIC}", base))
    if who:
        add(_para(f"{who}　{cover.other_honorific}", base))
    story.append(Spacer(1, 6))
    add(_para("上記書類を受領しました。", base))
    story.append(Spacer(1, 6))
    add(_para("令和　　年　　月　　日", right))
    story.append(Spacer(1, 8))
    receiver = cover.other.strip() or "＿＿＿＿＿＿＿＿＿＿＿＿＿＿"
    add(_para(f"{receiver}　　　　印", right))
    return story


def build_pdf(cover: CoverInput, sender: Sender, total_pages: int,
              paper_mm: tuple[float, float] = (210.0, 297.0)) -> tuple[bytes, int]:
    """送付書のPDF（メモリ内）と、そのページ数を作る。用紙は縦置き。"""
    from reportlab.platypus import SimpleDocTemplate

    page_width, page_height = sorted(paper_mm)
    pagesize = (page_width * _MM, page_height * _MM)
    left, right, top, bottom = 22 * _MM, 20 * _MM, 18 * _MM, 16 * _MM
    width = pagesize[0] - left - right
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=pagesize, leftMargin=left, rightMargin=right, topMargin=top, bottomMargin=bottom,
        title="", author="", subject="", creator="", producer="",
        initialFontName=_font(), initialFontSize=_FONT_SIZE,
    )
    doc.build(_story(cover, sender, total_pages, width))
    return buffer.getvalue(), doc.page


def build_cover(cover: CoverInput, sender: Sender, other_pages: int,
                paper_mm: tuple[float, float] = (210.0, 297.0)) -> tuple[bytes, int, int]:
    """送付書を作る。送信枚数は、送付書自身のページ数を含めた文書の総ページ数とする。

    other_pagesは、送付書のほかのページ数。戻り値は（PDF, 送付書のページ数, 送信枚数）。
    送付書が2ページ以上になったときは、その枚数で作り直す（枚数の桁が変わって、さらにページ数が
    変わることはまずないが、念のため数回まで合わせる）。
    """
    cover_pages = 1
    for _ in range(4):
        total = other_pages + cover_pages
        data, pages = build_pdf(cover, sender, total, paper_mm)
        if pages == cover_pages:
            return data, pages, total
        cover_pages = pages
    return data, pages, other_pages + pages
