"""操作マニュアルの PDF（<名前>_操作マニュアル_VerNNN.pdf）を作る。

元は docs/manual/<名前>_操作マニュアル.md（編集用の Markdown）。雛形（VAプロジェクト\\標準\\雛形\\python）の
generate_manual.py を写したもので（名前は src/version.py の FILE_STEM から読む。Ver211 で、npm の md-to-pdf を使う
build/make_manual.py から替えた）、Markdown の一部（見出し・段落・箇条書き〔入れ子も〕・番号付きの箇条書き・表・
コードブロック・引用・`code`・**太字**・先頭のフロントマター）を読み、reportlab で PDF にする。PDF は作ったもの
（ビルドの生成物）なので、Markdown とこのスクリプトから作り直す。

実行のしかた：
    python scripts\\generate_manual.py

出力：docs/manual/<名前>_操作マニュアル_<版>.pdf（<版> はフロントマターの version から「.」を除いたもの。
例：Ver100）

書体は Windows の游ゴシック（本文）と Consolas（等幅）を埋め込む。`code` の中の日本語は、Consolas に字がないので、
ＭＳ ゴシックに切り替える（VA Document と同じ）。Windows で作ることを前提とする。
"""

from __future__ import annotations

import re
import sys
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    HRFlowable,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from version import FILE_STEM  # noqa: E402  （VA FAX は src をパッケージにしていない。CLAUDE.md「標準と違う所」）

SOURCE = ROOT / "docs" / "manual" / f"{FILE_STEM}_操作マニュアル.md"
OUTPUT_DIR = ROOT / "docs" / "manual"

# 画面（theme.py）と同じアクセントの青。見出しの左の縦の線などに使う
ACCENT = colors.HexColor("#0078d4")
HEADER_BG = colors.HexColor("#eeeeee")
QUOTE_BG = colors.HexColor("#f3f3f3")
GRID = colors.HexColor("#cccccc")
TEXT = colors.HexColor("#1a1a1a")

_FONT_REGULAR = "ManualBody"
_FONT_BOLD = "ManualBodyBold"
_FONT_MONO = "ManualMono"
_FONT_MONO_JP = "ManualMonoJP"  # code の中の日本語（Consolas にない字を補う）
_FONT_FILES = {
    _FONT_REGULAR: r"C:\Windows\Fonts\YuGothR.ttc",
    _FONT_BOLD: r"C:\Windows\Fonts\YuGothB.ttc",
    _FONT_MONO: r"C:\Windows\Fonts\consola.ttf",
    _FONT_MONO_JP: r"C:\Windows\Fonts\msgothic.ttc",  # 0 番目がＭＳ ゴシック（等幅）
}


def _register_fonts() -> None:
    for name, path in _FONT_FILES.items():
        pdfmetrics.registerFont(TTFont(name, path, subfontIndex=0))
    pdfmetrics.registerFontFamily(
        _FONT_REGULAR, normal=_FONT_REGULAR, bold=_FONT_BOLD, italic=_FONT_REGULAR, boldItalic=_FONT_BOLD
    )


# ------------------------------------------------------------------ スタイル
def _styles() -> dict[str, ParagraphStyle]:
    body = ParagraphStyle(
        "body", fontName=_FONT_REGULAR, fontSize=10, leading=17, textColor=TEXT, alignment=TA_JUSTIFY,
        wordWrap="CJK", spaceAfter=8,
    )
    return {
        "body": body,
        "title": ParagraphStyle("title", parent=body, fontName=_FONT_BOLD, fontSize=22, leading=30,
                                alignment=TA_CENTER),
        "subtitle": ParagraphStyle("subtitle", parent=body, fontSize=13, leading=20, alignment=TA_CENTER,
                                   textColor=colors.HexColor("#555555")),
        "titlemeta": ParagraphStyle("titlemeta", parent=body, fontSize=10, leading=18, alignment=TA_CENTER,
                                    textColor=colors.HexColor("#555555")),
        "chapter": ParagraphStyle("chapter", parent=body, fontName=_FONT_BOLD, fontSize=15, leading=22,
                                  spaceAfter=2, spaceBefore=0),
        "section": ParagraphStyle("section", parent=body, fontName=_FONT_BOLD, fontSize=12.5, leading=19,
                                  spaceAfter=0),
        "toc_chapter": ParagraphStyle("toc_chapter", parent=body, fontName=_FONT_BOLD, fontSize=10.5, leading=20,
                                      spaceAfter=0),
        "toc_section": ParagraphStyle("toc_section", parent=body, fontSize=10, leading=18, leftIndent=16,
                                      spaceAfter=0),
        "cell": ParagraphStyle("cell", parent=body, spaceAfter=0, leading=15, fontSize=9.5),
        "cellhead": ParagraphStyle("cellhead", parent=body, fontName=_FONT_BOLD, spaceAfter=0, leading=15,
                                   fontSize=9.5),
        "listitem": ParagraphStyle("listitem", parent=body, spaceAfter=3),
        "quote": ParagraphStyle("quote", parent=body, spaceAfter=0, leading=16, fontSize=9.5),
        "code": ParagraphStyle("code", fontName=_FONT_MONO_JP, fontSize=9, leading=13, textColor=TEXT),
    }


def _code_span(content: str) -> str:
    """code の中を、書体を指定したマークアップにする（ASCII 以外は和文の等幅に切り替える）。"""
    out = []
    for part in re.split(r"([^\x00-\x7f]+)", content):
        if part:
            face = _FONT_MONO_JP if any(ord(c) > 127 for c in part) else _FONT_MONO
            out.append(f'<font face="{face}" size="9">{part}</font>')
    return "".join(out)


def _inline(text: str) -> str:
    """Markdown の `code` と **太字** を、reportlab のマークアップにする。code の中の * は太字にしない
    （`***本件契約***` などをそのまま見せるため）。"""
    codes: list[str] = []

    def keep(m: re.Match) -> str:
        codes.append(_code_span(m.group(1)))
        return f"\x00{len(codes) - 1}\x00"
    out = re.sub(r"`(.+?)`", keep, escape(text))
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], out)


# ------------------------------------------------------------------ 見出しの縦の線
class SectionHeading(Flowable):
    """節の見出し（例「1.1 これは何をするツールか」）。左に色の付いた縦の線を描く。"""

    def __init__(self, text: str, style: ParagraphStyle) -> None:
        super().__init__()
        self._text = text
        self._style = style
        self._bar_w = 3.5
        self._gap = 8
        self.keepWithNext = 1      # 見出しだけがページの下に残らないように

    def wrap(self, avail_w: float, avail_h: float) -> tuple[float, float]:
        self._para = Paragraph(_inline(self._text), self._style)
        self._pw, self._ph = self._para.wrap(avail_w - self._bar_w - self._gap, avail_h)
        self._h = max(self._ph, self._style.fontSize)
        return avail_w, self._h

    def draw(self) -> None:
        c = self.canv
        c.setFillColor(ACCENT)
        c.rect(0, 0, self._bar_w, self._h, stroke=0, fill=1)
        self._para.drawOn(c, self._bar_w + self._gap, self._h - self._ph)


# ------------------------------------------------------------------ Markdown を読む
def _parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    meta: dict[str, str] = {}
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)
        for line in fm.strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        return meta, body.strip()
    return meta, text


def _split_blocks(body: str) -> list[list[str]]:
    """空の行で塊に分ける。見出しの行は、それだけで1つの塊にする。コードブロック（```）の中は分けない。"""
    blocks: list[list[str]] = []
    cur: list[str] = []
    in_code = False
    for line in body.splitlines():
        if line.strip().startswith("```"):
            if in_code:
                cur.append(line)
                blocks.append(cur)
                cur, in_code = [], False
                continue
            if cur:
                blocks.append(cur)
            cur, in_code = [line], True
            continue
        if in_code:
            cur.append(line)
        elif line.strip() == "":
            if cur:
                blocks.append(cur)
                cur = []
        elif line.startswith("#"):
            if cur:
                blocks.append(cur)
            blocks.append([line])
            cur = []
        else:
            cur.append(line)
    if cur:
        blocks.append(cur)
    return blocks


def _split_row(line: str) -> list[str]:
    """表の1行をセルに分ける。`code` の中の「|」と「\\|」は区切りにせず、字として残す
    （更新履歴の「`|:--|--:|`」で、列が増えて表が崩れたため）。"""
    cells: list[str] = []
    cur: list[str] = []
    in_code = False
    s = line.strip()
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and s[i + 1:i + 2] == "|":
            cur.append("|")
            i += 2
            continue
        if ch == "`":
            in_code = not in_code
        elif ch == "|" and not in_code:
            cells.append("".join(cur).strip())
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    cells.append("".join(cur).strip())
    if s.startswith("|"):
        cells = cells[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        cells = cells[:-1]
    return cells


def _table_flowable(lines: list[str], styles: dict[str, ParagraphStyle]) -> Table:
    rows = [_split_row(ln) for ln in lines]
    for ln, r in zip(lines, rows):
        if len(r) != len(rows[0]):
            raise ValueError(f"表の列の数が見出しの行（{len(rows[0])}列）と違う（{len(r)}列）：{ln[:60]}")
    header, data = rows[0], rows[2:]          # 2行目（---|---）は区切り
    table_data = [[Paragraph(_inline(c), styles["cellhead"]) for c in header]]
    for r in data:
        table_data.append([Paragraph(_inline(c), styles["cell"]) for c in r])
    # 列の幅は、列のいちばん長いセルの字数（上限あり）に合わせて配分する（短い列は狭く）
    ncol = len(header)
    avail = A4[0] - 40 * mm
    longest = [max(min(len(re.sub(r"[`*]", "", r[k] if k < len(r) else "")), 60) for r in [header] + data)
               for k in range(ncol)]
    weights = [max(w, 6) ** 0.8 for w in longest]
    widths = [avail * w / sum(weights) for w in weights]
    table = Table(table_data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("GRID", (0, 0), (-1, -1), 0.5, GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


_ITEM_RE = re.compile(r"^(\s*)(-|\d+\.)\s+(.*)$")


def _list_flowable(lines: list[str], styles: dict[str, ParagraphStyle]) -> ListFlowable:
    """箇条書き・番号付きの箇条書き。字下げした行（空白2つ以上）の「- 」は、前の項目の中の箇条書きにする。
    項目の続きの行（記号のない行）は、前の項目の字に続ける。"""
    top: list[tuple[str, list[str]]] = []      # (項目の字, 中の箇条書きの字のリスト)
    ordered = bool(re.match(r"^\s*\d+\.", lines[0]))
    for ln in lines:
        m = _ITEM_RE.match(ln)
        if m and len(m.group(1)) >= 2 and top:
            top[-1][1].append(m.group(3))
        elif m:
            top.append((m.group(3), []))
        elif top:
            text, subs = top[-1]
            if subs:
                subs[-1] += " " + ln.strip()
            else:
                top[-1] = (text + " " + ln.strip(), subs)
    items = []
    for text, subs in top:
        content: list[Flowable] = [Paragraph(_inline(text), styles["listitem"])]
        if subs:
            content.append(ListFlowable(
                [ListItem(Paragraph(_inline(s), styles["listitem"]), leftIndent=12) for s in subs],
                bulletType="bullet", start="・", bulletFontName=_FONT_REGULAR, bulletColor=TEXT, leftIndent=14,
                bulletFontSize=9))
        items.append(ListItem(content, leftIndent=14))
    return ListFlowable(
        items, bulletType="1" if ordered else "bullet", bulletFormat="%s." if ordered else None,
        bulletFontName=_FONT_REGULAR, bulletColor=ACCENT if not ordered else TEXT, leftIndent=18, bulletFontSize=9,
    )


def _boxed(flowable: Flowable, background) -> Table:
    """枠のない、地に色の付いた箱に入れる（引用・コードブロック）。"""
    box = Table([[flowable]], colWidths=[A4[0] - 40 * mm])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), background),
        ("LINEBEFORE", (0, 0), (0, -1), 2, GRID),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return box


def build() -> Path:
    _register_fonts()
    styles = _styles()
    meta, body = _parse_front_matter(SOURCE.read_text(encoding="utf-8"))
    story: list[Flowable] = []

    # 表紙
    story.append(Spacer(1, 60 * mm))
    story.append(Paragraph(_inline(meta.get("title", "")), styles["title"]))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(meta.get("version", ""), styles["subtitle"]))
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(f"作成者：{meta.get('author', '')}　配布元：{meta.get('distributor', '')}",
                           styles["titlemeta"]))
    story.append(Paragraph(meta.get("date", ""), styles["titlemeta"]))
    story.append(PageBreak())

    blocks = _split_blocks(body)

    # 目次（「# 第N章」と「## 」の見出しから作る）
    story.append(Paragraph("目次", styles["chapter"]))
    story.append(HRFlowable(width="100%", thickness=1, color=TEXT, spaceBefore=4, spaceAfter=10))
    for block in blocks:
        head = block[0]
        if head.startswith("# ") and "更新履歴" not in head:
            story.append(Paragraph(_inline(head[2:].strip()), styles["toc_chapter"]))
        elif head.startswith("## "):
            story.append(Paragraph(_inline(head[3:].strip()), styles["toc_section"]))
    story.append(PageBreak())

    # 本文
    for block in blocks:
        head = block[0]
        if head.startswith("# "):
            if story and not isinstance(story[-1], PageBreak):
                story.append(PageBreak())
            story.append(Paragraph(_inline(head[2:].strip()), styles["chapter"]))
            story.append(HRFlowable(width="100%", thickness=1, color=TEXT, spaceBefore=4, spaceAfter=12))
        elif head.startswith("## "):
            story.append(Spacer(1, 4))
            story.append(SectionHeading(head[3:].strip(), styles["section"]))
            story.append(Spacer(1, 8))
        elif head.strip().startswith("```"):
            code = "\n".join(block[1:-1] if block[-1].strip().startswith("```") else block[1:])
            story.append(_boxed(Preformatted(code, styles["code"]), QUOTE_BG))
            story.append(Spacer(1, 8))
        elif head.startswith(">"):
            text = " ".join(ln.lstrip(">").strip() for ln in block)
            story.append(_boxed(Paragraph(_inline(text), styles["quote"]), QUOTE_BG))
            story.append(Spacer(1, 8))
        elif head.strip().startswith("|"):
            story.append(_table_flowable(block, styles))
            story.append(Spacer(1, 8))
        elif _ITEM_RE.match(head):
            story.append(_list_flowable(block, styles))
            story.append(Spacer(1, 6))
        elif len(block) == 1 and re.fullmatch(r"\*\*[^*]+\*\*", head.strip()):
            # 太字だけの1行（「**保存について**」など）は小見出しなので、次の塊と同じページに置く
            story.append(Paragraph(_inline(head), ParagraphStyle("lead", parent=styles["body"], keepWithNext=1)))
        else:
            story.append(Paragraph(_inline(" ".join(block)), styles["body"]))

    version_slug = meta.get("version", "Ver.000").replace(".", "").replace(" ", "")
    output = OUTPUT_DIR / f"{FILE_STEM}_操作マニュアル_{version_slug}.pdf"

    def _page_number(canvas, _doc) -> None:
        canvas.setFont(_FONT_REGULAR, 9)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawCentredString(A4[0] / 2, 12 * mm, str(canvas.getPageNumber()))

    doc = SimpleDocTemplate(
        str(output), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=22 * mm, bottomMargin=20 * mm,
        title=meta.get("title", ""), author=meta.get("author", ""),
    )
    doc.build(story, onFirstPage=_page_number, onLaterPages=_page_number)
    return output


if __name__ == "__main__":
    path = build()
    print(f"作りました：{path}")
