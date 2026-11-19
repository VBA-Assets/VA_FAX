# -*- coding: utf-8 -*-
"""メイン画面（F-15・F-32・F-33）のテスト：開く・保存と、ページの編集。

ウィンドウは作成後ただちにwithdraw()して画面に出さない（自動テストでGUIを実際に起動しない）。
メッセージ・ファイルを選ぶ画面・確認の画面は差し替え、mainloop()は使わず、update()を繰り返して
裏の処理（事前診断・保存）の結果を待つ。設定・ログの置き場（%APPDATA%）は一時フォルダにする。
画面のない環境ではスキップする。
"""
from __future__ import annotations

import io
from dataclasses import replace
import shutil
import time
from pathlib import Path

import pypdf
import pytest

pytest.importorskip("tkinter")

import tkinter as tk  # noqa: E402

from PIL import Image  # noqa: E402

from config.profiles import load_all_profiles  # noqa: E402
from config.settings import load_settings  # noqa: E402
from gui import app as app_module  # noqa: E402
from gui.app import VaFaxApp, fit_geometry_to_screen, suggested_save_name, window_title  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


# ---------------------------------------------------------------- 計算の部分

def test_window_title():
    assert window_title(None, False) == "VA FAX"
    assert window_title("送信票.pdf", False) == "送信票.pdf - VA FAX"
    assert window_title("送信票.pdf", True) == "＊送信票.pdf - VA FAX"


def test_suggested_save_name():
    assert suggested_save_name(Path("C:/x/甲001.pdf"), "_fax") == "甲001_fax.pdf"
    assert suggested_save_name(Path("C:/x/写真.jpg"), "_fax") == "写真_fax.pdf"
    assert suggested_save_name(Path("C:/x/甲001_fax.pdf"), "_fax") == "甲001_fax.pdf"   # 重ねない
    assert suggested_save_name(Path("C:/x/甲001_FAX.pdf"), "_fax") == "甲001_FAX.pdf"
    assert suggested_save_name(Path("C:/x/甲001.pdf"), None) == "甲001.pdf"
    assert suggested_save_name(Path("C:/x/写真.jpg"), None) == "写真.pdf"
    assert suggested_save_name(Path("C:/x/甲001.pdf"), "-送信用") == "甲001-送信用.pdf"


@pytest.mark.parametrize("saved, expected", [
    ("1200x800+40+30", "1200x800+40+30"),
    ("1200x800+3000+30", "1200x800"),      # 外した画面の位置：大きさだけを使う
    ("1200x800+40+2000", "1200x800"),
    ("1200x800-10+30", "1200x800"),
    ("4000x3000+0+0", "1920x1080+0+0"),    # 画面より大きい：画面に収める
    ("abc", None), (None, None),
])
def test_fit_geometry_to_screen(saved, expected):
    assert fit_geometry_to_screen(saved, 1920, 1080) == expected


# ---------------------------------------------------------------- 画面

class _Recorder:
    """メッセージの画面の代わり。出そうとした文を覚える。"""

    def __init__(self):
        self.errors: list[str] = []
        self.infos: list[str] = []
        self.warnings: list[str] = []

    def showerror(self, _title, message, **_kwargs):
        self.errors.append(message)

    def showinfo(self, _title, message, **_kwargs):
        self.infos.append(message)

    def showwarning(self, _title, message, **_kwargs):
        self.warnings.append(message)

    def askyesno(self, *_args, **_kwargs):
        return True


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(app_module, "_bring_to_front", lambda _window: None)
    recorder = _Recorder()
    monkeypatch.setattr(app_module, "messagebox", recorder)
    try:
        instance = VaFaxApp(load_settings(), load_all_profiles()["motfax"])
    except tk.TclError as e:
        pytest.skip(f"画面がない環境（{e}）")
    instance.withdraw()
    instance.messages = recorder
    instance.work = tmp_path / "work"
    instance.work.mkdir()
    yield instance
    _pump(instance, lambda: not instance._saving)
    instance.destroy()


def _pump(app, condition, timeout: float = 60.0) -> None:
    """裏の処理の結果が画面に届くまで、update()を繰り返す。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.update()
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("時間内に終わらなかった")


def _ready(app) -> None:
    _pump(app, lambda: app.diagnosis is not None and not app._saving)


def _copy_sample(app, name: str, as_name: str | None = None) -> Path:
    path = app.work / (as_name or name)
    shutil.copyfile(SAMPLES / name, path)
    return path


def _open(app, name="sample01_a4_text.pdf") -> Path:
    path = _copy_sample(app, name)
    assert app._open_path(path)
    _ready(app)
    return path


def _has_text(path: Path) -> bool:
    reader = pypdf.PdfReader(io.BytesIO(path.read_bytes()))
    return any((page.extract_text() or "").strip() for page in reader.pages)


def _state(button) -> str:
    return str(button.cget("state"))


def _save_to(app, monkeypatch, target: Path) -> None:
    """保存の画面で target を選ぶ（既にあるファイルを選んだときの置き換えの確認は、Windows の画面が出す）。"""
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **_k: str(target))


def test_save_is_one_large_button(app):
    """保存は［保存］の1つ（大きいボタン。2026-11-18 班長指示で、上書きの［保存］と［名前を付けて保存］を
    1つにした）。メニューも［保存…］だけ。"""
    from gui import theme
    # 青枠（アクセント色の枠線）は［保存］だけ。［開く］［新規作成］は普通の大きいボタン（2026-11-18 班長指示）。
    # ［保存］の青枠は、保存すると違うものができるときだけ（文書がないときは出さない）
    assert str(app.save_button.cget("style")) == theme.RIBBON_LARGE_BUTTON_STYLE
    assert str(app.open_button.cget("style")) == theme.RIBBON_LARGE_BUTTON_STYLE
    assert str(app.new_button.cget("style")) == theme.RIBBON_LARGE_BUTTON_STYLE
    assert str(app.save_button.cget("compound")) == "top" and app.save_button.cget("text") == "保存"
    assert not hasattr(app, "save_as_button")
    menu = app._file_menu
    labels = [menu.entrycget(i, "label") for i in range(menu.index("end") + 1) if menu.type(i) == "command"]
    assert "保存…" in labels and "保存" not in labels and "名前を付けて保存…" not in labels


def test_starts_empty(app):
    assert app.title() == "VA FAX"
    assert _state(app.open_button) == "normal"
    for button in (app.save_button, app.add_button, app.delete_button,
                   app.undo_button, app.rotate_button):
        assert _state(button) == "disabled"


def test_open_shows_document(app):
    path = _open(app)
    # 開いた直後は、まだ保存していない（二値化して送る形にしていない）ので「＊」を付ける（2026-11-18 班長指示）
    assert app.title() == f"＊{path.name} - VA FAX"
    assert app.document.page_count == 3
    assert app.preview_pane._page_count == 3 and app.preview_pane.selected_pages() == [0]
    assert _state(app.save_button) == "normal" and _state(app.add_button) == "normal"
    assert _state(app.undo_button) == "disabled"       # 開く前には戻せない
    assert "総ページ数：3ページ" in app.diagnosis_text.get("1.0", "end")


def test_broken_file_is_not_opened(app):
    path = app.work / "broken.pdf"
    path.write_bytes((SAMPLES / "sample01_a4_text.pdf").read_bytes()[:500])
    assert not app._open_path(path)
    assert app.document is None and "broken.pdf" in app.messages.errors[0]
    assert not app._open_path(_copy_sample(app, "sample11_encrypted.pdf"))
    assert "パスワード" in app.messages.errors[1]


def test_page_edits_mark_document_unsaved_and_can_be_undone(app):
    path = _open(app)
    app.preview_pane.select_page(1)
    app._delete_pages()
    _ready(app)
    assert app.document.page_count == 2 and app.preview_pane._page_count == 2
    assert app.title() == f"＊{path.name} - VA FAX"
    assert _state(app.undo_button) == "normal" and _state(app.redo_button) == "disabled"

    app._rotate_pages(90)
    _ready(app)
    assert app.document.pages[app.preview_pane.selected_pages()[0]].rotation == 90

    app._move_pages([0], 2)
    _ready(app)
    assert app.preview_pane.selected_pages() == [1]    # 動かしたページを選択している

    app._on_undo()
    app._on_undo()
    app._on_undo()
    _ready(app)
    assert app.document.page_count == 3 and _state(app.undo_button) == "disabled"
    assert _state(app.redo_button) == "normal"
    app._on_redo()
    _ready(app)
    assert app.document.page_count == 2


def _menu(app) -> dict[str, bool]:
    """右クリックメニューの項目（文字 → 押せるか）。"""
    return {item[0]: item[2] for item in app._page_menu_items() if item is not None}


def test_page_context_menu_offers_page_commands(app):
    """サムネイルの右クリックメニューから、ページの各コマンドを実行できる。"""
    _open(app)
    assert list(_menu(app)) == ["追加…", "削除", "左に90°回転", "右に90°回転", "180°回転",
                                "元に戻す", "やり直す"]
    assert _menu(app) == {"追加…": True, "削除": True, "左に90°回転": True, "右に90°回転": True,
                          "180°回転": True, "元に戻す": False, "やり直す": False}

    # 選択していないページを右クリックすると、そのページだけを選んでからメニューを出す
    shown = []
    app.preview_pane.on_context_menu = lambda x, y: shown.append((x, y))
    from types import SimpleNamespace
    app.preview_pane._on_thumb_context(2, SimpleNamespace(x_root=10, y_root=20))
    assert app.preview_pane.selected_pages() == [2] and shown == [(10, 20)]

    # メニューの［右に90°回転］を実行する
    commands = {item[0]: item[1] for item in app._page_menu_items() if item is not None}
    commands["右に90°回転"]()
    _ready(app)
    assert app.document.pages[2].rotation == 90
    assert _menu(app)["元に戻す"] and not _menu(app)["やり直す"]
    commands["元に戻す"]()
    _ready(app)
    assert app.document.pages[2].rotation == 0 and _menu(app)["やり直す"]

    # 全ページを選んでいるときは、削除できない
    app.preview_pane.select_all()
    assert not _menu(app)["削除"]


def test_rotate_button_is_one_dropdown(app):
    """リボンの回転は1つのボタンで、角度はメニューで選ぶ。"""
    menu = app.rotate_button.menu
    labels = [menu.entrycget(i, "label") for i in range(menu.index("end") + 1)]
    assert labels == ["左に90°回転", "右に90°回転", "180°回転"]
    assert _state(app.rotate_button) == "disabled"
    _open(app)
    assert _state(app.rotate_button) == "normal"
    menu.invoke(0)
    _ready(app)
    assert app.document.pages[0].rotation == 270


def test_last_page_cannot_be_deleted(app):
    _open(app, "sample03_a3_text.pdf")
    assert _state(app.delete_button) == "disabled"
    app._delete_pages([0])
    assert app.document.page_count == 1 and app.messages.infos


def test_add_pdf_and_image_after_selected_page(app):
    _open(app)
    extra = _copy_sample(app, "sample02_b4_text.pdf")
    image = app.work / "photo.png"
    Image.new("RGB", (400, 300), "white").save(image, dpi=(100, 100))
    app.preview_pane.select_page(0)
    app._add_paths([extra, image], at=1)
    _ready(app)
    assert app.document.page_count == 6
    assert [ref.source for ref in app.document.pages] == [0, 1, 1, 2, 0, 0]
    assert app.preview_pane.selected_pages() == [1, 2, 3]      # 追加したページを選択している
    assert "材料のファイル数：3件" in app.diagnosis_text.get("1.0", "end")


def test_save_always_asks_for_the_place_and_name(app, monkeypatch):
    """［保存］は、毎回、保存の画面で場所と名前を尋ねる。取りやめたら何も書かない。開いたファイルの
    名前を選べば上書きする（置き換えの確認は、保存の画面〔Windows〕が出す）。"""
    path = _open(app)
    original = path.read_bytes()
    asked = []
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename",
                        lambda **kwargs: (asked.append(kwargs), "")[1])
    app._on_save()
    assert not app._saving and path.read_bytes() == original   # 取りやめ：何も書かない
    assert asked[0]["title"] == "保存" and asked[0]["initialfile"] == "sample01_a4_text_fax.pdf"
    assert "confirmoverwrite" not in asked[0]                  # 置き換えの確認を出す（既定のまま）

    _save_to(app, monkeypatch, path)
    app._on_save()
    _pump(app, lambda: not app._saving)
    assert path.read_bytes() != original and not _has_text(path)
    assert app.title() == f"{path.name} - VA FAX"
    assert app.preview_pane._raw_mode                           # 保存した結果を表示している
    assert app.status_var.get().startswith(f"保存しました：{path.name}（3ページ、")
    assert not hasattr(app, "log_text")                         # 処理ログの欄はない

    # もう一度保存しても、材料（元のページ）から作り直す
    first_saved = path.read_bytes()
    app._on_save()
    _pump(app, lambda: not app._saving)
    assert path.read_bytes() == first_saved
    assert not app.messages.errors


def test_save_with_another_name_keeps_opened_file(app, monkeypatch):
    path = _open(app)
    original = path.read_bytes()
    other = app.work / "別名.pdf"
    _save_to(app, monkeypatch, other)
    app._on_save()
    _pump(app, lambda: not app._saving)
    assert path.read_bytes() == original and other.exists() and not _has_text(other)
    assert app.title() == "別名.pdf - VA FAX"


def _open_image(app, name: str = "受領書.jpg") -> Path:
    """画像を開く（名前のない文書になる）。"""
    image = app.work / name
    Image.new("RGB", (600, 800), "white").save(image)
    assert app._open_path(image)
    _ready(app)
    return image


def test_no_open_copy_command(app):
    """［コピーして開く］は、［保存］を1つにまとめて働きに差がなくなったので、なくした（2026-11-18 班長指示）。"""
    assert not hasattr(app, "open_copy_button")
    menu = app._file_menu
    labels = [menu.entrycget(i, "label") for i in range(menu.index("end") + 1) if menu.type(i) == "command"]
    assert not [label for label in labels if "コピー" in label]


def test_opened_image_is_untitled_and_keeps_original(app, monkeypatch):
    path = _open_image(app)
    original = path.read_bytes()
    assert app.title() == "＊受領書_fax.pdf - VA FAX"
    asked = {}

    def fake_dialog(**kwargs):
        asked.update(kwargs)
        return str(app.work / kwargs["initialfile"])

    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", fake_dialog)
    app._on_save()
    _pump(app, lambda: not app._saving)
    saved = app.work / "受領書_fax.pdf"
    assert asked["initialfile"] == "受領書_fax.pdf" and asked["initialdir"] == str(app.work)
    assert saved.exists() and not _has_text(saved)
    assert path.read_bytes() == original
    assert app.title() == "受領書_fax.pdf - VA FAX"


def test_image_is_opened_as_untitled_document(app):
    image = app.work / "受領書.jpg"
    Image.new("RGB", (600, 800), "white").save(image)
    assert app._open_path(image)
    _ready(app)
    assert app._save_path is None and app.title() == "＊受領書_fax.pdf - VA FAX"


def test_save_onto_added_file_is_refused(app, monkeypatch):
    _open(app)
    extra = _copy_sample(app, "sample02_b4_text.pdf")
    original = extra.read_bytes()
    app._add_paths([extra], at=None)
    _ready(app)
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **_k: str(extra))
    app._on_save()
    assert not app._saving and extra.read_bytes() == original
    assert "追加したファイルと同じ名前" in app.messages.errors[0]


def test_changing_options_marks_unsaved_and_returns_to_simulation(app, monkeypatch):
    path = _open(app)
    _save_to(app, monkeypatch, path)
    app._on_save()
    _pump(app, lambda: not app._saving)
    assert app.title() == f"{path.name} - VA FAX" and app.preview_pane._raw_mode
    app.mode_var.set("grayscale")
    assert app.title() == f"＊{path.name} - VA FAX" and not app.preview_pane._raw_mode


def test_split_keeps_opened_file_and_document_unsaved(app, monkeypatch):
    app.profile = load_all_profiles()["efax"]
    path = _open(app, "sample10_photo_heavy.pdf")
    original = path.read_bytes()
    app.mode_var.set("grayscale")
    app.capacity_method_var.set("split")
    notices = []
    _save_to(app, monkeypatch, path)
    monkeypatch.setattr(app, "_notify_split", lambda text, **_kw: (notices.append(text), "close")[1])
    app._on_save()
    _pump(app, lambda: not app._saving, timeout=180)
    parts = sorted(app.work.glob("sample10_photo_heavy_*of*.pdf"))
    assert len(parts) > 1 and path.read_bytes() == original
    assert app.title().startswith("＊")                         # 開いたファイルは変えていない＝未保存のまま
    assert parts[0].name in notices[0] and "変えていません" in notices[0]


def test_unsaved_changes_are_asked_before_closing(app, monkeypatch):
    _open(app)
    app._rotate_pages(180)
    _ready(app)
    answers = ["cancel", "discard"]
    monkeypatch.setattr(app, "_ask_save_changes", lambda _name: answers.pop(0))
    app._on_close_document()
    assert app.document is not None                             # キャンセル：閉じない
    app._on_close_document()
    assert app.document is None and app.title() == "VA FAX"
    assert _state(app.save_button) == "disabled" and app.preview_pane._page_count == 0


def test_save_then_continue_when_closing(app, monkeypatch):
    """閉じるときに「保存する」を選ぶと、保存が済んでから閉じる。"""
    path = _open(app)
    app._rotate_pages(180)
    _ready(app)
    monkeypatch.setattr(app, "_ask_save_changes", lambda _name: "save")
    _save_to(app, monkeypatch, path)
    app._on_close_document()
    _pump(app, lambda: app.document is None)
    assert not _has_text(path)


def test_diagnostic_text_has_no_file_names(app):
    path = _open(app)
    text = app._diagnostic_text()
    assert "文書：3ページ" in text
    assert path.name not in text and path.stem not in text and str(app.work) not in text


# ---------------------------------------------------------------- 「何もしない」（F-11・F-13）

def test_nothing_mode_disables_what_does_not_apply(app):
    _open(app)
    app.capacity_method_var.set("reduce_resolution")
    app.mode_var.set("none")
    app._update_mode_widgets()
    assert app.capacity_method_var.get() == "none"              # 効かない選択から切り替える
    assert _state(app.reduce_resolution_radio) == "disabled"
    assert _state(app.normalize_check) == "disabled"
    assert "効きません" in app.none_note_var.get()
    app.mode_var.set("binarize")
    app._update_mode_widgets()
    assert app.capacity_method_var.get() == "reduce_resolution"  # 圧縮方式を戻したら、元に戻す
    assert _state(app.reduce_resolution_radio) == "normal" and app.none_note_var.get() == ""


def test_nothing_mode_saves_onto_opened_file(app, monkeypatch):
    """圧縮方式「何もしない」で、開いたファイルの名前を選んで保存する。番号を入れないときは、元の
    ファイルとバイト単位で同じ。"""
    path = _open(app)
    original = path.read_bytes()
    _save_to(app, monkeypatch, path)
    app.mode_var.set("none")
    app.page_numbers_var.set(False)
    app._update_mode_widgets()
    app._on_save()
    _pump(app, lambda: not app._saving)
    assert path.read_bytes() == original and _has_text(path)
    assert not app.messages.warnings and not app.messages.errors


def test_nothing_mode_cancel_keeps_file_when_pages_were_edited(app, monkeypatch):
    path = _open(app)
    original = path.read_bytes()
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **_k: "")
    app.mode_var.set("none")
    app._update_mode_widgets()
    app._rotate_pages(180)
    _ready(app)
    app._on_save()
    assert not app._saving and path.read_bytes() == original


def test_nothing_mode_warns_about_constraints_after_saving(app, monkeypatch):
    """圧縮方式「何もしない」では、制約（用紙サイズ等）に合わなくても止めず、保存の後に知らせる。"""
    path = _open(app, "sample03_a3_text.pdf")                   # A3（MOT/FAXの対応外）
    _save_to(app, monkeypatch, path)
    app.mode_var.set("none")
    app._update_mode_widgets()
    app._on_save()
    _pump(app, lambda: not app._saving)
    assert _has_text(path)
    assert "対応外の用紙サイズ" in app.messages.warnings[0]
    assert app.title() == f"{path.name} - VA FAX"


def test_capacity_nothing_saves_over_limit_and_warns(app, monkeypatch):
    app.profile = load_all_profiles()["efax"]
    path = _open(app, "sample10_photo_heavy.pdf")
    _save_to(app, monkeypatch, path)
    app.mode_var.set("grayscale")
    app.capacity_method_var.set("none")
    app._on_save()
    _pump(app, lambda: not app._saving, timeout=180)
    assert path.stat().st_size > app.profile.max_file_size_bytes
    assert "容量上限" in app.messages.warnings[0] and not app.messages.errors
    assert not app.title().startswith("＊")


def test_file_saved_by_this_tool_opens_with_nothing_mode(app, monkeypatch):
    """本ツールで二値化して保存したファイルを開き直すと、圧縮方式の初めの値が「何もしない」になる。"""
    path = _open(app)
    _save_to(app, monkeypatch, path)
    app._on_save()
    _pump(app, lambda: not app._saving)
    app._close_document()
    assert app._open_path(path)
    _ready(app)
    assert app.mode_var.get() == "none"
    other = _copy_sample(app, "sample02_b4_text.pdf")
    assert app._open_path(other)
    _ready(app)
    assert app.mode_var.get() == "binarize"


def test_window_state_is_saved_on_quit(app, monkeypatch):
    app._normal_geometry = "1000x720+12+34"
    destroyed = []
    real_destroy = app.destroy
    monkeypatch.setattr(app, "destroy", lambda: destroyed.append(True))
    app._on_quit()
    monkeypatch.setattr(app, "destroy", real_destroy)   # 後始末では、本当に閉じる
    assert destroyed == [True]
    assert load_settings().window_geometry == "1000x720+12+34"


# ---------------------------------------------------------------- 保存の名前・オプション（F-32・F-35）

def _save_as_dialog_args(app, monkeypatch) -> dict:
    asked = {}
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename",
                        lambda **kwargs: (asked.update(kwargs), "")[1])   # 名前を尋ねるだけで、取りやめる
    app._on_save()
    return asked


def test_save_as_suggests_name_with_fax(app, monkeypatch):
    """開いたファイルの［保存］：名前の初めの値は「_fax」付き、場所は開いたファイルのフォルダ。"""
    path = _open(app)
    asked = _save_as_dialog_args(app, monkeypatch)
    assert asked["initialfile"] == "sample01_a4_text_fax.pdf"
    assert asked["initialdir"] == str(path.parent)
    assert app.title() == f"＊{path.name} - VA FAX"              # 保存先（題名）は、開いたファイルのまま


def test_save_as_after_saving_does_not_double_the_suffix(app, monkeypatch):
    path = _open(app)
    saved = app.work / "sample01_a4_text_fax.pdf"
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **_k: str(saved))
    app._on_save()
    _pump(app, lambda: not app._saving)
    assert saved.exists() and app.title() == "sample01_a4_text_fax.pdf - VA FAX"
    assert _save_as_dialog_args(app, monkeypatch)["initialfile"] == "sample01_a4_text_fax.pdf"
    assert path.exists()


def _run_options(app, monkeypatch, answer):
    """［ファイル］→［オプション］を開き、answer(dialog) で画面を操作する。answer が True を返したら
    ［保存］を押し、False ならキャンセルする。"""
    monkeypatch.setattr(app_module.OptionsDialog, "grab_set", lambda self: None)
    real_wait = app.wait_window
    original_init = app_module.OptionsDialog.__init__
    seen = {}

    def init_and_answer(self, master, settings, current_profile_id):
        original_init(self, master, settings, current_profile_id)
        self.withdraw()
        seen["dialog"] = self
        if answer(self):
            self._on_save()
        else:
            self.destroy()

    monkeypatch.setattr(app_module.OptionsDialog, "__init__", init_and_answer)
    monkeypatch.setattr(app, "wait_window", lambda _window=None: None)   # 画面の中で閉じ終えている
    app._on_options()
    monkeypatch.setattr(app, "wait_window", real_wait)
    return seen.get("dialog")


def test_options_has_no_settings_menu(app):
    """［設定］メニューはなく、FAXサービス・処理内容の既定値は、オプションの画面にまとめてある。"""
    menubar = app.nametowidget(app.cget("menu"))
    labels = [menubar.entrycget(i, "label") for i in range(menubar.index("end") + 1)
              if menubar.type(i) == "cascade"]
    assert "設定" not in labels and "ファイル" in labels and "ヘルプ" in labels
    menu = app._file_menu
    entries = [menu.entrycget(i, "label") if menu.type(i) == "command" else "―"
               for i in range(menu.index("end") + 1)]
    # 標準 02「メニュー」の並び。［終了］は置かない（ウィンドウの［×］で閉じる）。画面が開く項目は全角の「…」
    assert entries == ["新規作成", "開く…", "保存…", "閉じる", "―", "オプション…"]


def test_help_menu_and_about(app):
    """［ヘルプ］は「診断情報をコピー／区切り／VA FAX について」。「について」は5行（標準 01「について」）。"""
    menubar = app.nametowidget(app.cget("menu"))
    help_index = [i for i in range(menubar.index("end") + 1)
                  if menubar.type(i) == "cascade" and menubar.entrycget(i, "label") == "ヘルプ"][0]
    menu = app.nametowidget(menubar.entrycget(help_index, "menu"))
    entries = [menu.entrycget(i, "label") if menu.type(i) == "command" else "―"
               for i in range(menu.index("end") + 1)]
    assert entries == ["診断情報をコピー", "―", "VA FAX について"]
    from version import VERSION_LABEL
    assert app.about_text().split("\n") == [
        f"VA FAX  {VERSION_LABEL}", "FAX送信用PDFの準備（ページの編集・送付書・変換・容量調整）", "",
        "VBAアセット（vba-assets.net）", "MIT License"]


def test_option_turns_off_the_suffix(app, monkeypatch):
    """［ファイル］→［オプション］で外すと、［保存］の名前の初めの値と、画像を開いた文書の名前（題名）の
    両方に、文字を付けない。設定ファイルに覚える。"""
    _open_image(app)
    assert app.title() == "＊受領書_fax.pdf - VA FAX"

    def answer(dialog):
        assert [dialog.notebook.tab(i, "text") for i in range(4)] == ["保存", "処理内容の既定値", "FAXサービス", "送付書"]
        assert dialog._append_suffix_var.get() is True and dialog._suffix_var.get() == "_fax"
        dialog._append_suffix_var.set(False)
        dialog._update_suffix_state()
        assert str(dialog._suffix_entry.cget("state")) == "disabled"
        return True

    _run_options(app, monkeypatch, answer)
    assert app.settings.append_fax_to_save_name is False
    assert app.title() == "＊受領書.pdf - VA FAX"
    assert _save_as_dialog_args(app, monkeypatch)["initialfile"] == "受領書.pdf"
    assert load_settings().append_fax_to_save_name is False


def test_option_changes_the_suffix_text(app, monkeypatch):
    path = _open(app)
    _run_options(app, monkeypatch, lambda d: (d._suffix_var.set(" -送信用 "), True)[1])
    assert app.settings.save_name_suffix == "-送信用"
    assert _save_as_dialog_args(app, monkeypatch)["initialfile"] == f"{path.stem}-送信用.pdf"
    _open_image(app, "写真.png")
    assert app.title() == "＊写真-送信用.pdf - VA FAX"
    assert load_settings().save_name_suffix == "-送信用"


@pytest.mark.parametrize("bad", ["", "   ", "a/b", "x:y", "a*", "長" * 21])
def test_option_rejects_unusable_suffix(app, monkeypatch, bad):
    seen = {}

    def answer(dialog):
        dialog._suffix_var.set(bad)
        dialog._on_save()                       # 使えない文字なので、閉じずにエラーを出す
        seen["result"] = dialog.result
        seen["tab"] = dialog.notebook.index(dialog.notebook.select())
        return False

    _run_options(app, monkeypatch, answer)
    assert seen == {"result": None, "tab": 0} and app.messages.errors
    assert app.settings.save_name_suffix == "_fax"


def test_options_dialog_cancel_keeps_settings(app, monkeypatch):
    def answer(dialog):
        dialog._append_suffix_var.set(False)
        dialog._open_folder_var.set(True)
        return False

    _run_options(app, monkeypatch, answer)
    assert app.settings.append_fax_to_save_name is True and app.settings.open_folder_after_save is False


def test_option_changes_defaults_and_applies_them(app, monkeypatch):
    """処理内容の既定値のタブ：出力先・レポートの項目はなく、「何もしない」を選べる。変えると画面に反映する。"""
    _open(app)

    def answer(dialog):
        frame = dialog.defaults_frame
        assert not hasattr(frame, "_folder_policy_var") and not hasattr(frame, "_report_var")
        frame._compression_var.set("none")
        frame._update_compression_states()
        assert frame._capacity_method_var.get() == "none"
        assert str(frame._normalize_check.cget("state")) == "disabled"
        return True

    _run_options(app, monkeypatch, answer)
    assert app.settings.defaults.compression_policy == "none"
    assert app.settings.defaults.capacity_exceeded_method == "none"
    assert app.mode_var.get() == "none"
    assert app.title().startswith("＊")                       # ページ番号を入れるので、まだ保存していない作業がある
    app.page_numbers_var.set(False)
    assert not app.title().startswith("＊")                   # 保存しても同じものしかできない


def test_option_keeps_screen_choices_when_defaults_are_unchanged(app, monkeypatch):
    _open(app)
    app.mode_var.set("grayscale")
    _run_options(app, monkeypatch, lambda d: True)
    assert app.mode_var.get() == "grayscale"


def test_option_changes_fax_service(app, monkeypatch):
    _open(app)
    _run_options(app, monkeypatch, lambda d: (d.profile_frame.var.set("movfax"), True)[1])
    _ready(app)
    assert app.profile.id == "movfax" and load_settings().profile_id == "movfax"
    assert "MOVFAX" in app.profile_label_var.get()


def test_option_switching_to_custom_asks_for_its_content(app, monkeypatch):
    """ほかのサービスからカスタムに替えるときは、内容の入力を挟む。入力を取りやめたら、保存しない。"""
    edits = []
    monkeypatch.setattr(app_module.OptionsDialog, "_edit_custom", lambda self: (edits.append(1), False)[1])
    _run_options(app, monkeypatch, lambda d: (d.profile_frame.var.set("custom"), True)[1])
    assert edits == [1] and app.profile.id == "motfax"


def test_open_folder_after_save(app, monkeypatch):
    path = _open(app)
    opened = []
    monkeypatch.setattr(app, "_open_folder", lambda folder: opened.append(folder))
    _save_to(app, monkeypatch, path)
    app._on_save()
    _pump(app, lambda: not app._saving)
    assert opened == []                                         # 初めは、開かない
    _run_options(app, monkeypatch, lambda d: (d._open_folder_var.set(True), True)[1])
    app._on_save()
    _pump(app, lambda: not app._saving)
    assert opened == [path.parent]


# ---------------------------------------------------------------- 送付書（F-36。Ver210）

def _cover_input(**kwargs):
    from core.cover_sheet import CoverInput
    values = dict(date="令和8年11月18日", court="架空地方裁判所民事第1部", other="被告訴訟代理人弁護士　乙山次郎",
                  sender_title="原告訴訟代理人弁護士", case_number="令和8年（ワ）第123号")
    values.update(kwargs)
    return CoverInput(**values)


def _add(app, name: str, as_name: str) -> Path:
    path = _copy_sample(app, name, as_name)
    app._add_paths([path], None)
    _ready(app)
    return path


def test_cover_lists_materials_and_goes_to_the_front(app):
    _open(app)
    assert _state(app.cover_button) == "normal" and app.cover_button.cget("text") == "作成"
    _add(app, "sample01_a4_text.pdf", "甲001 契約書.pdf")
    _add(app, "sample01_a4_text.pdf", "甲002 領収書.pdf")
    initial = app._cover_initial()
    assert [(d.name, d.pages) for d in initial.documents] == [
        ("sample01_a4_text", "3"), ("甲第1号証〜第2号証", "6")]

    assert app._put_cover(_cover_input(documents=initial.documents))
    _ready(app)
    assert app.document.page_count == 10 and app._current_cover().total == 10
    assert app.document.pages[0].source == len(app.document.sources) - 1
    assert app.preview_pane.selected_pages() == [0] and app.title().startswith("＊")
    assert app.cover_button.cget("text") == "編集"       # 作成済みのときは「編集」（2026-11-18 班長指示）
    # 書類の一覧に、送付書自身は入らない
    assert [d.name for d in app._cover_documents()] == ["sample01_a4_text", "甲第1号証〜第2号証"]

    # もう一度作ると、差し替える（ページ数は変わらない）
    assert app._put_cover(_cover_input(message="2回目"))
    _ready(app)
    assert app.document.page_count == 10 and app._current_cover().input.message == "2回目"
    # 元に戻すと前の送付書に、もう一度戻すと送付書のない文書になる
    app._on_undo()
    _ready(app)
    assert app.document.page_count == 10 and app._current_cover().input.message == ""
    app._on_undo()
    _ready(app)
    assert app.document.page_count == 9 and app._current_cover() is None
    assert app.cover_button.cget("text") == "作成"


def test_double_click_on_cover_page_opens_the_form(app, monkeypatch):
    """送付書のページ（サムネイル・拡大表示）をダブルクリックすると、入力の画面を開く。ほかのページでは開かない。"""
    _open(app)
    opened = []
    monkeypatch.setattr(app, "_on_cover", lambda rebuild_documents=False: opened.append(1))
    app.preview_pane._on_thumb_double_click(0)
    assert opened == []                                  # 送付書がないときは開かない
    app._put_cover(_cover_input())
    _ready(app)
    app.preview_pane._on_thumb_double_click(1)
    assert opened == [] and app.preview_pane.selected_pages() == [1]
    app.preview_pane._on_thumb_double_click(0)
    assert opened == [1] and app.preview_pane.selected_pages() == [0]
    app.preview_pane._on_large_double_click()            # 拡大表示（いまは送付書のページ）
    assert opened == [1, 1]
    # 送付書を後ろへ動かしても、送付書のページで開く
    app._move_pages([0], 4)
    _ready(app)
    app.preview_pane._on_thumb_double_click(3)
    assert opened == [1, 1, 1]


def test_cover_input_is_saved_beside_pdf_and_read_when_opened(app, monkeypatch):
    import json
    _open(app)
    app._put_cover(_cover_input(documents=app._cover_documents()))
    _ready(app)
    saved = app.work / "送付_fax.pdf"
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **_k: str(saved))
    app._on_save()
    _pump(app, lambda: not app._saving)
    data = app.work / "FAX送付書データ" / "送付_fax.json"
    assert saved.exists() and data.exists()
    raw = json.loads(data.read_text(encoding="utf-8"))
    assert raw["cover_pages"] == 1 and raw["total_pages"] == 4
    assert raw["input"]["court"] == "架空地方裁判所民事第1部"
    assert len(pypdf.PdfReader(io.BytesIO(saved.read_bytes())).pages) == 4

    # 開き直すと、1ページ目を送付書とみなし、その入力を初めの値にする
    app._close_document()
    assert app._open_path(saved)
    _ready(app)
    record = app._current_cover()
    assert record is not None and record.refs == frozenset({(0, 0)}) and record.total == 4
    assert app._cover_initial().case_number == "令和8年（ワ）第123号"
    # 書類の一覧の初めの値は、保存したときの一覧（開いたファイルの名前ではない）
    assert [d.name for d in app._cover_initial().documents] == ["sample01_a4_text"]
    # 差し替えると、ページ数は変わらない
    app._put_cover(app._cover_initial())
    _ready(app)
    assert app.document.page_count == 4


def test_document_without_cover_does_not_write_cover_input(app, monkeypatch):
    _open(app)
    saved = app.work / "送付書なし.pdf"
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **_k: str(saved))
    app._on_save()
    _pump(app, lambda: not app._saving)
    assert saved.exists() and not (app.work / "FAX送付書データ").exists()


def test_cover_initial_uses_latest_input_in_same_folder_then_mints(app):
    import json
    from core import cover_sheet as cs
    _open(app)
    assert app._cover_initial().court == ""
    # VA mints の証拠説明書の入力（材料のフォルダにあるもの）
    mints_dir = app.work / "証拠説明書データ"
    mints_dir.mkdir()
    (mints_dir / "証拠説明書.json").write_text(json.dumps({"header": {
        "court": "架空地方裁判所", "case_number": "令和8年（ワ）第5号", "party": "原告",
        "name": "原告訴訟代理人弁護士　甲野太郎"}}, ensure_ascii=False), encoding="utf-8")
    initial = app._cover_initial()
    assert (initial.court, initial.case_number, initial.sender_title) == (
        "架空地方裁判所", "令和8年（ワ）第5号", "原告訴訟代理人弁護士")
    # 同じフォルダの、ほかのファイルの送付書の入力があれば、そちらを使う
    cs.save_input(app.work / "前に送った.pdf", _cover_input(case_number="令和8年（ワ）第7号"), 1, 3)
    assert app._cover_initial().case_number == "令和8年（ワ）第7号"


def test_save_asks_when_cover_count_no_longer_matches(app, monkeypatch):
    _open(app)
    app._put_cover(_cover_input())
    _ready(app)
    app.preview_pane.select_page(3)
    app._delete_pages()
    _ready(app)
    messages = []
    reply = {"value": "cancel"}

    def fake_choice(_master, _title, message, _buttons, cancel_value=None):
        messages.append(message)
        return reply["value"]

    monkeypatch.setattr(app_module, "ask_choice", fake_choice)
    asked = []
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **_k: (asked.append(1), "")[1])
    app._on_save()
    assert "（4枚）" in messages[0] and "（3ページ）" in messages[0] and asked == []
    fixes = []
    monkeypatch.setattr(app, "_on_cover", lambda rebuild_documents=False: fixes.append(rebuild_documents))
    reply["value"] = "fix"
    app._on_save()
    assert fixes == [True] and asked == []
    reply["value"] = "save"
    app._on_save()
    assert asked == [1]


def test_cover_dialog_returns_input_and_rebuilds_list(app, monkeypatch):
    from core.cover_sheet import DocItem
    from gui import cover_dialog
    _open(app)
    monkeypatch.setattr(cover_dialog.CoverSheetDialog, "grab_set", lambda self: None)
    original_init = cover_dialog.CoverSheetDialog.__init__
    seen = {}

    def init_and_answer(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.withdraw()
        seen["rows"] = [(n.get(), p.get()) for n, p in self._rows]
        self.court_var.set("架空地方裁判所")
        self.honorific_var.set("先生")
        self._rows[0][0].set("書き換えた名前")
        self._add_empty_row()
        self._on_rebuild()                       # ［ファイルから作り直す］
        seen["rebuilt"] = self._rows[0][0].get()
        self._rows[0][0].set("準備書面（1）")
        self.message_text.insert("1.0", "よろしくお願いします。")
        seen["title"] = self.title()
        self._on_ok()                            # ［OK］

    monkeypatch.setattr(cover_dialog.CoverSheetDialog, "__init__", init_and_answer)
    monkeypatch.setattr(app, "wait_window", lambda _window=None: None)
    app._on_cover()
    _ready(app)
    assert seen["rows"] == [("sample01_a4_text", "3")] + [("", "")] * 4
    assert seen["rebuilt"] == "sample01_a4_text" and seen["title"] == "送付書"
    cover = app._current_cover().input
    assert cover.court == "架空地方裁判所" and cover.other_honorific == "先生"
    assert cover.documents == [DocItem("準備書面（1）", "3")] and cover.message == "よろしくお願いします。"
    assert app.document.page_count == 4


def test_option_cover_sender_is_saved(app, monkeypatch):
    def answer(dialog):
        dialog.sender_frame._vars["office"].set(" 架空法律事務所 ")
        dialog.sender_frame._address.insert("1.0", "〒000-0000\n架空県架空市1-2-3")
        return True

    _run_options(app, monkeypatch, answer)
    assert app.settings.cover_sender.office == "架空法律事務所"
    assert load_settings().cover_sender.address == "〒000-0000\n架空県架空市1-2-3"


# ---------------------------------------------------------------- ページ番号（F-37。Ver210）

def test_page_numbers_option_on_main_screen_and_defaults(app, monkeypatch):
    path = _open(app)
    original_options_init = app_module.OptionsDialog.__init__
    assert app.page_numbers_var.get() is True
    assert app._current_preview_options().page_numbers is True
    # 「何もしない」のときも選べる（用紙正規化は選べない。2026-11-18 班長指示）
    app.mode_var.set("none")
    app._update_mode_widgets()
    assert str(app.page_numbers_check.cget("state")) == "normal"
    assert str(app.normalize_check.cget("state")) == "disabled"
    assert "ページ番号は入れられます" in app.none_note_var.get()
    app.mode_var.set("binarize")
    app._update_mode_widgets()
    assert str(app.page_numbers_check.cget("state")) == "normal"

    # 保存したページの上端に番号が入る
    saved = app.work / "番号付き.pdf"
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **_k: str(saved))
    app._on_save()
    _pump(app, lambda: not app._saving)
    import pypdfium2
    page = pypdfium2.PdfDocument(saved.read_bytes())[0].render(scale=200 / 72).to_pil().convert("L")
    assert page.crop((0, 0, page.width, 120)).getextrema()[0] < 128

    # オプションの既定値でオフにすると、画面の選択も戻る
    def answer(dialog):
        dialog.defaults_frame._page_numbers_var.set(False)
        return True

    _run_options(app, monkeypatch, answer)
    assert app.settings.defaults.page_numbers is False and app.page_numbers_var.get() is False
    # オプションでオフにしたときは、処理内容の選択に出さない（2026-11-18 班長指示）
    assert app.page_numbers_check.winfo_manager() == ""
    assert app._current_preview_options().page_numbers is False
    # _run_options は画面の作り方を差し替えるので、2回目の前に元に戻す
    monkeypatch.setattr(app_module.OptionsDialog, "__init__", original_options_init)
    _run_options(app, monkeypatch, lambda d: (d.defaults_frame._page_numbers_var.set(True), True)[1])
    assert app.page_numbers_check.winfo_manager() == "grid" and app.page_numbers_var.get() is True
    assert path.exists()


def test_normalize_hidden_when_option_is_off(app, monkeypatch):
    """用紙正規化も、オプションの既定値でオフにすると、処理内容の選択に出さず、行わない（2026-11-18 班長指示）。"""
    _open(app)
    assert app.normalize_check.winfo_manager() == "grid"
    original_options_init = app_module.OptionsDialog.__init__
    _run_options(app, monkeypatch, lambda d: (d.defaults_frame._normalize_var.set(False), True)[1])
    assert app.normalize_check.winfo_manager() == "" and app.normalize_var.get() is False
    assert app._current_preview_options().normalize_enabled is False
    assert app.page_numbers_check.winfo_manager() == "grid"            # ページ番号は出たまま
    monkeypatch.setattr(app_module.OptionsDialog, "__init__", original_options_init)
    _run_options(app, monkeypatch, lambda d: (d.defaults_frame._normalize_var.set(True), True)[1])
    assert app.normalize_check.winfo_manager() == "grid" and app.normalize_var.get() is True


def test_page_numbers_hidden_at_start_when_option_is_off(tmp_path, monkeypatch):
    from config.settings import ConversionDefaults, Settings, save_settings
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(app_module, "_bring_to_front", lambda _window: None)
    save_settings(Settings(profile_id="motfax", first_run_completed=True,
                           defaults=ConversionDefaults(page_numbers=False, normalize_enabled=False)))
    try:
        instance = VaFaxApp(load_settings(), load_all_profiles()["motfax"])
    except tk.TclError as e:
        pytest.skip(f"画面がない環境（{e}）")
    try:
        instance.withdraw()
        assert instance.page_numbers_check.winfo_manager() == ""
        assert instance.page_numbers_var.get() is False
        assert instance.normalize_check.winfo_manager() == "" and instance.normalize_var.get() is False
    finally:
        instance.destroy()


# ---------------------------------------------------------------- 新規作成（2026-11-18 班長指示）

def test_new_document_starts_empty(app):
    app._on_new()
    assert app.document is not None and app.document.page_count == 0
    assert app.title() == "新しい文書 - VA FAX"                      # 何も入れていない間は、未保存の印なし
    assert _state(app.save_button) == "disabled" and _state(app.add_button) == "normal"
    assert _state(app.cover_button) == "normal" and app.cover_button.cget("text") == "作成"
    assert "ページがありません" in app.diagnosis_text.get("1.0", "end")
    assert app.preview_pane._page_count == 0
    menu = app._file_menu
    labels = [menu.entrycget(i, "label") for i in range(menu.index("end") + 1) if menu.type(i) == "command"]
    assert labels[0] == "新規作成" and _state(app.new_button) == "normal"


def test_new_document_add_pages_then_save_with_first_file_name(app, monkeypatch):
    app._on_new()
    first = _copy_sample(app, "sample01_a4_text.pdf", "準備書面（1）.pdf")
    second = _copy_sample(app, "sample02_b4_text.pdf", "甲001 契約書.pdf")
    app._add_paths([first, second], None)
    _ready(app)
    assert app.document.page_count == 5 and app.title() == "＊新しい文書 - VA FAX"
    # 保存の名前と場所の初めの値は、初めに入れたファイルから
    asked = _save_as_dialog_args(app, monkeypatch)
    assert asked["initialfile"] == "準備書面（1）_fax.pdf" and asked["initialdir"] == str(app.work)
    # 元に戻して空にすると、診断・保存をやめる
    app._on_undo()
    _ready_or_empty = lambda: app.document.page_count == 0
    _pump(app, _ready_or_empty)
    assert app.diagnosis is None and _state(app.save_button) == "disabled"
    assert "ページがありません" in app.diagnosis_text.get("1.0", "end")
    app._on_redo()
    _ready(app)
    saved = app.work / "送信用.pdf"
    _save_to(app, monkeypatch, saved)
    app._on_save()
    _pump(app, lambda: not app._saving)
    assert saved.exists() and app.title() == "送信用.pdf - VA FAX"
    assert first.exists() and second.exists()


def test_new_document_with_cover_only(app, monkeypatch):
    """送付書だけの文書も保存できる（名前の初めの値は「送付書_fax.pdf」）。"""
    app._on_new()
    assert app._put_cover(_cover_input())
    _ready(app)
    assert app.document.page_count == 1 and app._current_cover().total == 1
    assert _save_as_dialog_args(app, monkeypatch)["initialfile"] == "送付書_fax.pdf"


def test_new_asks_to_save_unsaved_changes(app, monkeypatch):
    _open(app)
    app._rotate_pages(180)
    _ready(app)
    answers = ["cancel"]
    monkeypatch.setattr(app, "_ask_save_changes", lambda _name: answers.pop(0))
    app._on_new()
    assert app.document.page_count == 3                              # キャンセル：そのまま
    app._dirty = False
    app._on_new()
    assert app.document.page_count == 0


# ---------------------------------------------------------------- ［保存］の青枠（2026-11-18 班長指示）

def test_save_is_emphasized_only_when_saving_changes_something(app):
    from gui import theme
    accent, plain = theme.RIBBON_ACCENT_BUTTON_STYLE, theme.RIBBON_LARGE_BUTTON_STYLE

    def style():
        return str(app.save_button.cget("style"))

    _open(app)
    assert app.mode_var.get() == "binarize" and style() == accent        # 画像に変換する
    app.mode_var.set("none")
    assert style() == accent                                             # ページ番号を入れる
    app.page_numbers_var.set(False)
    assert style() == plain                                              # 何も変わらない
    app._rotate_pages(180)
    _ready(app)
    assert style() == accent                                             # ページを編集した
    app._on_undo()
    _ready(app)
    assert style() == plain
    app._put_cover(_cover_input())
    _ready(app)
    assert style() == accent                                             # 送付書がある
    app._on_undo()
    _ready(app)
    assert style() == plain
    app.mode_var.set("grayscale")
    assert style() == accent


def test_save_emphasis_for_new_and_image_documents(app):
    from gui import theme
    app._on_new()
    assert str(app.save_button.cget("style")) == theme.RIBBON_LARGE_BUTTON_STYLE      # ページがない
    _open_image(app)
    app.mode_var.set("none")
    app.page_numbers_var.set(False)
    assert str(app.save_button.cget("style")) == theme.RIBBON_ACCENT_BUTTON_STYLE     # 画像をPDFにする


# ---------------------------------------------------------------- 自作の画面の位置（2026-11-18 班長指摘）

def test_dialogs_are_placed_not_at_top_left(app):
    """自作の画面（保存の確認など）は、位置を決めてから出す（左上に出ない）。メイン画面が隠れている
    自動テストでは、隠したまま（画面に出さない）で、位置は画面の中央。"""
    dialog = app_module.ChoiceDialog(app, "保存の確認", "「a.pdf」への変更を保存しますか？",
                                     [("保存する", "save", True), ("キャンセル", "cancel", False)], "cancel")
    try:
        dialog.update_idletasks()
        assert dialog.state() == "withdrawn"
        import re
        x, y = map(int, re.search(r"\+(-?\d+)\+(-?\d+)$", dialog.geometry()).groups())
        width = dialog.winfo_reqwidth()
        assert abs(x + width / 2 - dialog.winfo_screenwidth() / 2) < 5 and y > 0
    finally:
        dialog.destroy()


# ---------------------------------------------------------------- 題名の「＊」・青枠・閉じるときの確認（2026-11-18 班長指示）

def test_unsaved_mark_emphasis_and_close_prompt_follow_one_rule(app, monkeypatch):
    from gui import theme
    path = _open(app)
    asked = []
    monkeypatch.setattr(app, "_ask_save_changes", lambda name: (asked.append(name), "cancel")[1])

    def state():
        return (app.title().startswith("＊"),
                str(app.save_button.cget("style")) == theme.RIBBON_ACCENT_BUTTON_STYLE)

    # 開いた直後（二値化）：まだ送る形にしていない → ＊・青枠・閉じるときに尋ねる
    assert state() == (True, True)
    app._on_close_document()
    assert asked == [path.name] and app.document is not None
    # 保存した直後：なし
    _save_to(app, monkeypatch, path)
    app._on_save()
    _pump(app, lambda: not app._saving)
    assert state() == (False, False)
    app._on_close_document()
    assert app.document is None and asked == [path.name]
    # 「何もしない」・番号なしで開いたファイル：保存しても同じものしかできない → なし
    other = _copy_sample(app, "sample02_b4_text.pdf")
    app.settings.defaults = replace(app.settings.defaults, compression_policy="none", page_numbers=False)
    assert app._open_path(other)
    _ready(app)
    assert app.mode_var.get() == "none" and state() == (False, False)
    app._rotate_pages(90)
    _ready(app)
    assert state() == (True, True)                            # 編集した → あり


def test_save_prompt_text(app, monkeypatch):
    seen = {}
    monkeypatch.setattr(app_module, "ask_choice",
                        lambda _m, title, message, _b, cancel_value=None: (seen.update(t=title, m=message), "cancel")[1])
    path = _open(app)
    app._on_close_document()
    assert seen == {"t": "保存の確認", "m": f"「{path.name}」を保存していません。保存しますか？"}
