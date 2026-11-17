# -*- coding: utf-8 -*-
"""VA FAX が使っている部品のライセンスの表示（THIRD_PARTY_NOTICES.txt）を作る（N-08。2026-11-17。
VA mints の scripts/make_notices.py にならう）。

exe に入る部品（Python・Tcl/Tk・pypdf と、pypdf が使う cryptography・cffi・fontTools・
typing_extensions、pypdfium2〔PDFium〕・Pillow と、Pillow が使う defusedxml・tkinterdnd2・tkdnd・
PyInstaller のブートローダー）と、リボンのボタンのアイコン（Fluent UI System Icons）の
ライセンスの全文を、いま入っている部品のファイルから集める。部品を足したり、版を上げたりしたら、
作り直してコミットする（build/make_release.py が配布物に入れる）。exe に実際に入った部品は、
ビルドの後に build/work/motfax/PYZ-00.toc・PKG-00.toc で確かめる（使っていない部品は
build/motfax.spec の excludes で外す）。

VA FAX は MIT License。MIT License と合わない部品（GPL・AGPL・LGPL。Ver130 までの img2pdf、
PyMuPDF など）は配るものに入れない（tests/test_license.py で確かめる）。

実行のしかた（リポジトリルートで）：
    python build/make_notices.py
"""
from __future__ import annotations

import importlib.metadata as metadata
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "THIRD_PARTY_NOTICES.txt"
PY_HOME = Path(sys.base_prefix)
# tkdnd の使用許諾は、tkdnd.tcl の先頭に書いてある（tkinterdnd2 に別のファイルがない）
TKDND_TCL = Path(metadata.distribution("tkinterdnd2").locate_file("tkinterdnd2/tkdnd/win-x64/tkdnd.tcl"))
_PDFIUM_BUILD = [
    "pdfium.txt", "pdfium-binaries.txt", "abseil.txt", "agg23.txt", "fast_float.txt", "freetype.txt", "icu.txt",
    "lcms.txt", "libjpeg_turbo.ijg", "libjpeg_turbo.md", "libopenjpeg.txt", "libpng.txt", "libtiff.txt",
    "llvm-libc.txt", "simdutf.txt", "zlib.txt",
]

# (表示する名前, 配布の名前〔版を読む。None は版なし〕, 用途, ライセンス, 全文のファイル)
COMPONENTS = [
    ("Python", None, "プログラムの言語（exe に入る。OpenSSL・libffi・zlib などの同梱の部品の条件も、この中にある）",
     "PSF License Version 2", [PY_HOME / "LICENSE.txt"]),
    ("Tcl/Tk", None, "画面", "Tcl/Tk License（BSD 形式）", [PY_HOME / "tcl" / "tk8.6" / "license.terms"]),
    ("pypdf", "pypdf", "PDF の事前診断・ページの組み立て・画像PDFの組み立て", "BSD License（3条項）",
     ["licenses/LICENSE"]),
    ("cryptography", "cryptography", "pypdf が使う（暗号の部品。起動時に読み込まれる）",
     "Apache License 2.0 または BSD License（3条項）。どちらかを選べる",
     ["licenses/LICENSE", "licenses/LICENSE.APACHE", "licenses/LICENSE.BSD"]),
    ("cffi", "cffi", "cryptography が使う", "MIT License", ["licenses/LICENSE"]),
    ("fontTools", "fonttools", "pypdf が使う（フォントの読み取り）", "MIT License",
     ["licenses/LICENSE", "licenses/LICENSE.external"]),
    ("typing_extensions", "typing_extensions", "cryptography 等が使う", "PSF License Version 2",
     ["licenses/LICENSE"]),
    ("pypdfium2", "pypdfium2",
     "PDF のページを画像にする（変換・プレビュー）。Google の PDFium と、PDFium が使う部品を含む",
     "Apache License 2.0・BSD License（3条項）。PDFium と、PDFium が使う部品のライセンスも写す",
     ["licenses/LICENSES/Apache-2.0.txt", "licenses/LICENSES/BSD-3-Clause.txt"]
     + [f"licenses/data/windows_x64/BUILD_LICENSES/{n}" for n in _PDFIUM_BUILD]),
    ("Pillow", "pillow", "画像の処理（二値化・グレースケール・縮小・G4 と JPEG の圧縮）、アイコン",
     "MIT-CMU License", ["licenses/LICENSE"]),
    ("defusedxml", "defusedxml", "Pillow が使う（画像の付加情報〔XMP〕の安全な読み取り）",
     "PSF License Version 2", ["LICENSE"]),
    ("tkinterdnd2", "tkinterdnd2", "エクスプローラーからのドラッグアンドドロップ", "MIT License", ["licenses/LICENSE"]),
    ("tkdnd", None, "エクスプローラーからのドラッグアンドドロップ（tkinterdnd2 に入っている Tk の拡張）",
     "BSD 形式（Tcl/Tk と同じ条項）", [TKDND_TCL]),
    ("PyInstaller", "pyinstaller", "実行ファイルの作成（ブートローダーが exe に入る）",
     "GNU General Public License 第2版以降（ブートローダーの例外条項付き。exe に入るブートローダーは、"
     "この例外により、VA FAX を MIT License で配ることを妨げない）", ["licenses/COPYING.txt"]),
    ("Fluent UI System Icons", None, "リボンのボタンのアイコン", "MIT License",
     [ROOT / "src" / "gui" / "icons" / "LICENSE"]),
]


def _license_files(dist: str | None, files: list) -> list[tuple[str, str]]:
    out = []
    for f in files:
        if isinstance(f, Path):
            path = f
        else:
            d = metadata.distribution(dist)
            matches = [p for p in d.files or [] if str(p).replace("\\", "/").endswith(f".dist-info/{f}")]
            if not matches:
                raise FileNotFoundError(f"{dist} の {f} が見つかりません")
            path = Path(d.locate_file(matches[0]))
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if path.suffix == ".tcl":
            # Tcl のファイル（tkdnd.tcl）は、先頭のコメント（著作権と使用許諾）だけを写す
            head = []
            for line in text.splitlines():
                if not line.startswith("#"):
                    break
                head.append(line[2:] if line.startswith("# ") else line[1:])
            text = "\n".join(head).strip("\n") + "\n"
        out.append((path.name, text))
    return out


def build() -> Path:
    parts = [
        "VA FAX が使っている部品のライセンスの表示",
        "",
        "VA FAX そのものは、MIT License で公開しています（LICENSE）。",
        "VA FAX の実行ファイル（VA_FAX.exe）には、次の部品が入っています。",
        "",
    ]
    for name, dist, use, lic, files in COMPONENTS:
        if name == "Python":
            version = sys.version.split()[0]
        elif name == "tkdnd":
            version = next(
                (p.name.removeprefix("libtkdnd").removesuffix(".dll") for p in TKDND_TCL.parent.glob("libtkdnd*.dll")), ""
            )
        else:
            version = metadata.version(dist) if dist else ""
        parts += ["=" * 78, f"{name} {version}".rstrip(), f"用途：{use}", f"ライセンス：{lic}", "=" * 78]
        for fname, text in _license_files(dist, files):
            parts += [f"--- {fname} ---", text.rstrip(), ""]
        parts.append("")
    OUTPUT.write_text("\n".join(parts) + "\n", encoding="utf-8", newline="\n")
    return OUTPUT


if __name__ == "__main__":
    print(f"作りました：{build()}")
