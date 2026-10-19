# -*- coding: utf-8 -*-
"""アプリケーション設定の永続化と変換の既定設定（F-23・F-26）。

保存内容はフォルダパス・選択中プロファイル・変換の既定設定等のUI状態のみとし、
文書に関する情報は一切含めない（N-06）。外部への送信は行わない（N-01）。
settings.json は手動編集にも耐える平易な構造とし、事務所内の複数PCへ
コピーすることでプロファイルと既定設定を一括配布できる（F-23・F-28）。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

SETTINGS_VERSION = 2

OUTPUT_FOLDER_POLICIES = ("same_as_input", "fixed")
OUTPUT_NAME_POLICIES = ("append_fax", "same_as_input")
COMPRESSION_POLICIES = ("always_binarize", "always_grayscale")
DITHER_METHODS = ("auto", "threshold", "diffusion")
JPEG_QUALITY_LEVELS = ("high", "medium", "low")
SIZE_REDUCTION_STRATEGIES = ("culprit_only", "uniform")
CAPACITY_EXCEEDED_METHODS = ("reduce_resolution", "split")


class OutputPathError(Exception):
    """出力パスの検証エラー（F-02。利用者向けの平易な日本語メッセージを保持する）。"""


@dataclass
class ConversionDefaults:
    """変換の既定設定（F-26）。"""

    output_folder_policy: str = "same_as_input"
    fixed_output_folder: Optional[str] = None
    output_name_policy: str = "append_fax"
    compression_policy: str = "always_binarize"
    normalize_enabled: bool = True
    dither_method: str = "auto"
    jpeg_quality_level: str = "medium"
    capacity_exceeded_method: str = "reduce_resolution"
    size_reduction_strategy: str = "culprit_only"
    report_enabled: bool = False


@dataclass
class Settings:
    """settings.jsonの内容（UI状態のみ）。"""

    last_input_dir: Optional[str] = None
    profile_id: Optional[str] = None
    first_run_completed: bool = False
    defaults: ConversionDefaults = field(default_factory=ConversionDefaults)


def app_data_dir() -> Path:
    """VA FAXの管理領域（%APPDATA%\\VA_FAX）を返す。"""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "VA_FAX"


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


def default_documents_dir() -> Path:
    """設定ファイルが存在しない・破損している場合のフォールバック先（F-23）。"""
    return Path.home() / "Documents"


def _choice(raw: dict[str, Any], key: str, valid: tuple[str, ...], fallback: str) -> str:
    value = raw.get(key)
    return value if value in valid else fallback


def _defaults_from_dict(raw: Any) -> ConversionDefaults:
    """defaults項目を読み込む。不正値は項目ごとに既定値へフォールバックする（手動編集耐性）。"""
    if not isinstance(raw, dict):
        return ConversionDefaults()

    base = ConversionDefaults()
    folder_policy = _choice(raw, "output_folder_policy", OUTPUT_FOLDER_POLICIES,
                             base.output_folder_policy)
    name_policy = _choice(raw, "output_name_policy", OUTPUT_NAME_POLICIES, base.output_name_policy)
    # 「入力と同じフォルダ×入力ファイル名と同一」は入力上書きとなるため許容しない（F-02・F-03）
    if folder_policy == "same_as_input" and name_policy == "same_as_input":
        name_policy = "append_fax"

    fixed = raw.get("fixed_output_folder")
    return ConversionDefaults(
        output_folder_policy=folder_policy,
        fixed_output_folder=str(fixed) if fixed else None,
        output_name_policy=name_policy,
        compression_policy=_choice(raw, "compression_policy", COMPRESSION_POLICIES,
                                    base.compression_policy),
        normalize_enabled=bool(raw.get("normalize_enabled", base.normalize_enabled)),
        dither_method=_choice(raw, "dither_method", DITHER_METHODS, base.dither_method),
        jpeg_quality_level=_choice(raw, "jpeg_quality_level", JPEG_QUALITY_LEVELS,
                                    base.jpeg_quality_level),
        capacity_exceeded_method=_choice(raw, "capacity_exceeded_method", CAPACITY_EXCEEDED_METHODS,
                                          base.capacity_exceeded_method),
        size_reduction_strategy=_choice(raw, "size_reduction_strategy", SIZE_REDUCTION_STRATEGIES,
                                         base.size_reduction_strategy),
        report_enabled=bool(raw.get("report_enabled", base.report_enabled)),
    )


def load_settings() -> Settings:
    """settings.jsonを読み込む。存在しない・破損している場合は既定値を返す。"""
    fallback = Settings(last_input_dir=str(default_documents_dir()))

    path = settings_path()
    if not path.is_file():
        return fallback

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback

    if not isinstance(raw, dict):
        return fallback

    return Settings(
        last_input_dir=raw.get("last_input_dir") or str(default_documents_dir()),
        profile_id=raw.get("profile_id"),
        first_run_completed=bool(raw.get("first_run_completed", False)),
        defaults=_defaults_from_dict(raw.get("defaults")),
    )


def save_settings(settings: Settings) -> None:
    """settings.jsonへ保存する（UI状態のみ。文書情報は含めない）。"""
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": SETTINGS_VERSION, **asdict(settings)}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def compute_default_output_path(input_path: Path, defaults: ConversionDefaults) -> Path:
    """既定設定の出力先方針・ファイル名方針から出力ファイルパスの初期値を求める（F-02）。"""
    if defaults.output_folder_policy == "fixed" and defaults.fixed_output_folder:
        folder = Path(defaults.fixed_output_folder)
    else:
        folder = input_path.parent

    if defaults.output_name_policy == "same_as_input":
        name = input_path.name
    else:
        name = f"{input_path.stem}_fax.pdf"

    return folder / name


def validate_output_path(input_path: Path, output_path: Path) -> None:
    """出力パスを検証する（F-02）。問題がある場合はOutputPathErrorを送出する。

    - 出力パスが入力パスと一致する場合（入力上書き＝F-03違反）はエラー。
    - 出力先フォルダが存在しない場合（固定フォルダの共有未接続等）はエラー。
      フォールバック出力は行わない（機密文書が意図しない場所に出力されることを防ぐ）。
    """
    same = os.path.normcase(os.path.abspath(str(input_path))) == \
        os.path.normcase(os.path.abspath(str(output_path)))
    if same:
        raise OutputPathError(
            "出力先が入力ファイルと同じです。入力ファイルは上書きできないため、"
            "出力ファイル名または出力先フォルダを変更してください。"
        )
    if not output_path.parent.is_dir():
        raise OutputPathError(
            "出力先のフォルダが見つかりません。出力先の指定、または設定メニューの"
            "「変換の既定設定」をご確認ください。"
        )
