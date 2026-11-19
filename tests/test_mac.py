# -*- coding: utf-8 -*-
"""Mac（ソースから動かす場合。N-03）向けの分岐のテスト。

Macの実機は無いため、sys.platform・Tkのウィンドウシステム名（theme.windowing_system）を
Mac（darwin・aqua）に差し替えて、Mac側の分岐を通す。あわせて、Windows側の結果が
従来と変わらないことも確かめる。

画面の試験は、Tkを1つのプロセスで何度も作り直さないよう（Windowsのpythonでは、
まれに初期化に失敗する）、conftest のメイン画面（main_window。試験の全体で1つ）を使う。
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace

import pytest

from config import profiles as profiles_module
from config import settings as settings_module
from gui import app as app_module
from gui import theme
from gui.preview import PreviewPane, wheel_scroll_units
import logging_setup


# ---------------------------------------------------------------- 管理領域の場所

def test_app_data_dir_on_windows_is_unchanged(tmp_path, monkeypatch):
    """Windowsでは従来どおり%APPDATA%\\VA_FAXとする。"""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert settings_module.app_data_dir() == tmp_path / "VA_FAX"
    assert profiles_module.appdata_profiles_dir() == tmp_path / "VA_FAX" / "profiles"
    assert logging_setup.logs_dir() == tmp_path / "VA_FAX" / "logs"


def test_app_data_dir_on_windows_without_appdata_is_unchanged(monkeypatch):
    """WindowsでAPPDATAが無い場合の代替先も従来どおりとする。"""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("APPDATA", raising=False)
    assert settings_module.app_data_dir() == Path.home() / "AppData" / "Roaming" / "VA_FAX"


def test_app_data_dir_on_mac_is_home_dot_va_fax(tmp_path, monkeypatch):
    """Macではホームの.va_faxとする（APPDATAの有無によらない）。"""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    expected = Path.home() / ".va_fax"
    assert settings_module.app_data_dir() == expected
    assert settings_module.settings_path() == expected / "settings.json"
    assert profiles_module.appdata_profiles_dir() == expected / "profiles"
    assert logging_setup.logs_dir() == expected / "logs"


# ---------------------------------------------------------------- ホイール

@pytest.mark.parametrize("delta, expected", [(120, -1), (-120, 1), (-240, 2), (360, -3), (60, 0)])
def test_wheel_units_on_windows_are_unchanged(delta, expected):
    """Windowsでは従来どおり120で割る（120未満の値は0行）。"""
    assert wheel_scroll_units(delta, aqua=False) == expected


@pytest.mark.parametrize("delta, expected", [(1, -1), (-1, 1), (-3, 3), (5, -5), (0, 0),
                                             (120, -1), (-240, 2)])
def test_wheel_units_on_mac(delta, expected):
    """Macでは120未満の値をそのまま行数にする（Tk 8.6は1刻みを±1程度で送る）。"""
    assert wheel_scroll_units(delta, aqua=True) == expected


# ---------------------------------------------------------------- 出力フォルダを開く

def test_open_folder_on_windows_uses_startfile(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(app_module.os, "startfile", opened.append, raising=False)
    monkeypatch.setattr(app_module.subprocess, "Popen", lambda *a, **k: pytest.fail("Popen"))
    app_module._open_folder(tmp_path)
    assert opened == [tmp_path]


@pytest.mark.parametrize("platform, opener", [("darwin", "open"), ("linux", "xdg-open")])
def test_open_folder_outside_windows_uses_opener(tmp_path, monkeypatch, platform, opener):
    calls = []
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(app_module.os, "startfile", lambda *_a: pytest.fail("startfile"),
                        raising=False)
    monkeypatch.setattr(app_module.subprocess, "Popen", lambda args, **_k: calls.append(args))
    app_module._open_folder(tmp_path)
    assert calls == [[opener, str(tmp_path)]]


# ---------------------------------------------------------------- 画面（Macを装う）

_MAC_KEYS = ("<Command-z>", "<Command-Z>", "<Command-y>", "<Command-Y>", "<Command-o>", "<Command-n>",
             "<Command-s>", "<Command-Shift-S>", "<Command-Shift-s>")


@pytest.fixture(scope="module")
def mac_app(main_window):
    """Tkのウィンドウシステムをaquaに見せかけて、メイン画面でMacのときの処理（アイコン・⌘のキー・
    プレビュー欄）を通す。

    メイン画面は TkinterDnD の Tk そのものなので、作り直さず、conftest の1つ（main_window）を使う
    （作り直すと、まれに Tk の初期化に失敗する。標準 02「テスト」）。起動のときに aqua で通る処理
    （theme.apply_app_icon・_bind_mac_keys・PreviewPane の作成）を、ここで同じように呼ぶ。終わったら、
    ⌘のキーと::tk::mac::Quitを外し、アイコンを Windows のものに戻す（後の試験に残さない）。
    """
    window = main_window
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(theme, "windowing_system", lambda _widget: "aqua")
        assert theme.is_aqua(window)
        theme.apply_app_icon(window)
        window._bind_mac_keys()
        top = tk.Toplevel(window)
        top.withdraw()
        pane = PreviewPane(top)
        window.update()
    try:
        yield SimpleNamespace(window=window, preview_pane=pane)
    finally:
        pane.destroy()
        top.destroy()
        for sequence in _MAC_KEYS:
            window.unbind_all(sequence)
        window.deletecommand("::tk::mac::Quit")
        if hasattr(window, "_va_fax_icon_photo"):
            del window._va_fax_icon_photo
        theme.apply_app_icon(window)


def test_main_window_binds_mac_keys_only_on_aqua():
    """起動のとき、⌘のキーは Mac（aqua）のときだけ付ける（標準 02「メニュー」）。"""
    import inspect
    source = inspect.getsource(app_module.VaFaxApp.__init__)
    assert "if theme.is_aqua(self):\n            self._bind_mac_keys()" in source


def test_mac_icon_uses_iconphoto(mac_app):
    """Mac（Windows以外）では.icoをiconbitmapに渡さず、Pillowで読んだ画像をiconphotoで与える。"""
    photo = getattr(mac_app.window, "_va_fax_icon_photo", None)
    assert photo is not None
    assert photo.width() > 0 and photo.height() > 0


def test_mac_command_a_selects_all_pages(mac_app):
    """ページの一覧（サムネイル）で、⌘AがCtrl+Aと同じく全ページを選ぶ。"""
    canvas = mac_app.preview_pane._canvas
    assert canvas.bind("<Command-a>") and canvas.bind("<Control-a>")


def test_mac_command_keys_and_quit_are_bound(mac_app):
    """⌘Z・⌘Y・⌘O・⌘S・⌘⇧Sは、Ctrlと同じ働きに、⌘Q（::tk::mac::Quit）は、閉じるボタンと
    同じ処理（未保存の確認）につながる。"""
    for sequence in ("<Command-z>", "<Command-Z>", "<Control-z>", "<Control-Z>", "<Command-y>",
                     "<Command-o>", "<Command-s>", "<Command-Shift-S>", "<Control-s>",
                     "<Control-Shift-S>"):
        assert mac_app.window.bind_all(sequence), sequence
    assert mac_app.window.tk.call("info", "commands", "::tk::mac::Quit")


def test_mac_preview_uses_mac_wheel_units(mac_app):
    assert mac_app.preview_pane._aqua is True


def test_remove_cursor_falls_back_to_default_when_x_cursor_is_missing(mac_app, monkeypatch):
    """同梱カーソルも代替のX_cursorも使えない環境（MacのTk等）では既定のカーソル（""）にする。"""
    assert theme._fallback_remove_cursor(mac_app.window) == theme.REMOVE_CURSOR_FALLBACK
    monkeypatch.setattr(theme, "REMOVE_CURSOR_FALLBACK", "va_fax_no_such_cursor")
    assert theme._fallback_remove_cursor(mac_app.window) == ""
