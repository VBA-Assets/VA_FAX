# -*- coding: utf-8 -*-
"""VA FAXのアプリケーションアイコン（.ico）を生成する（ビルド用資材）。

「VA」「FAX」の2行のテキストを、Office風フラットデザインのアクセント色
（#0078d4）の角丸タイル上に白文字で描く。グラデーション・影は用いない
（グローバルUI設計方針のフラットデザイン原則に従う）。

生成先: src/resources/icon.ico（タイトルバー用・exe同梱用の両方で使う）
実行方法（リポジトリルートで）: python build/make_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CANVAS = 256
CORNER_RADIUS = 28
# 緑系（Office系のExcel緑）。青系アクセント（#0078d4）はVA_MINTSのアイコンと
# 同一になってしまうため、区別のため本ツールは緑を用いる。
BG_COLOR = "#107c41"
TEXT_COLOR = "#ffffff"
SIDE_PADDING = 30        # タイル端から文字までの左右余白
LINE_GAP = 10            # 「VA」と「FAX」の行間

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeuib.ttf",  # Segoe UI Bold
    r"C:\Windows\Fonts\arialbd.ttf",   # Arial Bold
]

OUTPUT = Path(__file__).resolve().parent.parent / "src" / "resources" / "icon.ico"
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    raise FileNotFoundError("太字フォントが見つかりません（segoeuib.ttf / arialbd.ttf）")


def _fit_font_size(text: str, max_width: int) -> int:
    """textがmax_widthに収まる最大のフォントサイズを求める。"""
    size = 10
    while True:
        font = _load_font(size + 2)
        bbox = font.getbbox(text)
        if bbox[2] - bbox[0] > max_width:
            return size
        size += 2


def main() -> None:
    image = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, CANVAS - 1, CANVAS - 1), radius=CORNER_RADIUS, fill=BG_COLOR)

    # 幅の広い「FAX」を基準に、両行共通のフォントサイズを決める（左右余白を確保）
    font_size = _fit_font_size("FAX", CANVAS - SIDE_PADDING * 2)
    font = _load_font(font_size)

    # 2行（VA / FAX）を全体として上下中央に配置する
    lines = ["VA", "FAX"]
    bboxes = [font.getbbox(t) for t in lines]
    heights = [b[3] - b[1] for b in bboxes]
    total_height = sum(heights) + LINE_GAP
    y = (CANVAS - total_height) // 2
    for text, bbox, height in zip(lines, bboxes, heights):
        width = bbox[2] - bbox[0]
        x = (CANVAS - width) // 2 - bbox[0]
        draw.text((x, y - bbox[1]), text, font=font, fill=TEXT_COLOR)
        y += height + LINE_GAP

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUTPUT, format="ICO", sizes=ICO_SIZES)
    print(f"generated: {OUTPUT}")


if __name__ == "__main__":
    main()
