# -*- coding: utf-8 -*-
"""config/profiles.py のテスト（F-27・F-29）。"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from config.profiles import (
    PageSize,
    ProfileError,
    get_profile,
    load_all_profiles,
    load_profile_file,
    save_custom_profile,
)

BUNDLED_IDS = {"motfax", "movfax", "messageplus", "efax", "jfax", "sokusoku_fax", "custom"}

_EMPTY_DIR = Path(__file__).resolve().parent / "_no_such_dir"


def test_all_bundled_profiles_load():
    profiles = load_all_profiles(extra_dir=_EMPTY_DIR)
    assert set(profiles) == BUNDLED_IDS


@pytest.mark.parametrize("profile_id", sorted(BUNDLED_IDS))
def test_profile_schema_fields(profile_id):
    profile = get_profile(profile_id, extra_dir=_EMPTY_DIR)
    assert profile.max_file_size_bytes > 0
    assert profile.supported_page_sizes
    assert profile.resolution_dpi_steps
    assert profile.jpeg_quality_steps
    assert "pdf" in profile.output_formats


def test_reference_profiles_have_notice():
    for profile_id in ("efax", "jfax"):
        profile = get_profile(profile_id, extra_dir=_EMPTY_DIR)
        assert profile.verification_status == "reference"
        assert profile.reference_notice


def test_verified_profiles_are_marked_verified():
    for profile_id in ("motfax", "movfax", "messageplus", "sokusoku_fax"):
        profile = get_profile(profile_id, extra_dir=_EMPTY_DIR)
        assert profile.verification_status == "verified"


def test_movfax_landscape_accepted_motfax_not():
    movfax = get_profile("movfax", extra_dir=_EMPTY_DIR)
    motfax = get_profile("motfax", extra_dir=_EMPTY_DIR)
    assert movfax.landscape_accepted is True
    assert motfax.landscape_accepted is False


def test_appdata_profile_overrides_bundled(tmp_path):
    override = tmp_path / "motfax.json"
    override.write_text(
        """
        {
          "id": "motfax", "display_name": "MOT/FAX（上書き）",
          "verification_status": "custom", "source_note": "test",
          "max_file_size_bytes": 999, "page_count_limit": null,
          "output_formats": ["pdf"],
          "supported_page_sizes": [{"name": "A4", "width_mm": 210, "height_mm": 297}],
          "normalize_target": {"name": "A4", "width_mm": 210, "height_mm": 297},
          "landscape_accepted": false, "color_capability_note": "",
          "resolution_dpi_steps": [200, 150, 120], "jpeg_quality_steps": [85, 65, 45]
        }
        """,
        encoding="utf-8",
    )
    profiles = load_all_profiles(extra_dir=tmp_path)
    assert profiles["motfax"].max_file_size_bytes == 999
    assert profiles["motfax"].display_name == "MOT/FAX（上書き）"
    # 他の同梱プロファイルは影響を受けない
    assert profiles["movfax"].max_file_size_bytes == get_profile("movfax", extra_dir=_EMPTY_DIR).max_file_size_bytes


def test_jpeg_quality_steps_must_have_exactly_three_levels(tmp_path):
    """F-10は「高・中・低」の3段階を定めており、GUIもこれを前提に実装されているため、
    3段階以外のプロファイル定義はエラーとする（IndexError等の実行時エラーを未然に防ぐ）。
    """
    bad = tmp_path / "bad.json"
    bad.write_text(
        """
        {
          "id": "bad", "display_name": "不正", "verification_status": "custom", "source_note": "test",
          "max_file_size_bytes": 1000, "page_count_limit": null,
          "output_formats": ["pdf"],
          "supported_page_sizes": [{"name": "A4", "width_mm": 210, "height_mm": 297}],
          "normalize_target": {"name": "A4", "width_mm": 210, "height_mm": 297},
          "landscape_accepted": false, "color_capability_note": "",
          "resolution_dpi_steps": [200, 150, 120], "jpeg_quality_steps": [80, 60]
        }
        """,
        encoding="utf-8",
    )
    with pytest.raises(ProfileError):
        load_profile_file(bad)


def test_load_all_profiles_skips_broken_extra_profile(tmp_path):
    """追加プロファイルに不正なJSONが1件あっても、他のプロファイルで起動できること（B-2）。"""
    (tmp_path / "broken.json").write_text("{not valid json", encoding="utf-8")
    (tmp_path / "also_missing_fields.json").write_text('{"id": "incomplete"}', encoding="utf-8")

    profiles = load_all_profiles(extra_dir=tmp_path)

    assert "broken" not in profiles
    assert "incomplete" not in profiles
    assert set(profiles) == BUNDLED_IDS


def test_missing_required_field_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"id": "bad"}', encoding="utf-8")
    with pytest.raises(ProfileError):
        load_profile_file(bad)


def test_malformed_json_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ProfileError):
        load_profile_file(bad)


def test_missing_pdf_in_output_formats_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(
        """
        {
          "id": "bad", "display_name": "不正", "verification_status": "custom", "source_note": "test",
          "max_file_size_bytes": 1000, "page_count_limit": null,
          "output_formats": ["tiff"],
          "supported_page_sizes": [{"name": "A4", "width_mm": 210, "height_mm": 297}],
          "normalize_target": {"name": "A4", "width_mm": 210, "height_mm": 297},
          "landscape_accepted": false, "color_capability_note": "",
          "resolution_dpi_steps": [200], "jpeg_quality_steps": [65]
        }
        """,
        encoding="utf-8",
    )
    with pytest.raises(ProfileError):
        load_profile_file(bad)


def test_unknown_profile_id_raises():
    with pytest.raises(ProfileError):
        get_profile("no_such_profile", extra_dir=_EMPTY_DIR)


def test_save_custom_profile_round_trip(tmp_path):
    """カスタムプロファイル（F-27）を保存すると、load_all_profilesで正しく読み込めること。"""
    original = get_profile("custom", extra_dir=_EMPTY_DIR)
    edited = replace(
        original,
        display_name="事務所専用FAX",
        max_file_size_bytes=2_000_000,
        page_count_limit=5,
        supported_page_sizes=(
            PageSize(name="A4", width_mm=210.0, height_mm=297.0),
            PageSize(name="B4", width_mm=257.0, height_mm=364.0),
        ),
        landscape_accepted=True,
        color_capability_note="モノクロのみ対応",
    )

    save_custom_profile(edited, extra_dir=tmp_path)
    loaded = load_all_profiles(extra_dir=tmp_path)["custom"]

    assert loaded.display_name == "事務所専用FAX"
    assert loaded.max_file_size_bytes == 2_000_000
    assert loaded.page_count_limit == 5
    assert [s.name for s in loaded.supported_page_sizes] == ["A4", "B4"]
    assert loaded.landscape_accepted is True
    assert loaded.color_capability_note == "モノクロのみ対応"
    # 保存されたファイルには文書に関する情報を一切含めない（N-06）
    raw = (tmp_path / "custom.json").read_text(encoding="utf-8")
    assert "文書" not in raw and ".pdf" not in raw


def test_page_size_matches_ignores_orientation():
    a4 = PageSize(name="A4", width_mm=210.0, height_mm=297.0)
    assert a4.matches(210.0, 297.0)
    assert a4.matches(297.0, 210.0)


def test_page_size_tolerance_boundary():
    a4 = PageSize(name="A4", width_mm=210.0, height_mm=297.0)
    assert a4.matches(212.0, 297.0)
    assert not a4.matches(212.1, 297.0)
    assert not a4.matches(215.9, 279.4)  # レターサイズは対応外
