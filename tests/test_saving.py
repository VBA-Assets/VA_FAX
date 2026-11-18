# -*- coding: utf-8 -*-
"""文書の保存（core/saving.py。F-02・F-03・F-13）のテスト。容量の上限等は、プロファイル経由で与える。"""
from __future__ import annotations

import io
import os
from pathlib import Path

import pypdf
import pytest

from core import saving
from core.convert import ConvertOptions


def _save(sample_bytes, profile, target: Path, name="sample01_a4_text.pdf", method="reduce_resolution",
          materials=(), mode="binarize"):
    return saving.save_document(
        sample_bytes(name), target, profile, ConvertOptions(mode=mode), method, "culprit_only",
        list(materials))


def test_split_output_paths():
    base = Path("C:/x/送信票.pdf")
    assert saving.split_output_paths(base, 1) == [base]
    assert [p.name for p in saving.split_output_paths(base, 3)] == [
        "送信票_1of3.pdf", "送信票_2of3.pdf", "送信票_3of3.pdf"]


def test_same_path_ignores_case_and_relative_form(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert saving.same_path(tmp_path / "a.pdf", Path("a.pdf"))
    assert not saving.same_path(tmp_path / "a.pdf", tmp_path / "b.pdf")


def test_write_atomically_replaces_file_and_leaves_no_temp(tmp_path):
    target = tmp_path / "out.pdf"
    target.write_bytes(b"old")
    saving.write_atomically(target, b"new")
    assert target.read_bytes() == b"new"
    assert [p.name for p in tmp_path.iterdir()] == ["out.pdf"]


def test_write_failure_keeps_original_and_removes_temp(tmp_path, monkeypatch):
    """置き換えに失敗しても、元のファイルは元のまま。一時的なファイルも残さない。"""
    target = tmp_path / "out.pdf"
    target.write_bytes(b"old")

    def fail(*_args, **_kwargs):
        raise PermissionError("locked")

    monkeypatch.setattr(saving.os, "replace", fail)
    with pytest.raises(OSError):
        saving.write_atomically(target, b"new")
    assert target.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["out.pdf"]


def test_save_overwrites_target_with_image_pdf(tmp_path, sample_bytes, motfax):
    target = tmp_path / "doc.pdf"
    target.write_bytes(sample_bytes("sample01_a4_text.pdf"))
    outcome = _save(sample_bytes, motfax, target)
    assert not outcome.split and outcome.paths == (target,)
    assert outcome.page_counts == (3,)
    data = target.read_bytes()
    assert data == outcome.first_pdf and len(data) == outcome.sizes[0] <= motfax.max_file_size_bytes
    reader = pypdf.PdfReader(io.BytesIO(data))
    assert not any((page.extract_text() or "").strip() for page in reader.pages)


def test_split_writes_numbered_files_and_leaves_target_untouched(tmp_path, sample_bytes):
    """分割が起きたときは、連番の付いたファイルだけを書き、目的の名前のファイルは変えない。"""
    from config.profiles import load_all_profiles
    efax = load_all_profiles()["efax"]
    original = sample_bytes("sample10_photo_heavy.pdf")
    target = tmp_path / "photo.pdf"
    target.write_bytes(original)
    outcome = _save(sample_bytes, efax, target, name="sample10_photo_heavy.pdf", method="split",
                    mode="grayscale")
    assert outcome.split and len(outcome.paths) > 1
    assert target.read_bytes() == original
    assert all(p.exists() and p.stat().st_size <= efax.max_file_size_bytes for p in outcome.paths)
    assert outcome.paths[0].name == f"photo_1of{len(outcome.paths)}.pdf"
    assert sum(outcome.page_counts) == 11

    # もう一度保存すると、分割してできるファイルが既にあるので、止める（上書きしない）
    with pytest.raises(saving.SaveError) as info:
        _save(sample_bytes, efax, target, name="sample10_photo_heavy.pdf", method="split", mode="grayscale")
    assert info.value.kind == "split_file_exists"


def test_split_name_equal_to_material_is_rejected(tmp_path, sample_bytes):
    from config.profiles import load_all_profiles
    efax = load_all_profiles()["efax"]
    target = tmp_path / "photo.pdf"
    probe = _save(sample_bytes, efax, tmp_path / "probe.pdf", name="sample10_photo_heavy.pdf",
                  method="split", mode="grayscale")
    count = len(probe.paths)
    material = tmp_path / f"photo_1of{count}.pdf"   # 材料のファイルの名前が、偶然この形のとき
    material.write_bytes(b"material")
    with pytest.raises(saving.SaveError) as info:
        _save(sample_bytes, efax, target, name="sample10_photo_heavy.pdf", method="split",
              mode="grayscale", materials=[material])
    assert info.value.kind == "split_name_is_material"
    assert material.read_bytes() == b"material"


def test_write_error_becomes_save_error_and_keeps_target(tmp_path, sample_bytes, motfax, monkeypatch):
    target = tmp_path / "doc.pdf"
    target.write_bytes(b"old")
    monkeypatch.setattr(saving.os, "replace",
                        lambda *_a, **_k: (_ for _ in ()).throw(PermissionError("locked")))
    with pytest.raises(saving.SaveError) as info:
        _save(sample_bytes, motfax, target)
    assert info.value.kind == "write_failed:PermissionError"
    assert "locked" not in str(info.value)
    assert target.read_bytes() == b"old"
    assert sorted(os.listdir(tmp_path)) == ["doc.pdf"]


# ---------------------------------------------------------------- 「何もしない」（F-11・F-13）

def test_compression_none_writes_pages_as_is(tmp_path, sample_bytes, motfax):
    """圧縮方式「何もしない」：ページを画像にせず、そのまま書く（文字データが残る）。"""
    target = tmp_path / "doc.pdf"
    outcome = _save(sample_bytes, motfax, target, mode="none")
    assert target.read_bytes() == sample_bytes("sample01_a4_text.pdf")
    assert outcome.page_counts == (3,) and not outcome.split
    reader = pypdf.PdfReader(io.BytesIO(target.read_bytes()))
    assert any((page.extract_text() or "").strip() for page in reader.pages)


def test_capacity_none_saves_even_when_over_limit(tmp_path, sample_bytes):
    """容量超過時「何もしない」：上限を超えていても、そのまま書く（解像度も下げず、分割もしない）。"""
    from config.profiles import load_all_profiles
    efax = load_all_profiles()["efax"]
    target = tmp_path / "photo.pdf"
    outcome = _save(sample_bytes, efax, target, name="sample10_photo_heavy.pdf", method="none",
                    mode="grayscale")
    assert not outcome.split and outcome.paths == (target,)
    assert outcome.sizes[0] > efax.max_file_size_bytes
    assert target.stat().st_size == outcome.sizes[0]


def test_split_pages_as_is_returns_input_when_under_limit(sample_bytes, motfax):
    from core import size_control
    data = sample_bytes("sample01_a4_text.pdf")
    assert size_control.split_pages_as_is(data, motfax) == [(data, 3)]


def test_split_pages_as_is_splits_by_real_size(sample_bytes, motfax):
    """上限を小さくしたプロファイルで、ページの順を保ったまま分かれること
    （圧縮方式「何もしない」×「分割して再実行」）。"""
    from dataclasses import replace
    from core import size_control
    data = sample_bytes("sample06_mixed_sizes.pdf")
    small = replace(motfax, max_file_size_bytes=int(len(data) * 0.7))
    parts = size_control.split_pages_as_is(data, small)
    assert len(parts) > 1 and sum(count for _pdf, count in parts) == 4
    assert all(len(pdf) <= small.max_file_size_bytes for pdf, _count in parts)
    # 1ページ単体で上限を超えるときは、画質を変えない以上は収められないので、止める
    tiny = replace(motfax, max_file_size_bytes=200)
    with pytest.raises(size_control.SizeLimitExceededError) as info:
        size_control.split_pages_as_is(data, tiny)
    assert "何もしない" in str(info.value)


def test_compression_none_with_split_writes_numbered_files(tmp_path, sample_bytes, motfax):
    from dataclasses import replace
    data = sample_bytes("sample06_mixed_sizes.pdf")
    small = replace(motfax, max_file_size_bytes=int(len(data) * 0.7))
    target = tmp_path / "doc.pdf"
    outcome = _save(sample_bytes, small, target, name="sample06_mixed_sizes.pdf", method="split",
                    mode="none")
    assert outcome.split and sum(outcome.page_counts) == 4
    assert not target.exists() and all(p.exists() for p in outcome.paths)


# ---------------------------------------------------------------- 「何もしない」のページ番号（F-37。Ver210）

def test_none_mode_stamps_numbers_without_rasterizing(tmp_path, motfax, sample_bytes):
    """「何もしない」でも番号を入れる。ページは画像にせず（文字データが残る）、番号だけを重ねる。"""
    import io as _io
    import pypdf as _pypdf
    from core.convert import ConvertOptions
    from core.saving import save_document
    data = sample_bytes("sample01_a4_text.pdf")
    target = tmp_path / "out.pdf"
    save_document(data, target, motfax, ConvertOptions(mode="none", page_numbers=True), "none",
                  "culprit_only", [])
    reader = _pypdf.PdfReader(_io.BytesIO(target.read_bytes()))
    texts = [page.extract_text() or "" for page in reader.pages]
    assert all("本書は" in text for text in texts)                       # 元の文字データが残る
    assert [f"{i + 1}/3" in text for i, text in enumerate(texts)] == [True, True, True]
    # 番号を入れないときは、そのまま（バイト単位で同じ）
    save_document(data, target, motfax, ConvertOptions(mode="none"), "none", "culprit_only", [])
    assert target.read_bytes() == data
