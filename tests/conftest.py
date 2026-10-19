# -*- coding: utf-8 -*-
"""pytest共通フィクスチャ。src/ をインポートパスに追加し、プロファイル・サンプル読込を提供する。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"

from config.profiles import get_profile  # noqa: E402


@pytest.fixture(scope="session")
def motfax():
    return get_profile("motfax")


@pytest.fixture(scope="session")
def movfax():
    return get_profile("movfax")


@pytest.fixture(scope="session")
def messageplus():
    return get_profile("messageplus")


@pytest.fixture(scope="session")
def sokusoku():
    return get_profile("sokusoku_fax")


@pytest.fixture
def sample_bytes():
    """サンプルPDFのファイル名からバイト列を読み込むヘルパーを返す。"""

    def _load(name: str) -> bytes:
        return (SAMPLES_DIR / name).read_bytes()

    return _load


@pytest.fixture
def corrupted_pdf_bytes(sample_bytes):
    """正常なサンプルPDFを途中で切り詰めた破損PDF（リポジトリには保存しない）。"""
    data = sample_bytes("sample01_a4_text.pdf")
    return data[: len(data) // 2]
