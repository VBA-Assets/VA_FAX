# -*- coding: utf-8 -*-
"""アプリケーション設定の永続化と変換の既定設定（F-23・F-26）。

保存内容はフォルダパス・選択中プロファイル・変換の既定設定・ウィンドウの大きさ等のUI状態のみとし、
文書に関する情報は一切含めない（N-06）。外部への送信は行わない（N-01）。
settings.json は手動編集にも耐える平易な構造とし、事務所内の複数PCへ
コピーすることでプロファイルと既定設定を一括配布できる（F-23・F-28）。
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

# 3：Ver200。出力先・出力ファイル名の方針と、変換レポートの既定値をなくし（F-02・旧F-25）、
#    ウィンドウの大きさ・位置を足した（F-23）。古い項目が残っていても、読み飛ばす。
SETTINGS_VERSION = 3

# "none"は「何もしない」（F-11・F-13。Ver200）
COMPRESSION_POLICIES = ("always_binarize", "always_grayscale", "none")
DITHER_METHODS = ("auto", "threshold", "diffusion")
JPEG_QUALITY_LEVELS = ("high", "medium", "low")
SIZE_REDUCTION_STRATEGIES = ("culprit_only", "uniform")
CAPACITY_EXCEEDED_METHODS = ("reduce_resolution", "split", "none")

# ［名前を付けて保存］・［コピーして開く］のファイル名に付ける文字の初期値と、使えない文字（F-35）
DEFAULT_NAME_SUFFIX = "_fax"
NAME_SUFFIX_MAX_LENGTH = 20
_NAME_SUFFIX_FORBIDDEN = set('\\/:*?"<>|')

# ウィンドウの大きさ・位置（Tkのgeometryの形：幅x高さ±左±上）
_GEOMETRY_PATTERN = re.compile(r"^\d{2,5}x\d{2,5}[+-]-?\d{1,5}[+-]-?\d{1,5}$")


@dataclass
class ConversionDefaults:
    """変換の既定設定（F-26）。"""

    compression_policy: str = "always_binarize"
    normalize_enabled: bool = True
    dither_method: str = "auto"
    jpeg_quality_level: str = "medium"
    capacity_exceeded_method: str = "reduce_resolution"
    size_reduction_strategy: str = "culprit_only"


@dataclass
class Settings:
    """settings.jsonの内容（UI状態のみ）。"""

    last_input_dir: Optional[str] = None
    profile_id: Optional[str] = None
    first_run_completed: bool = False
    # ウィンドウの大きさ・位置（最大化していないときのもの）と、最大化していたか（F-23。Ver200）
    window_geometry: Optional[str] = None
    window_maximized: bool = False
    # ［名前を付けて保存］・［コピーして開く］のファイル名の初めの値に、文字を付けるか・付ける文字
    # （［ファイル］→［オプション］。F-35）
    append_fax_to_save_name: bool = True
    save_name_suffix: str = DEFAULT_NAME_SUFFIX
    # 保存の後に、保存したフォルダを開くか（［ファイル］→［オプション］。F-35）
    open_folder_after_save: bool = False
    defaults: ConversionDefaults = field(default_factory=ConversionDefaults)


def app_data_dir() -> Path:
    """VA FAXの管理領域（%APPDATA%\\VA_FAX）を返す。

    Windows以外（Macでソースから動かす場合。N-03）では、ホームの .va_fax とする
    （Windows風のAppData\\Roamingフォルダを作らないため）。
    """
    if sys.platform != "win32":
        return Path.home() / ".va_fax"
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

    # Ver130 までの出力先・出力ファイル名の方針、変換レポートの既定値が残っていても、
    # 読み飛ばす（エラーにしない。F-26）。
    base = ConversionDefaults()
    return ConversionDefaults(
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
    )


def valid_name_suffix(value: Any) -> Optional[str]:
    """ファイル名に付ける文字として使えるなら、前後の空白を除いて返す（使えなければNone）。

    空・長すぎる・ファイル名に使えない文字（\\ / : * ? " < > |）や制御文字を含むものは使えない。
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > NAME_SUFFIX_MAX_LENGTH:
        return None
    if any(ch in _NAME_SUFFIX_FORBIDDEN or ord(ch) < 32 for ch in text):
        return None
    return text


def valid_geometry(value: Any) -> Optional[str]:
    """ウィンドウの大きさ・位置の文字列として使えるものなら、そのまま返す（使えなければNone）。"""
    return value if isinstance(value, str) and _GEOMETRY_PATTERN.match(value) else None


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
        window_geometry=valid_geometry(raw.get("window_geometry")),
        window_maximized=bool(raw.get("window_maximized", False)),
        append_fax_to_save_name=bool(raw.get("append_fax_to_save_name", True)),
        save_name_suffix=valid_name_suffix(raw.get("save_name_suffix")) or DEFAULT_NAME_SUFFIX,
        open_folder_after_save=bool(raw.get("open_folder_after_save", False)),
        defaults=_defaults_from_dict(raw.get("defaults")),
    )


def save_settings(settings: Settings) -> None:
    """settings.jsonへ保存する（UI状態のみ。文書情報は含めない）。"""
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": SETTINGS_VERSION, **asdict(settings)}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
