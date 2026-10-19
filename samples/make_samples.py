# -*- coding: utf-8 -*-
"""機能確認用サンプルPDF生成スクリプト。

要件定義書（docs/requirements.md）の各要件を検証するためのPDFを
samples/ フォルダに生成する。各ファイルの検証対象は samples/README.md を参照。
"""
import io
import math
import os
import random

from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A3, A4, A5, LETTER, landscape
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)))
# reportlab.lib.pagesizes.B4 はISO 216のB4（250×353mm）だが、
# 日本国内のFAXサービスが指す「B4」はJIS B4（257×364mm）であるため明示的に定義する。
B4 = (257 * mm, 364 * mm)
FONT = "HeiseiMin-W3"  # CIDフォント（フォントファイル不要・「テキスト付きPDF」の典型例）
pdfmetrics.registerFont(UnicodeCIDFont(FONT))

random.seed(20260719)

LOREM = (
    "本書は、インターネットFAX送信用PDF変換ツールの機能確認のために生成されたサンプル文書である。"
    "第一条　本サンプルの各ページには、小さな文字の判読性を確認するための約款調の文章を配置する。"
    "第二条　当事者は、前条の目的を達するため、誠実に協議しなければならない。"
)
SMALL = "細字判読確認：金壱百万円也（六・八・十・特・微・議・護）0123456789 O0 l1 I|"


def text_page(c, size, title, body_lines=28):
    """テキスト主体のページを描画する。"""
    w, h = size
    c.setFont(FONT, 16)
    c.drawString(20 * mm, h - 25 * mm, title)
    c.setFont(FONT, 10)
    y = h - 40 * mm
    for i in range(body_lines):
        c.drawString(20 * mm, y, f"{i + 1:02d}  {LOREM[: 60]}")
        y -= 6 * mm
        if y < 25 * mm:
            break
    c.setFont(FONT, 6)
    c.drawString(20 * mm, 15 * mm, SMALL)
    c.showPage()


def photo_image(px_w, px_h, color=True):
    """写真調（グラデーション＋ノイズ）の画像を生成する。"""
    img = Image.new("RGB", (px_w, px_h))
    d = img.load()
    for y in range(px_h):
        for x in range(px_w):
            r = int(120 + 100 * math.sin(x / 37.0) + random.randint(-25, 25))
            g = int(120 + 100 * math.sin(y / 53.0) + random.randint(-25, 25))
            b = int(120 + 100 * math.sin((x + y) / 71.0) + random.randint(-25, 25))
            if not color:
                v = (r + g + b) // 3
                r = g = b = v
            d[x, y] = (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))
    return img


def scan_like_image(px_w, px_h):
    """スキャン風画像：薄いグレーのムラ地に文字状の黒い線分を配置する。

    テキストレイヤーを持たない「文字が画像になっているページ」を模擬する
    （F-09自動判定で単純閾値が選ばれるべきページ）。
    """
    img = Image.new("L", (px_w, px_h), 235)
    d = ImageDraw.Draw(img)
    # 地のムラ（照明ムラ・紙地）
    for _ in range(px_w * px_h // 60):
        x, y = random.randrange(px_w), random.randrange(px_h)
        d.point((x, y), fill=random.randint(215, 250))
    # 文字行を線分列で模擬
    line_h = max(10, px_h // 45)
    y = line_h * 2
    while y < px_h - line_h * 2:
        x = px_w // 12
        while x < px_w - px_w // 12:
            seg = random.randint(line_h // 2, line_h * 2)
            d.rectangle([x, y, x + seg, y + line_h // 2], fill=random.randint(0, 40))
            x += seg + random.randint(3, line_h)
        y += line_h * 2
    return img.convert("RGB")


def light_color_lines_image(px_w, px_h):
    """淡い色（ピンク）の罫線・枠のみで構成された帳票風画像に、黒文字ラベルを配置する。

    輝度だけでは白背景とほぼ区別できない淡色の線を含むページを模擬する
    （F-09自動判定：単純閾値ではこの淡色線が白背景に埋もれて消えてしまうため、
    誤差拡散が選ばれるべきページ。銀行の振込依頼書等、淡色罫線の帳票で実際に
    確認された事例に基づく）。
    """
    img = Image.new("RGB", (px_w, px_h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    pink = (252, 163, 227)  # 実例に近い淡いピンク（輝度約190。単純閾値では白と判定され消える）
    margin = px_w // 20
    rows, cols = 6, 8
    row_h = (px_h - margin * 2) // rows
    col_w = (px_w - margin * 2) // cols
    for r in range(rows + 1):
        y = margin + r * row_h
        d.line([(margin, y), (px_w - margin, y)], fill=pink, width=4)
    for c_ in range(cols + 1):
        x = margin + c_ * col_w
        d.line([(x, margin), (x, px_h - margin)], fill=pink, width=4)
    d.text((margin, margin // 3), "サンプル12　淡色罫線帳票", fill=(0, 0, 0))
    return img


def draw_image_page(c, size, img, caption=None):
    w, h = size
    margin = 15 * mm
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    buf.seek(0)
    c.drawImage(ImageReader(buf), margin, margin, w - 2 * margin, h - 2 * margin,
                preserveAspectRatio=True, anchor="c")
    if caption:
        c.setFont(FONT, 9)
        c.drawString(margin, 8 * mm, caption)
    c.showPage()


def save(name):
    return os.path.join(OUT, name)


# ---- 01 A4テキスト付き（3ページ・CIDフォント） --------------------------------
c = canvas.Canvas(save("sample01_a4_text.pdf"), pagesize=A4)
for i in range(3):
    text_page(c, A4, f"サンプル01　A4テキスト文書　{i + 1}/3")
c.save()

# ---- 02 B4テキスト（2ページ） -------------------------------------------------
c = canvas.Canvas(save("sample02_b4_text.pdf"), pagesize=B4)
for i in range(2):
    text_page(c, B4, f"サンプル02　B4文書（A4へ約82%縮小されるべきページ）　{i + 1}/2")
c.save()

# ---- 03 A3テキスト（1ページ） -------------------------------------------------
c = canvas.Canvas(save("sample03_a3_text.pdf"), pagesize=A3)
text_page(c, A3, "サンプル03　A3文書（A4へ約71%縮小されるべきページ）", body_lines=40)
c.save()

# ---- 04 A4横向き（2ページ） ---------------------------------------------------
c = canvas.Canvas(save("sample04_a4_landscape.pdf"), pagesize=landscape(A4))
for i in range(2):
    text_page(c, landscape(A4), f"サンプル04　A4横向き（90度回転で縦置きに収めるべきページ）　{i + 1}/2",
              body_lines=18)
c.save()

# ---- 05 レターサイズ ----------------------------------------------------------
c = canvas.Canvas(save("sample05_letter.pdf"), pagesize=LETTER)
text_page(c, LETTER, "サンプル05　レターサイズ（±2mm許容外・A4正規化対象）")
c.save()

# ---- 06 サイズ混在（A4・B4・A3・A4横の結合を模擬） ----------------------------
c = canvas.Canvas(save("sample06_mixed_sizes.pdf"), pagesize=A4)
text_page(c, A4, "サンプル06　混在1/4：A4ページ")
c.setPageSize(B4)
text_page(c, B4, "サンプル06　混在2/4：B4ページ")
c.setPageSize(A3)
text_page(c, A3, "サンプル06　混在3/4：A3ページ", body_lines=40)
c.setPageSize(landscape(A4))
text_page(c, landscape(A4), "サンプル06　混在4/4：A4横ページ", body_lines=18)
c.save()

# ---- 07 カラー写真入りA4 ------------------------------------------------------
c = canvas.Canvas(save("sample07_color_photo.pdf"), pagesize=A4)
draw_image_page(c, A4, photo_image(1000, 1400, color=True),
                "サンプル07　カラー写真ページ（誤差拡散が選ばれるべきページ）")
c.save()

# ---- 08 スキャン風（画像のみ・テキストなし） ----------------------------------
c = canvas.Canvas(save("sample08_scan_like.pdf"), pagesize=A4)
for i in range(2):
    draw_image_page(c, A4, scan_like_image(1654, 2339),
                    f"サンプル08　スキャン風ページ（単純閾値が選ばれるべきページ）　{i + 1}/2")
c.save()

# ---- 09 内容混在（文字＋写真＋スキャン風：F-09/F-13の本命） --------------------
c = canvas.Canvas(save("sample09_mixed_content.pdf"), pagesize=A4)
text_page(c, A4, "サンプル09　内容混在1/4：テキストページ")
draw_image_page(c, A4, photo_image(1000, 1400, color=True), "サンプル09　内容混在2/4：カラー写真ページ")
draw_image_page(c, A4, scan_like_image(1654, 2339), "サンプル09　内容混在3/4：スキャン風ページ")
text_page(c, A4, "サンプル09　内容混在4/4：テキストページ")
c.save()

# ---- 10 大容量誘発（写真調10ページ：ページ単位容量制御の検証） -----------------
c = canvas.Canvas(save("sample10_photo_heavy.pdf"), pagesize=A4)
text_page(c, A4, "サンプル10　1/11：テキストページ（劣化させてはならないページ）")
for i in range(10):
    draw_image_page(c, A4, photo_image(1200, 1697, color=(i % 2 == 0)),
                    f"サンプル10　{i + 2}/11：写真調ページ（容量制御の対象候補）")
c.save()

# ---- 11 暗号化PDF（パスワード: test） -----------------------------------------
reader = PdfReader(save("sample01_a4_text.pdf"))
writer = PdfWriter()
for p in reader.pages:
    writer.add_page(p)
writer.encrypt("test")
with open(save("sample11_encrypted.pdf"), "wb") as f:
    writer.write(f)

# ---- 12 淡色罫線帳票（銀行帳票等を模擬：F-09自動判定の検証） -------------------
c = canvas.Canvas(save("sample12_light_color_lines.pdf"), pagesize=A4)
draw_image_page(c, A4, light_color_lines_image(1654, 2339),
                "サンプル12　淡色罫線帳票（誤差拡散が選ばれるべきページ）")
c.save()

# ---- 13 A5小サイズ（正規化先A4より小さいページ：拡大しないことの検証） ---------
c = canvas.Canvas(save("sample13_a5_small.pdf"), pagesize=A5)
text_page(c, A5, "サンプル13　A5文書（A4より小さく、拡大せず原寸のまま中央配置されるべきページ）",
          body_lines=14)
c.save()

print("生成完了：")
for name in sorted(os.listdir(OUT)):
    if name.endswith(".pdf"):
        size = os.path.getsize(os.path.join(OUT, name))
        print(f"  {name}  {size / 1024:.0f} KB")
