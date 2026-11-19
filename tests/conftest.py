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
def main_window(tmp_path_factory):
    """メイン画面（VaFaxApp。TkinterDnD の Tk そのもの）を、試験の全体で1つだけ作る（標準 02「テスト」）。

    Windows の Python では、Tk を1つのプロセスで何度も作り直すと、まれに初期化に失敗する（「invalid command name
    "tcl_findLibrary"」「Unable to load tkdnd library」など）。Ver211 までは、メイン画面を試験ごとに作り直しており、
    1回の実行に1件ほど、画面の試験が飛ばされていた（2026-11-19 に分かった。Ver212 で、この形に改めた）。
    試験ごとの状態は、test_app.py の fixture（app）が戻す。リボン・プレビュー欄の試験は、この画面の子の窓（tk_root）を使う。
    """
    import tkinter as tk

    from config.settings import Settings
    from gui import app as app_module

    with pytest.MonkeyPatch.context() as mp:
        # 作るときのログ・誤りの記録も、班長の %APPDATA% に書かない
        mp.setenv("APPDATA", str(tmp_path_factory.mktemp("appdata_main")))
        if sys.platform != "win32":
            mp.setenv("HOME", str(tmp_path_factory.getbasetemp()))
        mp.setattr(app_module, "_bring_to_front", lambda _window: None)
        try:
            window = app_module.VaFaxApp(Settings(), get_profile("motfax"))
        except tk.TclError as e:
            pytest.skip(f"画面がない環境（{e}）")
        window.withdraw()
        window.update()
    yield window
    window.destroy()
    gc.collect()


@pytest.fixture(scope="module")
def tk_root(main_window):
    """リボン・プレビュー欄の試験の置き場。メイン画面の子の窓（Toplevel）を、試験のファイルごとに作って消す
    （Tk は作らない）。"""
    import tkinter as tk

    top = tk.Toplevel(main_window)
    top.withdraw()
    yield top
    top.destroy()
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
