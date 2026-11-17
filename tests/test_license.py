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
    text = (ROOT / "build" / "motfax.spec").read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#") and not line.lstrip().startswith("excludes="))
    assert not [name for name in _FORBIDDEN if name in code]


def test_spec_excludes_incompatible_packages():
    """Ver130 まで入っていた img2pdf（LGPL-3.0）と、それが使っていた pikepdf を、exe から外す指定がある。"""
    text = (ROOT / "build" / "motfax.spec").read_text(encoding="utf-8")
    excludes = [line for line in text.splitlines() if line.lstrip().startswith("excludes=")]
    assert len(excludes) == 1 and '"img2pdf"' in excludes[0] and '"pikepdf"' in excludes[0]
