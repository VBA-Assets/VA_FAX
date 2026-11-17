# -*- coding: utf-8 -*-
"""config/settings.py のテスト（F-23・F-26）。"""
from __future__ import annotations

import json

import pytest

from config.settings import (
    ConversionDefaults,
    Settings,
    load_settings,
    save_settings,
    settings_path,
)


@pytest.fixture
def appdata(tmp_path, monkeypatch):
    """APPDATAを一時ディレクトリに差し替える（実環境の設定を汚さない）。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------- 永続化（F-23）

def test_round_trip_with_defaults(appdata):
    settings = Settings(
        profile_id="motfax",
        first_run_completed=True,
        defaults=ConversionDefaults(
            compression_policy="none",
            normalize_enabled=False,
            dither_method="diffusion",
            jpeg_quality_level="low",
            capacity_exceeded_method="none",
            size_reduction_strategy="uniform",
        ),
        window_geometry="1200x800+40+30",
        window_maximized=True,
    )
    save_settings(settings)
    loaded = load_settings()
    assert loaded.profile_id == "motfax"
    assert loaded.defaults == settings.defaults
    assert loaded.window_geometry == "1200x800+40+30" and loaded.window_maximized is True


def test_missing_file_falls_back(appdata):
    loaded = load_settings()
    assert loaded.profile_id is None
    assert loaded.defaults == ConversionDefaults()
    assert loaded.last_input_dir  # ドキュメントフォルダ


def test_legacy_v1_settings_json_loads_without_error(appdata):
    """旧バージョン（プリセット・デバッグモード・last_output_dirがあった時代）のsettings.jsonを
    読み込んでも例外を起こさず、次回保存時に新形式へ自己修復されること（廃止機能の残骸対応）。
    """
    path = settings_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "version": 1,
        "last_input_dir": r"C:\旧フォルダ",
        "last_output_dir": r"C:\旧出力フォルダ",
        "profile_id": "motfax",
        "debug_mode": True,
        "report_enabled": False,
        "first_run_completed": True,
        "presets": {"事務所標準": {"mode": "binarize"}},
    }), encoding="utf-8")

    loaded = load_settings()

    assert loaded.profile_id == "motfax"
    assert loaded.last_input_dir == r"C:\旧フォルダ"
    assert loaded.first_run_completed is True
    assert not hasattr(loaded, "debug_mode")
    assert not hasattr(loaded, "last_output_dir")
    assert not hasattr(loaded, "presets")
    # 保存し直すと新形式で書き直され、旧キーは残らない
    save_settings(loaded)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 3
    assert "debug_mode" not in raw
    assert "presets" not in raw


def test_broken_json_falls_back(appdata):
    path = settings_path()
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    loaded = load_settings()
    assert loaded.defaults == ConversionDefaults()


def test_invalid_default_values_fall_back_per_field(appdata):
    path = settings_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "profile_id": "motfax",
        "defaults": {
            "capacity_exceeded_method": "nonsense",
            "compression_policy": "always_grayscale",
            "jpeg_quality_level": "ultra",
        },
    }), encoding="utf-8")
    loaded = load_settings()
    # 不正値は項目単位で既定値へ、正しい値は維持される
    assert loaded.defaults.capacity_exceeded_method == "reduce_resolution"
    assert loaded.defaults.jpeg_quality_level == "medium"
    assert loaded.defaults.compression_policy == "always_grayscale"


def test_invalid_size_reduction_strategy_falls_back_to_culprit_only(appdata):
    path = settings_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "defaults": {"size_reduction_strategy": "nonsense"},
    }), encoding="utf-8")
    loaded = load_settings()
    assert loaded.defaults.size_reduction_strategy == "culprit_only"


def test_capacity_exceeded_method_round_trips(appdata):
    settings = Settings(defaults=ConversionDefaults(capacity_exceeded_method="split"))
    save_settings(settings)
    loaded = load_settings()
    assert loaded.defaults.capacity_exceeded_method == "split"


def test_invalid_capacity_exceeded_method_falls_back_to_reduce_resolution(appdata):
    path = settings_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "defaults": {"capacity_exceeded_method": "nonsense"},
    }), encoding="utf-8")
    loaded = load_settings()
    assert loaded.defaults.capacity_exceeded_method == "reduce_resolution"


def test_legacy_auto_retry_enabled_key_is_ignored_and_dropped_on_save(appdata):
    """廃止されたauto_retry_enabledキー（容量超過時の自動対処ON/OFF。常時ONに変更）が
    旧settings.jsonに残っていても、読込時に無視され、保存時に消えること。"""
    path = settings_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "defaults": {"auto_retry_enabled": False, "capacity_exceeded_method": "split"},
    }), encoding="utf-8")

    loaded = load_settings()
    assert not hasattr(loaded.defaults, "auto_retry_enabled")
    assert loaded.defaults.capacity_exceeded_method == "split"

    save_settings(loaded)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "auto_retry_enabled" not in raw["defaults"]
    assert raw["defaults"]["capacity_exceeded_method"] == "split"


def test_legacy_follow_diagnosis_compression_policy_falls_back_to_always_binarize(appdata):
    """F-06改訂により廃止された「事前診断のおすすめに従う」（compression_policy）は、
    読込時にalways_binarize（新しい既定値）へフォールバックすること。
    """
    path = settings_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "defaults": {"compression_policy": "follow_diagnosis"},
    }), encoding="utf-8")
    loaded = load_settings()
    assert loaded.defaults.compression_policy == "always_binarize"


def test_legacy_normalize_policy_field_is_ignored_falls_back_to_default_enabled(appdata):
    """用紙正規化の診断ベース提案（normalize_policy）は廃止され、常時ONを既定値とする
    単純なnormalize_enabledに置き換えられた。旧設定ファイルのnormalize_policyキーは
    無視され、新しい既定値（True）にフォールバックすること。
    """
    path = settings_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "defaults": {"normalize_policy": "follow_diagnosis"},
    }), encoding="utf-8")
    loaded = load_settings()
    assert loaded.defaults.normalize_enabled is True


def test_normalize_enabled_false_is_respected(appdata):
    path = settings_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "defaults": {"normalize_enabled": False},
    }), encoding="utf-8")
    loaded = load_settings()
    assert loaded.defaults.normalize_enabled is False

def test_ver130_output_and_report_settings_are_ignored_and_dropped(appdata):
    """Ver130 までの出力先・出力ファイル名の方針と、変換レポートの既定値が残っていても、
    エラーにせず読み飛ばす。保存し直すと、古い項目は残らない（F-26）。"""
    path = settings_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "version": 2,
        "profile_id": "motfax",
        "defaults": {
            "output_folder_policy": "fixed",
            "fixed_output_folder": r"C:\FAX送信箱",
            "output_name_policy": "same_as_input",
            "compression_policy": "always_grayscale",
            "report_enabled": True,
        },
    }), encoding="utf-8")
    loaded = load_settings()
    assert loaded.defaults.compression_policy == "always_grayscale"
    assert not hasattr(loaded.defaults, "fixed_output_folder")
    save_settings(loaded)
    text = path.read_text(encoding="utf-8")
    assert "FAX送信箱" not in text and "report_enabled" not in text and "output_" not in text


@pytest.mark.parametrize("value", ["none", "always_binarize", "always_grayscale"])
def test_compression_policy_round_trips_including_nothing(appdata, value):
    save_settings(Settings(defaults=ConversionDefaults(compression_policy=value)))
    assert load_settings().defaults.compression_policy == value


@pytest.mark.parametrize("value", ["abc", "1200x800", "1200x800+10", 123, None, "99999999x1+0+0"])
def test_invalid_window_geometry_is_dropped(appdata, value):
    """手で書き換えられた・壊れた大きさの指定は、使わない。"""
    path = settings_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"window_geometry": value}), encoding="utf-8")
    assert load_settings().window_geometry is None


def test_settings_file_holds_no_file_names(appdata):
    """設定ファイルに書くのは、フォルダの場所・プロファイル・既定設定・ウィンドウの大きさだけ（N-06）。"""
    save_settings(Settings(last_input_dir=r"C:\案件", profile_id="motfax"))
    raw = json.loads(settings_path().read_text(encoding="utf-8"))
    assert set(raw) == {"version", "last_input_dir", "profile_id", "first_run_completed",
                        "window_geometry", "window_maximized", "append_fax_to_save_name",
                        "save_name_suffix", "open_folder_after_save", "defaults"}


def test_append_fax_option_defaults_to_on_and_round_trips(appdata):
    assert load_settings().append_fax_to_save_name is True
    save_settings(Settings(append_fax_to_save_name=False))
    assert load_settings().append_fax_to_save_name is False


def test_suffix_and_open_folder_round_trip(appdata):
    save_settings(Settings(save_name_suffix="-送信用", open_folder_after_save=True))
    loaded = load_settings()
    assert loaded.save_name_suffix == "-送信用" and loaded.open_folder_after_save is True


@pytest.mark.parametrize("value", ["", "  ", "a/b", 'x"y', "a\\b", "長" * 21, 5, None])
def test_invalid_suffix_falls_back_to_fax(appdata, value):
    """手で書き換えられた・使えない文字は、初期値の「_fax」にする。"""
    path = settings_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"save_name_suffix": value}), encoding="utf-8")
    assert load_settings().save_name_suffix == "_fax"
