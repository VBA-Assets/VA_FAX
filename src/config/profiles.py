# -*- coding: utf-8 -*-
"""サービスプロファイルの読込・検証（F-27・F-29）。

送信制約値（容量上限・対応用紙サイズ等）はコードに直接埋め込まず、
本モジュールを通じて外部JSON定義から取得する（F-29）。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

_BUNDLED_PROFILES_DIR = Path(__file__).resolve().parent.parent / "resources" / "profiles"

_REQUIRED_FIELDS = (
    "id",
    "display_name",
    "verification_status",
    "source_note",
    "max_file_size_bytes",
    "page_count_limit",
    "output_formats",
    "supported_page_sizes",
    "normalize_target",
    "landscape_accepted",
    "color_capability_note",
    "resolution_dpi_steps",
    "jpeg_quality_steps",
)

_VALID_VERIFICATION_STATUSES = ("verified", "reference", "custom")

DEFAULT_TOLERANCE_MM = 2.0


class ProfileError(Exception):
    """プロファイル定義の読込・検証に失敗した場合の例外。"""


@dataclass(frozen=True)
class PageSize:
    """用紙サイズ（ミリメートル単位）。"""

    name: str
    width_mm: float
    height_mm: float

    def matches(self, width_mm: float, height_mm: float,
                tolerance_mm: float = DEFAULT_TOLERANCE_MM) -> bool:
        """向き（縦横）を区別せず、許容誤差内で寸法が一致するか判定する。"""
        a = sorted((self.width_mm, self.height_mm))
        b = sorted((width_mm, height_mm))
        return abs(a[0] - b[0]) <= tolerance_mm and abs(a[1] - b[1]) <= tolerance_mm


@dataclass(frozen=True)
class Profile:
    """サービスプロファイル（インターネットFAXサービスごとの送信制約一式）。"""

    id: str
    display_name: str
    verification_status: str
    source_note: str
    max_file_size_bytes: int
    page_count_limit: Optional[int]
    output_formats: tuple[str, ...]
    supported_page_sizes: tuple[PageSize, ...]
    normalize_target: PageSize
    landscape_accepted: bool
    color_capability_note: str
    resolution_dpi_steps: tuple[int, ...]
    jpeg_quality_steps: tuple[int, ...]
    reference_notice: Optional[str] = None

    @property
    def resolution_dpi_default(self) -> int:
        return self.resolution_dpi_steps[0]

    @property
    def resolution_dpi_floor(self) -> int:
        return self.resolution_dpi_steps[-1]

    @property
    def jpeg_quality_default(self) -> int:
        """JPEG品質の初期値（「中」＝中央の段階。F-10）。"""
        mid = (len(self.jpeg_quality_steps) - 1) // 2
        return self.jpeg_quality_steps[mid]

    def is_reference(self) -> bool:
        """参考値（未確認事項を含む）プロファイルか判定する（F-28）。"""
        return self.verification_status in ("reference", "custom")

    def find_supported_page_size(self, width_mm: float, height_mm: float,
                                  tolerance_mm: float = DEFAULT_TOLERANCE_MM) -> Optional[PageSize]:
        """指定寸法が対応用紙サイズに含まれる場合、そのPageSizeを返す（向き無視）。"""
        for size in self.supported_page_sizes:
            if size.matches(width_mm, height_mm, tolerance_mm=tolerance_mm):
                return size
        return None


def _require(data: dict[str, Any], source: str) -> None:
    missing = [f for f in _REQUIRED_FIELDS if f not in data]
    if missing:
        raise ProfileError(
            f"プロファイル定義（{source}）に必須項目が不足しています：{', '.join(missing)}"
        )


def _parse_page_size(data: Any, source: str, field_name: str) -> PageSize:
    try:
        return PageSize(
            name=str(data["name"]),
            width_mm=float(data["width_mm"]),
            height_mm=float(data["height_mm"]),
        )
    except (TypeError, KeyError, ValueError) as exc:
        raise ProfileError(
            f"プロファイル定義（{source}）の{field_name}の形式が不正です：{exc}"
        ) from exc


def _profile_from_dict(data: dict[str, Any], source: str) -> Profile:
    _require(data, source)

    verification_status = data["verification_status"]
    if verification_status not in _VALID_VERIFICATION_STATUSES:
        raise ProfileError(
            f"プロファイル定義（{source}）のverification_statusが不正です：{verification_status}"
        )

    output_formats = tuple(str(v) for v in data["output_formats"])
    if "pdf" not in output_formats:
        raise ProfileError(
            f"プロファイル定義（{source}）のoutput_formatsにpdfが含まれていません"
        )

    supported_sizes = tuple(
        _parse_page_size(s, source, "supported_page_sizes") for s in data["supported_page_sizes"]
    )
    if not supported_sizes:
        raise ProfileError(f"プロファイル定義（{source}）のsupported_page_sizesが空です")

    normalize_target = _parse_page_size(data["normalize_target"], source, "normalize_target")

    try:
        resolution_dpi_steps = tuple(int(v) for v in data["resolution_dpi_steps"])
        jpeg_quality_steps = tuple(int(v) for v in data["jpeg_quality_steps"])
        max_file_size_bytes = int(data["max_file_size_bytes"])
        page_count_limit = (
            int(data["page_count_limit"]) if data["page_count_limit"] is not None else None
        )
    except (TypeError, ValueError) as exc:
        raise ProfileError(f"プロファイル定義（{source}）の数値項目が不正です：{exc}") from exc

    if not resolution_dpi_steps or not jpeg_quality_steps:
        raise ProfileError(
            f"プロファイル定義（{source}）のresolution_dpi_stepsまたはjpeg_quality_stepsが空です"
        )
    if len(jpeg_quality_steps) != 3:
        # F-10はJPEG品質を「高・中・低」の3段階選択と定めており、GUIもこの3段階を前提に
        # 実装されている（各段階が具体的にどの品質値かはプロファイルが定める）。
        raise ProfileError(
            f"プロファイル定義（{source}）のjpeg_quality_stepsは3段階（高・中・低）で"
            f"指定してください：{len(jpeg_quality_steps)}段階"
        )
    if max_file_size_bytes <= 0:
        raise ProfileError(f"プロファイル定義（{source}）のmax_file_size_bytesが不正です")

    return Profile(
        id=str(data["id"]),
        display_name=str(data["display_name"]),
        verification_status=verification_status,
        source_note=str(data["source_note"]),
        max_file_size_bytes=max_file_size_bytes,
        page_count_limit=page_count_limit,
        output_formats=output_formats,
        supported_page_sizes=supported_sizes,
        normalize_target=normalize_target,
        landscape_accepted=bool(data["landscape_accepted"]),
        color_capability_note=str(data["color_capability_note"]),
        resolution_dpi_steps=resolution_dpi_steps,
        jpeg_quality_steps=jpeg_quality_steps,
        reference_notice=data.get("reference_notice"),
    )


def load_profile_file(path: Path) -> Profile:
    """プロファイル定義JSONファイルを1件読み込む。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProfileError(f"プロファイルファイルを読み込めません（{path.name}）：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise ProfileError(f"プロファイルファイルの形式が不正です（{path.name}）：{exc}") from exc

    if not isinstance(raw, dict):
        raise ProfileError(f"プロファイルファイルの形式が不正です（{path.name}）")

    return _profile_from_dict(raw, source=path.name)


def appdata_profiles_dir() -> Path:
    """追加プロファイルの配置先ディレクトリ（%APPDATA%\\VA_FAX\\profiles）を返す。"""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "VA_FAX" / "profiles"


def _profile_to_dict(profile: Profile) -> dict[str, Any]:
    return {
        "id": profile.id,
        "display_name": profile.display_name,
        "verification_status": profile.verification_status,
        "source_note": profile.source_note,
        "reference_notice": profile.reference_notice,
        "max_file_size_bytes": profile.max_file_size_bytes,
        "page_count_limit": profile.page_count_limit,
        "output_formats": list(profile.output_formats),
        "supported_page_sizes": [
            {"name": s.name, "width_mm": s.width_mm, "height_mm": s.height_mm}
            for s in profile.supported_page_sizes
        ],
        "normalize_target": {
            "name": profile.normalize_target.name,
            "width_mm": profile.normalize_target.width_mm,
            "height_mm": profile.normalize_target.height_mm,
        },
        "landscape_accepted": profile.landscape_accepted,
        "color_capability_note": profile.color_capability_note,
        "resolution_dpi_steps": list(profile.resolution_dpi_steps),
        "jpeg_quality_steps": list(profile.jpeg_quality_steps),
    }


def save_custom_profile(profile: Profile, extra_dir: Optional[Path] = None) -> None:
    """カスタムプロファイル（F-27）を追加プロファイル配置先へ保存する。

    次回起動時・次回のload_all_profiles呼び出し時に、同梱の雛形（custom_template.json）を
    上書きする形で読み込まれる。文書に関する情報は一切含めない（N-06）。
    """
    target_dir = extra_dir if extra_dir is not None else appdata_profiles_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    data = _profile_to_dict(profile)
    (target_dir / f"{profile.id}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_all_profiles(extra_dir: Optional[Path] = None) -> dict[str, Profile]:
    """同梱プロファイルと追加プロファイル（%APPDATA%）をあわせて読み込む。

    id が重複する場合は追加プロファイル側で上書きする
    （事務所の管理者が同梱プロファイルの値を上書き配布できるようにするため）。
    不正なプロファイルファイルが1件あっても、他の読込可能なプロファイルで
    アプリが起動できるよう、当該ファイルのみを読み飛ばす。
    """
    profiles: dict[str, Profile] = {}

    for path in sorted(_BUNDLED_PROFILES_DIR.glob("*.json")):
        try:
            profile = load_profile_file(path)
        except ProfileError:
            continue
        profiles[profile.id] = profile

    extra = extra_dir if extra_dir is not None else appdata_profiles_dir()
    if extra.is_dir():
        for path in sorted(extra.glob("*.json")):
            try:
                profile = load_profile_file(path)
            except ProfileError:
                continue
            profiles[profile.id] = profile

    return profiles


def get_profile(profile_id: str, extra_dir: Optional[Path] = None) -> Profile:
    """指定idのプロファイルを取得する。"""
    profiles = load_all_profiles(extra_dir=extra_dir)
    try:
        return profiles[profile_id]
    except KeyError as exc:
        raise ProfileError(f"プロファイル「{profile_id}」が見つかりません") from exc
