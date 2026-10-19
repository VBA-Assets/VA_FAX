# -*- coding: utf-8 -*-
"""config/settings.py のテスト（F-23・F-26・F-02の出力パス計算）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.settings import (
    ConversionDefaults,
    OutputPathError,
    Settings,
    compute_default_output_path,
    load_settings,
    save_settings,
    settings_path,
    validate_output_path,
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
            output_folder_policy="fixed",
            fixed_output_folder=r"C:\FAX送信箱",
            output_name_policy="same_as_input",
            compression_policy="always_binarize",
            normalize_enabled=False,
            dither_method="diffusion",
            jpeg_quality_level="low",
            capacity_exceeded_method="split",
            size_reduction_strategy="uniform",
            report_enabled=False,
        ),
    )
    save_settings(settings)
    loaded = load_settings()
    assert loaded.profile_id == "motfax"
    assert loaded.defaults == settings.defaults


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
    # 保存し直すと新形式（version 2）で書き直され、旧キーは残らない
    save_settings(loaded)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 2
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
            "output_folder_policy": "nonsense",
            "compression_policy": "always_grayscale",
            "jpeg_quality_level": "ultra",
        },
    }), encoding="utf-8")
    loaded = load_settings()
    # 不正値は項目単位で既定値へ、正しい値は維持される
    assert loaded.defaults.output_folder_policy == "same_as_input"
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


def test_same_folder_same_name_combination_is_corrected_on_load(appdata):
    """手動編集で禁止組合せが書かれていても、読込時に_fax付与へ矯正される（F-02・F-03）。"""
    path = settings_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "defaults": {
            "output_folder_policy": "same_as_input",
            "output_name_policy": "same_as_input",
        },
    }), encoding="utf-8")
    loaded = load_settings()
    assert loaded.defaults.output_name_policy == "append_fax"


# ---------------------------------------------------- 出力パスの初期値計算（F-02）

def test_output_path_same_folder_append_fax():
    input_path = Path(r"C:\案件\書面.pdf")
    result = compute_default_output_path(input_path, ConversionDefaults())
    assert result == Path(r"C:\案件\書面_fax.pdf")


def test_output_path_fixed_folder():
    defaults = ConversionDefaults(output_folder_policy="fixed",
                                   fixed_output_folder=r"C:\FAX送信箱")
    result = compute_default_output_path(Path(r"C:\案件\書面.pdf"), defaults)
    assert result == Path(r"C:\FAX送信箱\書面_fax.pdf")


def test_output_path_fixed_folder_same_name():
    defaults = ConversionDefaults(output_folder_policy="fixed",
                                   fixed_output_folder=r"C:\FAX送信箱",
                                   output_name_policy="same_as_input")
    result = compute_default_output_path(Path(r"C:\案件\書面.pdf"), defaults)
    assert result == Path(r"C:\FAX送信箱\書面.pdf")


def test_output_path_fixed_policy_without_folder_falls_back_to_input_folder():
    defaults = ConversionDefaults(output_folder_policy="fixed", fixed_output_folder=None)
    result = compute_default_output_path(Path(r"C:\案件\書面.pdf"), defaults)
    assert result == Path(r"C:\案件\書面_fax.pdf")


# ------------------------------------------------------ 出力パスの検証（F-02・F-03）

def test_validate_rejects_output_equal_to_input(tmp_path):
    input_path = tmp_path / "書面.pdf"
    input_path.write_bytes(b"dummy")
    with pytest.raises(OutputPathError):
        validate_output_path(input_path, input_path)


def test_validate_rejects_case_insensitive_same_path(tmp_path):
    input_path = tmp_path / "Shomen.PDF"
    input_path.write_bytes(b"dummy")
    with pytest.raises(OutputPathError):
        validate_output_path(input_path, tmp_path / "shomen.pdf")


def test_validate_rejects_missing_output_folder(tmp_path):
    input_path = tmp_path / "書面.pdf"
    input_path.write_bytes(b"dummy")
    with pytest.raises(OutputPathError):
        validate_output_path(input_path, tmp_path / "存在しないフォルダ" / "書面_fax.pdf")


def test_validate_accepts_normal_output(tmp_path):
    input_path = tmp_path / "書面.pdf"
    input_path.write_bytes(b"dummy")
    validate_output_path(input_path, tmp_path / "書面_fax.pdf")  # 例外が出ないこと
