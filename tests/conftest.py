# -*- coding: utf-8 -*-
"""pytest共通フィクスチャ。src/ をインポートパスに追加し、プロファイル・サンプル読込を提供する。"""
from __future__ import annotations

import gc
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"

from config.profiles import get_profile  # noqa: E402


@pytest.fixture(autouse=True)
def _config_dir(tmp_path_factory, monkeypatch):
    """設定・ログ・誤りの記録の置き場所（%APPDATA%\\VA_FAX。Windows 以外はホームの .va_fax）を、試験ごとの
    一時フォルダに差し替える（班長の設定に書かない。標準 02「テスト」）。"""
    folder = tmp_path_factory.mktemp("appdata")
    monkeypatch.setenv("APPDATA", str(folder))
    if sys.platform != "win32":
        monkeypatch.setenv("HOME", str(folder))
    return folder


@pytest.fixture(scope="session")
def tk_root():
    """リボン・プレビュー欄の試験が使う、1つの Tk（試験のファイルごとに作らない。標準 02「テスト」）。

    Windows の Python では、Tk を作り直すと、まれに初期化に失敗する（「invalid command name "tcl_findLibrary"」
    「Unable to load tkdnd library」など）。メイン画面（VaFaxApp）は Tk そのものなので、test_app.py・test_mac.py は
    試験ごとに作っており、1回の実行に1件ほど、画面の試験が「画面がない環境」として飛ばされることがある
    （2026-11-19 に分かった。直すには、メイン画面の試験で1つの画面を使い回す形に組み替える。CLAUDE.md「標準と違う所」）。
    """
    import tkinter as tk

    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"画面がない環境（{e}）")
    root.withdraw()
    from gui import theme
    theme.apply_theme(root)
    yield root
    root.destroy()
    gc.collect()


@pytest.fixture(autouse=True)
def _collect_tk_garbage_in_main_thread():
    """閉じたウィンドウの部品（Tkの変数・画像）を、テストの前後に、このスレッドで片づける。

    閉じたウィンドウの部品が、後のテストの裏のスレッド（事前診断・保存・プレビューの描画）で
    ガベージコレクションにかかると、Tclが「別のスレッドからの解放」として異常終了する
    （Windows fatal exception: code 0x80000003）。画面を作るテストの前後で先に片づけておく。
    """
    gc.collect()
    yield
    gc.collect()


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
