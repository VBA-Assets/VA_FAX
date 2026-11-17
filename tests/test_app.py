# -*- coding: utf-8 -*-
"""メイン画面（F-15・F-32・F-33）のテスト：開く・コピーして開く・保存・名前を付けて保存と、ページの編集。

ウィンドウは作成後ただちにwithdraw()して画面に出さない（自動テストでGUIを実際に起動しない）。
メッセージ・ファイルを選ぶ画面・確認の画面は差し替え、mainloop()は使わず、update()を繰り返して
裏の処理（事前診断・保存）の結果を待つ。設定・ログの置き場（%APPDATA%）は一時フォルダにする。
画面のない環境ではスキップする。
"""
from __future__ import annotations

import io
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
    except tk.TclError:
        pytest.skip("画面がない環境")
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


def _open(app, name="sample01_a4_text.pdf", as_copy=False) -> Path:
    path = _copy_sample(app, name)
    assert app._open_path(path, as_copy=as_copy)
    _ready(app)
    return path


def _has_text(path: Path) -> bool:
    reader = pypdf.PdfReader(io.BytesIO(path.read_bytes()))
    return any((page.extract_text() or "").strip() for page in reader.pages)


def _state(button) -> str:
    return str(button.cget("state"))


def test_save_as_is_the_large_button_and_save_is_small(app):
    """このツールでは［名前を付けて保存］が標準（大きいボタン）、［保存］（上書き）は小さいボタン。"""
    from gui import theme
    assert str(app.save_as_button.cget("style")) == theme.RIBBON_ACCENT_BUTTON_STYLE
    assert str(app.save_button.cget("style")) == theme.RIBBON_BUTTON_STYLE
    assert str(app.save_as_button.cget("compound")) == "top"
    assert str(app.save_button.cget("compound")) == "left"


def test_starts_empty(app):
    assert app.title() == "VA FAX"
    assert _state(app.open_button) == "normal"
    for button in (app.save_button, app.save_as_button, app.add_button, app.delete_button,
                   app.undo_button, app.rotate_button):
        assert _state(button) == "disabled"


def test_open_shows_document(app):
    path = _open(app)
    assert app.title() == f"{path.name} - VA FAX"
    assert app.document.page_count == 3
    assert app.preview_pane._page_count == 3 and app.preview_pane.selected_pages() == [0]
    assert _state(app.save_button) == "normal" and _state(app.add_button) == "normal"
    assert _state(app.undo_button) == "disabled"       # 開く前には戻せない
    assert "総ページ数：3ページ" in app.diagnosis_text.get("1.0", "end")


def test_broken_file_is_not_opened(app):
    path = app.work / "broken.pdf"
    path.write_bytes((SAMPLES / "sample01_a4_text.pdf").read_bytes()[:500])
    assert not app._open_path(path, as_copy=False)
    assert app.document is None and "broken.pdf" in app.messages.errors[0]
    assert not app._open_path(_copy_sample(app, "sample11_encrypted.pdf"), as_copy=False)
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
    assert list(_menu(app)) == ["追加...", "削除", "左に90°回転", "右に90°回転", "180°回転",
                                "元に戻す", "やり直す"]
    assert _menu(app) == {"追加...": True, "削除": True, "左に90°回転": True, "右に90°回転": True,
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


def test_save_asks_before_overwriting_every_time(app, monkeypatch):
    path = _open(app)
    original = path.read_bytes()
    answers = ["cancel", "replace"]
    asked = []
    monkeypatch.setattr(app, "_ask_overwrite", lambda name: (asked.append(name), answers.pop(0))[1])

    app._on_save()
    assert not app._saving and path.read_bytes() == original   # キャンセル：何も書かない

    app._on_save()
    _pump(app, lambda: not app._saving)
    assert asked == [path.name, path.name]
    assert path.read_bytes() != original and not _has_text(path)
    assert app.title() == f"{path.name} - VA FAX"
    assert app.preview_pane._raw_mode                           # 保存した結果を表示している
    assert app.status_var.get().startswith(f"保存しました：{path.name}（3ページ、")
    assert not hasattr(app, "log_text")                         # 処理ログの欄はない

    # 2回目の保存でも、確認を出す（2026-11-17 班長指示）。材料（元のページ）から作り直す
    first_saved = path.read_bytes()
    answers.append("replace")
    app._on_save()
    _pump(app, lambda: not app._saving)
    assert asked == [path.name, path.name, path.name]
    assert path.read_bytes() == first_saved
    assert not app.messages.errors


def test_first_overwrite_dialog_can_lead_to_save_as(app, monkeypatch):
    path = _open(app)
    original = path.read_bytes()
    other = app.work / "別名.pdf"
    monkeypatch.setattr(app, "_ask_overwrite", lambda _name: "save_as")
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **_k: str(other))
    app._on_save()
    _pump(app, lambda: not app._saving)
    assert path.read_bytes() == original and other.exists() and not _has_text(other)
    assert app.title() == "別名.pdf - VA FAX"


def test_open_copy_is_untitled_and_keeps_original(app, monkeypatch):
    path = _open(app, as_copy=True)
    original = path.read_bytes()
    assert app.title() == "＊sample01_a4_text_fax.pdf - VA FAX"
    asked = {}

    def fake_dialog(**kwargs):
        asked.update(kwargs)
        return str(app.work / kwargs["initialfile"])

    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", fake_dialog)
    app._on_save()                                              # 名前がないので、名前を尋ねる
    _pump(app, lambda: not app._saving)
    saved = app.work / "sample01_a4_text_fax.pdf"
    assert asked["initialfile"] == "sample01_a4_text_fax.pdf" and asked["initialdir"] == str(app.work)
    assert saved.exists() and not _has_text(saved)
    assert path.read_bytes() == original
    assert app.title() == "sample01_a4_text_fax.pdf - VA FAX"


def test_image_is_opened_as_untitled_document(app):
    image = app.work / "受領書.jpg"
    Image.new("RGB", (600, 800), "white").save(image)
    assert app._open_path(image, as_copy=False)
    _ready(app)
    assert app._save_path is None and app.title() == "＊受領書_fax.pdf - VA FAX"


def test_save_as_onto_added_file_is_refused(app, monkeypatch):
    _open(app)
    extra = _copy_sample(app, "sample02_b4_text.pdf")
    original = extra.read_bytes()
    app._add_paths([extra], at=None)
    _ready(app)
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **_k: str(extra))
    app._on_save_as()
    assert not app._saving and extra.read_bytes() == original
    assert "追加したファイルと同じ名前" in app.messages.errors[0]


def test_changing_options_marks_unsaved_and_returns_to_simulation(app, monkeypatch):
    path = _open(app)
    monkeypatch.setattr(app, "_ask_overwrite", lambda _name: "replace")
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
    monkeypatch.setattr(app, "_ask_overwrite", lambda _name: "replace")
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
    monkeypatch.setattr(app, "_ask_overwrite", lambda _name: "replace")
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


def test_nothing_mode_also_asks_before_overwriting(app, monkeypatch):
    """圧縮方式「何もしない」でも、上書き保存の前に確認を出す。"""
    path = _open(app)
    original = path.read_bytes()
    asked = []
    monkeypatch.setattr(app, "_ask_overwrite", lambda name: (asked.append(name), "replace")[1])
    app.mode_var.set("none")
    app._update_mode_widgets()
    app._on_save()
    _pump(app, lambda: not app._saving)
    assert asked == [path.name] and path.read_bytes() == original and _has_text(path)
    assert not app.messages.warnings and not app.messages.errors


def test_nothing_mode_asks_before_replacing_when_pages_were_edited(app, monkeypatch):
    path = _open(app)
    original = path.read_bytes()
    asked = []
    monkeypatch.setattr(app, "_ask_overwrite", lambda name: (asked.append(name), "cancel")[1])
    app.mode_var.set("none")
    app._update_mode_widgets()
    app._rotate_pages(180)
    _ready(app)
    app._on_save()
    assert asked == [path.name] and path.read_bytes() == original


def test_nothing_mode_warns_about_constraints_after_saving(app, monkeypatch):
    """圧縮方式「何もしない」では、制約（用紙サイズ等）に合わなくても止めず、保存の後に知らせる。"""
    path = _open(app, "sample03_a3_text.pdf")                   # A3（MOT/FAXの対応外）
    monkeypatch.setattr(app, "_ask_overwrite", lambda _name: "replace")
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
    monkeypatch.setattr(app, "_ask_overwrite", lambda _name: "replace")
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
    monkeypatch.setattr(app, "_ask_overwrite", lambda _name: "replace")
    app._on_save()
    _pump(app, lambda: not app._saving)
    app._close_document()
    assert app._open_path(path, as_copy=False)
    _ready(app)
    assert app.mode_var.get() == "none"
    other = _copy_sample(app, "sample02_b4_text.pdf")
    assert app._open_path(other, as_copy=False)
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


# ---------------------------------------------------------------- 名前を付けて保存の名前・オプション（F-32・F-35）

def _save_as_dialog_args(app, monkeypatch) -> dict:
    asked = {}
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename",
                        lambda **kwargs: (asked.update(kwargs), "")[1])   # 名前を尋ねるだけで、取りやめる
    app._on_save_as()
    return asked


def test_save_as_suggests_name_with_fax(app, monkeypatch):
    """開いたファイルの［名前を付けて保存］：名前の初めの値は「_fax」付き、場所は開いたファイルのフォルダ。"""
    path = _open(app)
    asked = _save_as_dialog_args(app, monkeypatch)
    assert asked["initialfile"] == "sample01_a4_text_fax.pdf"
    assert asked["initialdir"] == str(path.parent)
    assert app.title() == f"{path.name} - VA FAX"               # 保存先（題名）は、開いたファイルのまま


def test_save_as_after_saving_does_not_double_the_suffix(app, monkeypatch):
    path = _open(app)
    saved = app.work / "sample01_a4_text_fax.pdf"
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **_k: str(saved))
    app._on_save_as()
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


def test_options_has_three_tabs_and_no_settings_menu(app):
    """［設定］メニューはなく、FAXサービス・処理内容の既定値は、オプションの画面にまとめてある。"""
    menubar = app.nametowidget(app.cget("menu"))
    labels = [menubar.entrycget(i, "label") for i in range(menubar.index("end") + 1)
              if menubar.type(i) == "cascade"]
    assert "設定" not in labels and "ファイル" in labels and "ヘルプ" in labels
    menu = app._file_menu
    commands = [menu.entrycget(i, "label") for i in range(menu.index("end") + 1) if menu.type(i) == "command"]
    assert commands.index("オプション...") < commands.index("終了")


def test_option_turns_off_the_suffix_for_save_as_and_open_copy(app, monkeypatch):
    """［ファイル］→［オプション］で外すと、［名前を付けて保存］の名前の初めの値と、［コピーして開く］で
    開いた文書の名前（題名）の両方に、文字を付けない。設定ファイルに覚える。"""
    _open(app, as_copy=True)
    assert app.title() == "＊sample01_a4_text_fax.pdf - VA FAX"

    def answer(dialog):
        assert [dialog.notebook.tab(i, "text") for i in range(3)] == ["保存", "処理内容の既定値", "FAXサービス"]
        assert dialog._append_suffix_var.get() is True and dialog._suffix_var.get() == "_fax"
        dialog._append_suffix_var.set(False)
        dialog._update_suffix_state()
        assert str(dialog._suffix_entry.cget("state")) == "disabled"
        return True

    _run_options(app, monkeypatch, answer)
    assert app.settings.append_fax_to_save_name is False
    assert app.title() == "＊sample01_a4_text.pdf - VA FAX"
    assert _save_as_dialog_args(app, monkeypatch)["initialfile"] == "sample01_a4_text.pdf"
    assert load_settings().append_fax_to_save_name is False


def test_option_changes_the_suffix_text(app, monkeypatch):
    path = _open(app)
    _run_options(app, monkeypatch, lambda d: (d._suffix_var.set(" -送信用 "), True)[1])
    assert app.settings.save_name_suffix == "-送信用"
    assert _save_as_dialog_args(app, monkeypatch)["initialfile"] == f"{path.stem}-送信用.pdf"
    _open(app, "sample02_b4_text.pdf", as_copy=True)
    assert app.title() == "＊sample02_b4_text-送信用.pdf - VA FAX"
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
    assert app.mode_var.get() == "none" and not app.title().startswith("＊")


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
    monkeypatch.setattr(app, "_ask_overwrite", lambda _name: "replace")
    app._on_save()
    _pump(app, lambda: not app._saving)
    assert opened == []                                         # 初めは、開かない
    _run_options(app, monkeypatch, lambda d: (d._open_folder_var.set(True), True)[1])
    app._on_save()
    _pump(app, lambda: not app._saving)
    assert opened == [path.parent]
