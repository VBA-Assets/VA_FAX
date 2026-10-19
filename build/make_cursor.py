# -*- coding: utf-8 -*-
"""入力ファイル欄の「一覧から取り除く」操作用マウスカーソル（.cur）を生成する（ビルド用資材）。

行を入力ファイル欄の外へドラッグしている間に表示するカーソル（F-15）。
標準のカーソル（X_cursor等）にゴミ箱の絵がないため、矢印＋ゴミ箱のバッジを
32×32で描いて同梱する。Windowsの標準ドラッグカーソルに倣い、矢印は黒地に
白フチ、バッジは白地に黒フチとして、明るい地・暗い地のどちらでも見えるようにする
（フラットデザインのため、影・グラデーションは用いない）。

Pillowは.curを書き出せず、.icoからの変換もPNG圧縮のエントリになってしまう
（Windowsのカーソルとしては互換性が不確か）ため、非圧縮の32ビットDIB
（XORビットマップ＋ANDマスク）を組み立てて.curのバイト列を直接書き出す。

生成先: src/resources/trash.cur（exeにも同梱する。build/motfax.spec）
実行方法（リポジトリルートで）: python build/make_cursor.py
"""
from __future__ import annotations

import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SIZE = 32
HOTSPOT = (1, 1)  # 矢印の先端

BLACK = (0, 0, 0, 255)
WHITE = (255, 255, 255, 255)

OUTPUT = Path(__file__).resolve().parent.parent / "src" / "resources" / "trash.cur"

# 矢印の輪郭（左上を先端とする標準的な形。先端をHOTSPOTに合わせる）
ARROW = [(1, 1), (1, 17), (5, 13), (8, 19), (10, 18), (7, 12), (12, 12)]


def _draw_ink(draw: ImageDraw.ImageDraw) -> None:
    """黒い線画（矢印＋右下のゴミ箱）を描く。白フチは後段で自動生成する。"""
    draw.polygon(ARROW, fill=BLACK)

    left, right = 17, 30       # ゴミ箱の左右
    lid_y, top, bottom = 17, 19, 30
    draw.rectangle([left + 4, lid_y - 3, right - 4, lid_y - 2], fill=BLACK)  # 取っ手
    draw.rectangle([left, lid_y - 1, right, lid_y], fill=BLACK)             # ふた
    draw.rectangle([left + 1, top, right - 1, bottom], outline=BLACK, width=2)  # 本体
    for x in (left + 4, (left + right) // 2, right - 4):                    # 中の縦線
        draw.line([(x, top + 3), (x, bottom - 3)], fill=BLACK, width=1)


def _with_white_outline(ink: Image.Image) -> Image.Image:
    """黒い線画を1px膨張させた白フチを敷いて合成する（明暗どちらの地でも見えるように）。"""
    halo_alpha = ink.getchannel("A").filter(ImageFilter.MaxFilter(3))
    halo = Image.new("RGBA", ink.size, WHITE)
    halo.putalpha(halo_alpha)
    return Image.alpha_composite(halo, ink)


def _dib_bytes(image: Image.Image) -> bytes:
    """32ビットDIB（BITMAPINFOHEADER＋XORビットマップ＋ANDマスク）を組み立てる。

    アイコン・カーソルのDIBは、高さに実画像の2倍（XOR部＋AND部）を記録し、
    行は下から上の順に並べる決まりになっている。
    """
    width, height = image.size
    header = struct.pack(
        "<IiiHHIIiiII",
        40,                 # biSize
        width,              # biWidth
        height * 2,         # biHeight（XOR＋ANDの2枚分）
        1,                  # biPlanes
        32,                 # biBitCount
        0,                  # biCompression（BI_RGB＝無圧縮）
        0,                  # biSizeImage
        0, 0,               # biXPelsPerMeter / biYPelsPerMeter
        0, 0,               # biClrUsed / biClrImportant
    )

    pixels = image.load()
    xor = bytearray()
    mask = bytearray()
    for y in range(height - 1, -1, -1):  # 下の行から順に
        bits = 0
        for x in range(width):
            r, g, b, a = pixels[x, y]
            xor += bytes((b, g, r, a))   # DIBはBGRAの順
            # ANDマスクは1が「透過（背景をそのまま表示）」、0が「不透明」。
            bits = (bits << 1) | (1 if a == 0 else 0)
        mask += bits.to_bytes(width // 8, "big")  # 32px＝4バイト（4バイト境界に一致）
    return header + bytes(xor) + bytes(mask)


def main() -> None:
    ink = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    _draw_ink(ImageDraw.Draw(ink))
    image = _with_white_outline(ink)

    dib = _dib_bytes(image)
    data = struct.pack("<HHH", 0, 2, 1)  # 予約, 種別（2＝カーソル）, 画像数
    data += struct.pack(
        "<BBBBHHII",
        SIZE, SIZE, 0, 0,      # 幅・高さ・パレット色数・予約
        HOTSPOT[0], HOTSPOT[1],  # .curではplanes/bitCountの位置がホットスポットx/y
        len(dib), 6 + 16,      # データ長・データ開始位置
    )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(data + dib)
    print(f"generated: {OUTPUT}（{len(data + dib)}バイト）")


if __name__ == "__main__":
    main()
