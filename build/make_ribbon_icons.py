# -*- coding: utf-8 -*-
"""リボンのボタンのアイコンを、SVGからPNGにする（開発時のみ実行。VA mintsのmake_ribbon_icons.pyと同じ）。

アイコンはMicrosoftのFluent UI System Icons（MIT License。build/icons_src/LICENSE）の20pxのSVG。
VA mints・VA Case・VA Documentと同じく2色にする：塗りつぶし版（<名前>_filled.svg）を薄い青
（theme.COLOR_ICON_TINT）で下に敷き、その上に線画を濃い色（theme.COLOR_TEXT）で重ねる。
押せないときの版は灰色（COLOR_DISABLED_TEXT・COLOR_ICON_TINT_DISABLED）。
大きいボタン用の色付きアイコン（<名前>_large.svg）があれば、それもPNGにする。

PyMuPDFはAGPLのため、配布物（requirements.txt・exe）には含めない。ここで一度PNGにして
src/gui/icons/へ置き、起動時はPillowで画面の拡大率に合わせて縮める（src/gui/icons.py）。
このスクリプトだけがPyMuPDFを使う（開発環境にのみ存在するもの）。

使い方：python build/make_ribbon_icons.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import fitz  # PyMuPDF（開発時のみ）

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gui import theme  # noqa: E402

SRC = ROOT / "build" / "icons_src"
OUT = ROOT / "src" / "gui" / "icons"
SVG_FILL = "#212121"  # Fluent UI System IconsのSVGの塗り色
SMALL = 64   # 小さいアイコンのPNGの大きさ（ピクセル。起動時に縮める）
LARGE = 128  # 大きいボタン用の色付きアイコンのPNGの大きさ


def _body(svg: str) -> str:
    """SVGの<svg>〜</svg>の中身。"""
    match = re.search(r"<svg[^>]*>(.*)</svg>", svg, re.S)
    assert match is not None
    return match.group(1)


def _png(svg: str, size: int) -> bytes:
    with fitz.open(stream=svg.encode("utf-8"), filetype="svg") as doc:
        page = doc[0]
        zoom = size / page.rect.width
        return page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=True).tobytes("png")


def two_tone(name: str, color: str, tint: str) -> str:
    """線画（color）の下に、塗りつぶし版（tint）を敷いた2色のSVGを作る。"""
    svg = (SRC / f"{name}.svg").read_text(encoding="utf-8").replace(SVG_FILL, color)
    filled = SRC / f"{name}_filled.svg"
    if filled.exists():
        head = re.match(r"<svg[^>]*>", svg)
        assert head is not None
        svg = head.group(0) + _body(filled.read_text(encoding="utf-8")).replace(SVG_FILL, tint) \
            + _body(svg) + "</svg>"
    return svg


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    names = sorted(p.stem for p in SRC.glob("*.svg")
                   if not p.stem.endswith(("_filled", "_large")))
    for name in names:
        (OUT / f"{name}.png").write_bytes(
            _png(two_tone(name, theme.COLOR_TEXT, theme.COLOR_ICON_TINT), SMALL))
        (OUT / f"{name}_disabled.png").write_bytes(
            _png(two_tone(name, theme.COLOR_DISABLED_TEXT, theme.COLOR_ICON_TINT_DISABLED), SMALL))
        large = SRC / f"{name}_large.svg"
        if large.exists():
            (OUT / f"{name}_large.png").write_bytes(
                _png(large.read_text(encoding="utf-8"), LARGE))
    license_src = SRC / "LICENSE"
    if license_src.is_file():
        (OUT / "LICENSE").write_bytes(license_src.read_bytes())
    print(f"{len(names)}個のアイコンをPNGにしました：{OUT}")


if __name__ == "__main__":
    main()
