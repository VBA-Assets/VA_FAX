# -*- coding: utf-8 -*-
"""Mac（ソースから動かす場合。N-03）向けの分岐のテスト。

Macの実機は無いため、sys.platform・Tkのウィンドウシステム名（theme.windowing_system）を
Mac（darwin・aqua）に差し替えて、Mac側の分岐を通す。あわせて、Windows側の結果が
従来と変わらないことも確かめる。

画面の試験は、Tkを1つのプロセスで何度も作り直さないよう（Windowsのpythonでは、
まれに初期化に失敗する）、メインウィンドウを1つだけ作り、このモジュールの試験で共用する。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from config import profiles as profiles_module
from config import settings as settings_module
from gui import app as app_module
from gui import theme
from gui.preview import wheel_scroll_units
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

@pytest.fixture(scope="module")
def mac_app(tmp_path_factory):
    """Tkのウィンドウシステムをaquaに見せかけてメインウィンドウを作る。

    ログ（setup_logger）が班長の%APPDATA%に書かれないよう、APPDATAを一時フォルダに替える。
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("APPDATA", str(tmp_path_factory.mktemp("appdata")))
        mp.setattr(theme, "windowing_system", lambda _widget: "aqua")
        profile = profiles_module.get_profile("motfax")
        window = app_module.VaFaxApp(settings_module.Settings(), profile)
        window.withdraw()
        window.update()
        try:
            yield window
        finally:
            window.destroy()


def test_mac_icon_uses_iconphoto(mac_app):
    """Mac（Windows以外）では.icoをiconbitmapに渡さず、Pillowで読んだ画像をiconphotoで与える。"""
    photo = getattr(mac_app, "_va_fax_icon_photo", None)
    assert photo is not None
    assert photo.width() > 0 and photo.height() > 0


def test_mac_command_key_selects_all_input_lines(mac_app):
    """入力ファイル欄で⌘A（Command＝Mod1＝0x8）がCtrl+Aと同じく全行を選ぶ。"""
    assert mac_app._shortcut_state_mask & 0x4
    assert mac_app._shortcut_state_mask & 0x8
    mac_app.input_paths = [Path("a.pdf"), Path("b.pdf"), Path("c.pdf")]
    mac_app._refresh_input_text()
    mac_app._set_input_selection(None)
    event = SimpleNamespace(keysym="a", state=0x8, widget=mac_app.input_text)
    assert mac_app._on_input_text_key(event) == "break"
    assert mac_app._selected_input_lines() == (1, 3)
    mac_app.input_paths = []
    mac_app._refresh_input_text()


def test_mac_command_z_and_quit_are_bound(mac_app):
    """⌘Zは全体の取り消しに、⌘Q（::tk::mac::Quit）はウィンドウの破棄につながる。"""
    for sequence in ("<Command-z>", "<Command-Z>", "<Control-z>", "<Control-Z>"):
        assert mac_app.bind_all(sequence)
    assert mac_app.tk.call("info", "commands", "::tk::mac::Quit")


def test_mac_preview_uses_mac_wheel_units(mac_app):
    assert mac_app.preview_pane._aqua is True


def test_remove_cursor_falls_back_to_default_when_x_cursor_is_missing(mac_app, monkeypatch):
    """同梱カーソルも代替のX_cursorも使えない環境（MacのTk等）では既定のカーソル（""）にする。"""
    assert theme._fallback_remove_cursor(mac_app) == theme.REMOVE_CURSOR_FALLBACK
    monkeypatch.setattr(theme, "REMOVE_CURSOR_FALLBACK", "va_fax_no_such_cursor")
    assert theme._fallback_remove_cursor(mac_app) == ""
