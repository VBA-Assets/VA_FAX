# -*- coding: utf-8 -*-
"""配るプログラム（src）が、MIT License と合わない部品を使っていないことを確かめる（N-08）。

VA FAX は MIT License。GPL・AGPL・LGPL の部品を exe に入れると、その部品の条件を配布物で
満たさなければならなくなる。Ver130 まで img2pdf（LGPL-3.0）を使っていたので、Ver200 で外して
自前にした（core/convert.py の build_pdf）。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
# MIT License と合わない部品（import の名前）
_FORBIDDEN = {
    "img2pdf": "LGPL-3.0",
    "fitz": "AGPL-3.0（PyMuPDF）",
    "pymupdf": "AGPL-3.0（PyMuPDF）",
    "mutagen": "GPL-2.0-or-later",
}


def test_src_does_not_import_incompatible_packages():
    found = []
    for path in SRC.rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*(?:from|import)\s+([A-Za-z0-9_]+)", line)
            if m and m.group(1).lower() in _FORBIDDEN:
                found.append(f"{path.relative_to(SRC)}：{line.strip()}（{_FORBIDDEN[m.group(1).lower()]}）")
    assert not found, "\n".join(found)


def test_requirements_do_not_list_incompatible_packages():
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    listed = {re.split(r"[<>=!~\[ ]", line.strip(), maxsplit=1)[0].lower() for line in text.splitlines()
              if line.strip() and not line.lstrip().startswith("#")}
    assert not listed & set(_FORBIDDEN)


def test_spec_does_not_collect_incompatible_packages():
    """PyInstaller の定義が、合わない部品を名指しで集めていないこと（外す指定〔excludes〕は除く）。"""
    text = (ROOT / "build" / "va_fax.spec").read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#") and not line.lstrip().startswith("excludes="))
    assert not [name for name in _FORBIDDEN if name in code]


def test_spec_excludes_incompatible_packages():
    """Ver130 まで入っていた img2pdf（LGPL-3.0）と、それが使っていた pikepdf を、exe から外す指定がある。"""
    text = (ROOT / "build" / "va_fax.spec").read_text(encoding="utf-8")
    excludes = [line for line in text.splitlines() if line.lstrip().startswith("excludes=")]
    assert len(excludes) == 1 and '"img2pdf"' in excludes[0] and '"pikepdf"' in excludes[0]


# ---------------------------------------------------------------- 雛形と同じ確かめ（標準 02「テスト」。Ver211）
# 配るプログラムが読み込む外の部品：{import の名前: 配布の名前}。標準ライブラリ（tkinter を含む）は書かない。
# 部品を足したら、ここと requirements.txt に書き足し、python build/make_notices.py で THIRD_PARTY_NOTICES.txt を
# 作り直す。
RUNTIME_PACKAGES = {"pypdfium2": "pypdfium2", "pypdfium2_raw": "pypdfium2", "PIL": "pillow", "pypdf": "pypdf",
                    "tkinterdnd2": "tkinterdnd2", "reportlab": "reportlab"}
# MIT License と両立するライセンス（配布の情報の License・Classifier に、どれかの語が入っていること）
COMPATIBLE = ("MIT", "BSD", "Apache", "PSF", "Python Software Foundation", "ISC", "HPND", "MIT-CMU", "Zlib")
# src の直下の、このツール自身のモジュール（VA FAX は src をパッケージにしていない。CLAUDE.md「標準と違う所」）
OWN_MODULES = {"config", "core", "gui", "diagnostics", "logging_setup", "version", "main"}


def _imported_top_modules():
    import ast
    import sys

    found = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return found - set(sys.stdlib_module_names) - OWN_MODULES


def test_only_listed_packages_are_imported():
    imported = _imported_top_modules()
    assert imported <= set(RUNTIME_PACKAGES), f"RUNTIME_PACKAGES にない部品：{sorted(imported - set(RUNTIME_PACKAGES))}"


def test_runtime_packages_are_mit_compatible_and_noticed():
    import importlib.metadata as metadata

    notices = (ROOT / "THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8")
    for module, dist in RUNTIME_PACKAGES.items():
        meta = metadata.metadata(dist)
        text = " ".join([meta.get("License", "") or "", meta.get("License-Expression", "") or ""]
                        + [c for c in meta.get_all("Classifier") or [] if c.startswith("License")])
        assert any(word in text for word in COMPATIBLE), f"{dist} のライセンスを確かめる：{text}"
        assert "GPL" not in text.replace("LGPL", ""), f"{dist} は GPL"
        assert dist.lower() in notices.lower(), f"{dist} が THIRD_PARTY_NOTICES.txt にない"


def test_license_file():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License")
    assert "VBAアセット" in text and "All Rights Reserved" not in text
