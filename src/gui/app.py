# -*- coding: utf-8 -*-
"""VA FAX のtkinter GUI（F-15〜F-19・F-28・F-32・F-33）。

本モジュールはエンジン（core.document / core.diagnose / core.saving 等）を呼び出すのみで、
変換・保存のロジック自体は持たない（GUIとエンジンの分離）。

Ver200 で、通常のPDF編集アプリと同じ形にした：ファイルを開く・保存
（F-32。Ver210 で、保存は場所と名前を指定する［保存］の1つにし、［コピーして開く］はなくした）と、サムネイルでのページの編集（追加・削除・並べ替え・回転、
元に戻す・やり直す。F-33）。文書の中身は core.document.Document（ページの表）が持つ。
開いた・追加した・保存したファイルの名前と場所は、この画面がメモリ内にだけ持ち、
設定ファイル・ログ・診断情報には書かない（N-06・F-24・F-31）。
"""
from __future__ import annotations

import gc
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
import traceback
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Iterable, Optional

from tkinterdnd2 import DND_FILES, TkinterDnD

from config.profiles import PageSize, Profile, ProfileError, load_all_profiles, save_custom_profile
from config.settings import (NAME_SUFFIX_MAX_LENGTH, ConversionDefaults, Settings, save_settings,
                             valid_name_suffix)
from core import convert as convert_module
from core import cover_sheet as cover_module
from core import diagnose as diagnose_module
from core import document as document_module
from core import saving as saving_module
from core import size_control as size_control_module
from core.document import Document, Source, SourceError
from core.size_control import format_bytes_as_size as _format_size
import diagnostics
from gui import theme
from gui.cover_dialog import CoverSheetDialog, SenderFrame
from gui.icons import IconSet
from gui.preview import PreviewPane
from gui.ribbon import Ribbon
from logging_setup import setup_logger
from version import __version__

APP_NAME = "VA FAX"
# 新規作成した文書の、保存するまでの名前（題名。VA Document と同じ）
NEW_DOCUMENT_NAME = "新しい文書"
# 新規作成した文書に、ファイルのページがなく送付書だけがあるときの、保存の名前の元
COVER_ONLY_NAME = "送付書.pdf"
EMPTY_DOCUMENT_TEXT = ("ページがありません。［追加］か、ファイルのドラッグ＆ドロップでページを入れてください。"
                       "送付書は、「送付書」の組の［作成］で作れます。")
CONTACT_NOTICE = "問題が解決しない場合は、VBAアセット（vba-assets.net）のお問い合わせ窓口までご連絡ください。"

# 一度の操作（ドロップ・ダイアログ選択）でこの件数を超えて追加する場合は確認を挟む
# （フォルダ内の大量のファイルを誤ってまとめて落とした場合等の歯止め。件数のハード上限は
# 設けない。サービス制約値ではないUI上の設計値のためプロファイル〔F-29〕の対象外）。
CONFIRM_ADD_FILES_THRESHOLD = 10

# 開く・追加で選べるファイル（F-32・F-34）
_OPEN_FILETYPES = [
    ("PDF・画像（JPEG・PNG）", "*.pdf *.jpg *.jpeg *.png"),
    ("PDFファイル", "*.pdf"),
    ("画像（JPEG・PNG）", "*.jpg *.jpeg *.png"),
]
_PDF_FILETYPES = [("PDFファイル", "*.pdf")]

# 処理内容の選択肢の文字（メイン画面と、変換の既定設定の画面で同じものを使う。F-26）
_MODE_BINARIZE_TEXT = "二値化（白黒・G4圧縮）"
_MODE_GRAYSCALE_TEXT = "グレースケール（JPEG圧縮）"
_NOTHING_TEXT = "何もしない"
_CAPACITY_REDUCE_TEXT = "解像度・品質を下げて再実行"
_CAPACITY_SPLIT_TEXT = "分割して再実行"
_NORMALIZE_TEXT = "用紙正規化（用紙サイズ・方向が対応外のページを自動で修正します）"
_PAGE_NUMBERS_TEXT = "ページ番号を入れる（各ページの上端の中央に「1/12」の形で入れます）"
_NONE_MODE_NOTE = ("※ 圧縮方式が「何もしない」のときは、「解像度・品質を下げて再実行」と用紙正規化は効きません"
                   "（ページ番号は入れられます）。")

# 既定設定の圧縮方式（settings.json）→ 画面の圧縮方式
_POLICY_TO_MODE = {"always_binarize": "binarize", "always_grayscale": "grayscale", "none": "none"}

# ページの回転（F-33）：メニューの文字と、時計回りの角度
_ROTATIONS = [("左に90°回転", -90), ("右に90°回転", 90), ("180°回転", 180)]

_QUALITY_LABELS = ["高", "中", "低"]
_QUALITY_LEVEL_TO_INDEX = {"high": 0, "medium": 1, "low": 2}
_QUALITY_INDEX_TO_LEVEL = {v: k for k, v in _QUALITY_LEVEL_TO_INDEX.items()}
_DITHER_LABELS = {"auto": "自動", "threshold": "単純閾値", "diffusion": "誤差拡散"}
_DITHER_LABELS_REV = {v: k for k, v in _DITHER_LABELS.items()}
_REDUCTION_STRATEGY_LABELS = {"culprit_only": "原因ページのみ", "uniform": "全体を均等に"}
_REDUCTION_STRATEGY_LABELS_REV = {v: k for k, v in _REDUCTION_STRATEGY_LABELS.items()}

# カスタムプロファイル（F-27）の対応用紙サイズ選択肢。他の同梱プロファイルと同じ寸法を用いる。
_CUSTOM_PAGE_SIZE_CHOICES = (
    PageSize(name="A4", width_mm=210.0, height_mm=297.0),
    PageSize(name="B4", width_mm=257.0, height_mm=364.0),
    PageSize(name="A3", width_mm=297.0, height_mm=420.0),
    PageSize(name="A5", width_mm=148.0, height_mm=210.0),
    PageSize(name="レター", width_mm=215.9, height_mm=279.4),
)

_DND_PATH_PATTERN = re.compile(r"\{([^}]*)\}|(\S+)")


@dataclass(frozen=True)
class CoverRecord:
    """文書に入れた送付書（F-36）。refsは、送付書のページ（（材料の番号, ページ）の組）。

    totalは、送付書に書いた送信枚数（分からないときはNone。保存の前に、文書のページ数と比べる）。
    """

    refs: frozenset
    input: cover_module.CoverInput
    total: Optional[int]


def _parse_dropped_paths(data: str) -> list[str]:
    """ドロップされたパス一覧を解析する。

    tkinterdnd2の慣例であるtk.splitlist()はTclの文字列としてバックスラッシュを
    エスケープ解釈するため、Windowsのパス（円記号区切り）を壊してしまう。
    そのため波括弧（空白を含むパス）と空白区切りのみで単純に分割する。
    """
    return [braced if braced else bare for braced, bare in _DND_PATH_PATTERN.findall(data)]


def window_title(name: Optional[str], dirty: bool) -> str:
    """ウィンドウの題名（F-15）。文書を開いていないときはツールの名前だけ、開いているときは
    「（未保存の印）ファイルの名前 - VA FAX」。未保存の印は全角の「＊」、区切りは半角のハイフンと
    その前後の半角の空白（VAプロジェクト共通の題名の書き方）。
    """
    if not name:
        return APP_NAME
    return f"{'＊' if dirty else ''}{name} - {APP_NAME}"


def fit_geometry_to_screen(geometry: Optional[str], screen_width: int, screen_height: int) -> Optional[str]:
    """保存してあったウィンドウの大きさ・位置（幅x高さ±左±上）を、いまの画面で使える形にする（F-23）。

    位置が画面の外になるとき（外付けの画面を外した等）は、大きさだけを返す（位置はOSに任せる）。
    大きさが画面より大きいときは、画面に収める。使えない文字列はNone。
    """
    match = re.match(r"^(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)$", geometry or "")
    if not match:
        return None
    width, height = min(int(match.group(1)), screen_width), min(int(match.group(2)), screen_height)
    x_text, y_text = match.group(3), match.group(4)
    if x_text.startswith("-") or y_text.startswith("-"):
        return f"{width}x{height}"   # 右端・下端からの指定や負の位置は、位置を使わない
    x, y = int(x_text), int(y_text)
    # 題名の帯をつかめるだけの幅が、画面の中に残っていること
    if x > screen_width - 120 or y > screen_height - 60:
        return f"{width}x{height}"
    return f"{width}x{height}+{x}+{y}"


def suggested_save_name(base: Path, suffix: Optional[str]) -> str:
    """［保存］の画面の名前の初めの値と、画像を開いた文書の名前（F-32）。base は、開いたファイル
    （保存した後は、保存したファイル）。suffix（初期値は「_fax」。［ファイル］→［オプション］で変える・
    付けないにできる。F-35）を付ける。既に suffix で終わる名前（VA FAX で保存したファイルを開き直した
    とき等）には、重ねて付けない。拡張子は .pdf にする（画像を開いたとき等）。
    """
    stem = base.stem
    if suffix and not stem.lower().endswith(suffix.lower()):
        stem += suffix
    return f"{stem}.pdf"


def _profile_constraint_text(profile: Profile) -> str:
    size_text = _format_size(profile.max_file_size_bytes)
    names = "/".join(dict.fromkeys(s.name for s in profile.supported_page_sizes))
    parts = [f"上限{size_text}", names]
    if profile.page_count_limit is not None:
        parts.append(f"{profile.page_count_limit}ページ以内")
    return f"{profile.display_name}（{'・'.join(parts)}）"


def _profile_summary(profile: Profile) -> str:
    return f"対象サービス：{_profile_constraint_text(profile)}"


def _format_exception_for_log(exc: BaseException) -> str:
    """例外をログ安全な形式（型名とコード上の発生位置のみ）に整形する（F-24）。

    traceback.format_exc()は例外メッセージ本文を含むが、ファイル読み書きの
    OSError等はメッセージにフルパス（＝文書のファイル名）を含みうるため、
    メッセージ本文は記録せず、型名とスタックのファイル名:行番号:関数名のみを記録する。
    """
    frames = traceback.extract_tb(exc.__traceback__)
    location = " -> ".join(f"{Path(f.filename).name}:{f.lineno}:{f.name}" for f in frames)
    return f"{type(exc).__name__}（{location}）"


def _open_folder(folder: Path) -> None:
    """フォルダをOSのファイル管理画面（エクスプローラー・Finder等）で開く。

    Windowsはos.startfile（従来どおり）。os.startfileはWindowsにしかないため、
    Mac（ソースから動かす場合。N-03）はopen、その他はxdg-openを使う（いずれも
    手元のフォルダを開くだけで、外部と通信しない）。開けない場合はOSErrorを送出する。
    """
    if sys.platform == "win32":
        os.startfile(folder)  # noqa: S606 (Windows専用)
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, str(folder)])  # noqa: S603


def _collect_closed_window() -> None:
    """閉じた画面の部品（Tkの変数等）を、ここ（画面のスレッド）で片づける。

    部品を属性に持つ画面は、閉じた後も循環参照のごみとして残る（部品の master をたどると画面に
    戻るため）。残したままにすると、裏のスレッド（事前診断・保存・プレビューの描画）でガベージ
    コレクションにかかり、Tclが「別のスレッドからの解放」として異常終了することがある
    （Windows fatal exception: code 0x80000003。main.py の初回の選択画面と同じ対策。2026-11-18 に
    送付書の入力の画面で見つけ、同じ形の［オプション］の画面にも入れた）。
    """
    gc.collect()


def _bring_to_front(window: tk.Misc) -> None:
    """新規ウィンドウがタスクバーで点滅するだけで前面に出てこない場合があるため、明示的に前面化する。"""
    window.lift()
    window.attributes("-topmost", True)

    def _clear_topmost() -> None:
        # 200ms以内にウィンドウが閉じられている場合がある（すぐ閉じるダイアログ等）ため確認する。
        if window.winfo_exists():
            window.attributes("-topmost", False)

    window.after(200, _clear_topmost)
    window.focus_force()


class ProfileChoiceFrame(ttk.Frame):
    """FAXサービス（プロファイル）を選ぶ欄（F-28）。初回の選択画面と、オプションの画面で使う。"""

    def __init__(self, master: tk.Misc, current_profile_id: Optional[str] = None):
        super().__init__(master)
        self.profiles = load_all_profiles()
        ordered_ids = ["motfax", "movfax", "messageplus", "sokusoku_fax", "efax", "jfax", "custom"]
        self._ids = [pid for pid in ordered_ids if pid in self.profiles] + [
            pid for pid in self.profiles if pid not in ordered_ids
        ]

        ttk.Label(self, text="お使いのインターネットFAXサービスを選択してください。",
                  anchor="w", justify="left").pack(fill="x", pady=(0, 6))

        # current_profile_idが不正（存在しないid）な場合、どのラジオボタンにも一致せず
        # 未選択のまま「決定」で無効なidが返ってしまうため、先頭を既定選択にフォールバックする。
        initial_id = current_profile_id if current_profile_id in self.profiles else self._ids[0]
        self.var = tk.StringVar(value=initial_id)
        for pid in self._ids:
            profile = self.profiles[pid]
            frame = ttk.Frame(self)
            frame.pack(fill="x", pady=3, anchor="w")
            ttk.Radiobutton(frame, text=_profile_constraint_text(profile), variable=self.var,
                            value=pid).pack(anchor="w")
            if profile.reference_notice:
                ttk.Label(frame, text=profile.reference_notice, foreground=theme.COLOR_WARNING,
                          anchor="w", justify="left", wraplength=460).pack(anchor="w", padx=(20, 0))

    @property
    def selected_id(self) -> str:
        return self.var.get()


class ProfileChoiceDialog(tk.Toplevel):
    """初回起動時のFAXサービス（プロファイル）選択ダイアログ（F-28）。"""

    def __init__(self, master: Optional[tk.Misc], current_profile_id: Optional[str] = None):
        super().__init__(master)
        self.title("お使いのFAXサービスを選んでください")
        self.resizable(False, False)
        self.result_profile_id: Optional[str] = None

        self._frame = ProfileChoiceFrame(self, current_profile_id)
        self._frame.pack(fill="both", expand=True, padx=12, pady=(12, 0))
        self.profiles = self._frame.profiles
        self._var = self._frame.var

        button_row = ttk.Frame(self)
        button_row.pack(fill="x", padx=12, pady=12)
        ok_button = theme.create_button(
            button_row, accent=True, text="決定", width=10, command=self._on_ok)
        ok_button.pack(side="right")

        self.transient(master)
        theme.place_over(self, master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        _bring_to_front(self)

    def _on_ok(self) -> None:
        selected_id = self._var.get()
        if selected_id == "custom":
            # カスタム選択時は、容量上限・対応用紙等を入力する画面を挟む（F-27）。
            # キャンセルされた場合はプロファイルの選択自体を確定しない。
            editor = CustomProfileDialog(self, self.profiles["custom"])
            self.wait_window(editor)
            if editor.result is None:
                return
            save_custom_profile(editor.result)
            self.profiles = load_all_profiles()
        self.result_profile_id = selected_id
        self.destroy()

    def _on_cancel(self) -> None:
        self.result_profile_id = None
        self.destroy()


class CustomProfileDialog(tk.Toplevel):
    """カスタムプロファイルの入力フォーム（F-27）。

    利用者が自身の契約サービスの公表仕様（容量上限・対応用紙等）を入力する。
    解像度・JPEG品質の段階値・正規化先サイズ（A4）は他プロファイルと共通の
    既定値を用い、編集対象としない（複雑さを抑えるため）。
    """

    def __init__(self, master: tk.Misc, current: Profile):
        super().__init__(master)
        self.title("カスタムプロファイルの設定")
        self.resizable(False, False)
        self.result: Optional[Profile] = None
        self._current = current

        ttk.Label(self, text="お使いのサービスの公表仕様をご確認のうえ、各項目を入力してください。",
                  foreground=theme.COLOR_WARNING, anchor="w", justify="left", wraplength=440).pack(
            fill="x", padx=12, pady=(12, 6))

        form = ttk.Frame(self)
        form.pack(fill="x", padx=12)

        self._name_var = tk.StringVar(value=current.display_name)
        ttk.Label(form, text="サービス名：").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=self._name_var, width=30).grid(
            row=0, column=1, sticky="w", pady=3)

        mb_text = f"{current.max_file_size_bytes / (1024 * 1024):.2f}".rstrip("0").rstrip(".")
        self._max_size_mb_var = tk.StringVar(value=mb_text)
        ttk.Label(form, text="最大ファイルサイズ（MB）：").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=self._max_size_mb_var, width=10).grid(
            row=1, column=1, sticky="w", pady=3)

        self._page_limit_var = tk.StringVar(
            value=str(current.page_count_limit) if current.page_count_limit else "")
        ttk.Label(form, text="ページ数上限（空欄は上限なし）：").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=self._page_limit_var, width=10).grid(
            row=2, column=1, sticky="w", pady=3)

        ttk.Label(form, text="対応用紙サイズ：").grid(row=3, column=0, sticky="nw", pady=3)
        sizes_frame = ttk.Frame(form)
        sizes_frame.grid(row=3, column=1, sticky="w", pady=3)
        current_names = {s.name for s in current.supported_page_sizes}
        self._size_vars: dict[str, tk.BooleanVar] = {}
        for size in _CUSTOM_PAGE_SIZE_CHOICES:
            var = tk.BooleanVar(value=size.name in current_names)
            self._size_vars[size.name] = var
            tk.Checkbutton(sizes_frame, text=size.name, variable=var).pack(side="left")

        self._landscape_var = tk.BooleanVar(value=current.landscape_accepted)
        tk.Checkbutton(form, text="横向きページを受け付ける",
                        variable=self._landscape_var).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=3)

        self._color_note_var = tk.StringVar(value=current.color_capability_note)
        ttk.Label(form, text="カラー対応等の備考：").grid(row=5, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=self._color_note_var, width=40).grid(
            row=5, column=1, sticky="w", pady=3)

        button_row = ttk.Frame(self)
        button_row.pack(fill="x", padx=12, pady=12)
        save_button = theme.create_button(
            button_row, accent=True, text="保存", width=10, command=self._on_save)
        save_button.pack(side="right")
        theme.create_button(
            button_row, text="キャンセル", width=10, command=self.destroy).pack(
            side="right", padx=(0, 6))

        self.transient(master)
        theme.place_over(self, master)
        self.grab_set()
        _bring_to_front(self)

    def _on_save(self) -> None:
        name = self._name_var.get().strip()
        if not name:
            messagebox.showerror("VA FAX", "サービス名を入力してください。", parent=self)
            return

        try:
            max_size_mb = float(self._max_size_mb_var.get())
            if max_size_mb <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("VA FAX", "最大ファイルサイズは正の数値で入力してください。",
                                  parent=self)
            return

        page_limit_text = self._page_limit_var.get().strip()
        page_limit: Optional[int] = None
        if page_limit_text:
            try:
                page_limit = int(page_limit_text)
                if page_limit <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror(
                    "VA FAX", "ページ数上限は正の整数で入力するか、空欄にしてください。", parent=self)
                return

        selected_sizes = tuple(
            size for size in _CUSTOM_PAGE_SIZE_CHOICES if self._size_vars[size.name].get()
        )
        if not selected_sizes:
            messagebox.showerror("VA FAX", "対応用紙サイズを1つ以上選択してください。", parent=self)
            return

        self.result = replace(
            self._current,
            display_name=name,
            max_file_size_bytes=round(max_size_mb * 1024 * 1024),
            page_count_limit=page_limit,
            supported_page_sizes=selected_sizes,
            landscape_accepted=self._landscape_var.get(),
            color_capability_note=self._color_note_var.get().strip(),
        )
        self.destroy()


class DefaultsFrame(ttk.Frame):
    """処理内容の既定値の欄（F-26。オプションの画面の1つのタブ）。result()でConversionDefaultsを返す。"""

    def __init__(self, master: tk.Misc, current: ConversionDefaults):
        super().__init__(master)
        self._compression_var = tk.StringVar(value=current.compression_policy)
        self._normalize_var = tk.BooleanVar(value=current.normalize_enabled)
        self._page_numbers_var = tk.BooleanVar(value=current.page_numbers)
        self._dither_label_var = tk.StringVar(
            value=_DITHER_LABELS.get(current.dither_method, _DITHER_LABELS["auto"]))
        self._quality_label_var = tk.StringVar(
            value=_QUALITY_LABELS[_QUALITY_LEVEL_TO_INDEX.get(current.jpeg_quality_level, 1)])
        self._capacity_method_var = tk.StringVar(value=current.capacity_exceeded_method)
        self._reduction_strategy_var = tk.StringVar(
            value=_REDUCTION_STRATEGY_LABELS.get(
                current.size_reduction_strategy, _REDUCTION_STRATEGY_LABELS["culprit_only"]))

        ttk.Label(self, text="ファイルを開いたときの、処理内容の初めの値です。",
                  foreground=theme.COLOR_MUTED).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        opt = ttk.Frame(self)
        opt.grid(row=1, column=0, columnspan=3, sticky="we")

        # 選択肢の数が少ない項目から順に並べる：圧縮方式→容量超過→用紙正規化。
        # 配置・文言はメイン画面の処理内容選択欄（_build_options）と同一にする。ただし
        # 各選択肢の副作用を説明する「※」注記は、既定値を機械的に設定するだけの本画面
        # では判断材料として不要なため省く。出力先・出力ファイル名・変換レポートの項目は、
        # Ver200 でなくした（F-02・旧F-25）。
        ttk.Label(opt, text="圧縮方式：").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(4, 0))

        ttk.Radiobutton(opt, text=_MODE_BINARIZE_TEXT, variable=self._compression_var,
                        value="always_binarize", command=self._update_compression_states).grid(
            row=1, column=0, sticky="w", padx=8)

        ttk.Label(opt, text="　ディザ方式：").grid(row=2, column=0, sticky="e", padx=(24, 0))
        self._dither_combo = ttk.Combobox(
            opt, textvariable=self._dither_label_var, state="readonly", width=10,
            values=list(_DITHER_LABELS.values()))
        self._dither_combo.grid(row=2, column=1, sticky="w")

        ttk.Radiobutton(opt, text=_MODE_GRAYSCALE_TEXT, variable=self._compression_var,
                        value="always_grayscale", command=self._update_compression_states).grid(
            row=3, column=0, sticky="w", padx=8)

        ttk.Label(opt, text="　JPEG品質：").grid(row=4, column=0, sticky="e", padx=(24, 0))
        self._quality_combo = ttk.Combobox(
            opt, textvariable=self._quality_label_var, state="readonly", width=10,
            values=_QUALITY_LABELS)
        self._quality_combo.grid(row=4, column=1, sticky="w")

        ttk.Radiobutton(opt, text=_NOTHING_TEXT, variable=self._compression_var,
                        value="none", command=self._update_compression_states).grid(
            row=5, column=0, sticky="w", padx=8)

        ttk.Label(opt, text="容量超過時の処理方式：").grid(
            row=6, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 0))

        self._reduce_resolution_radio = ttk.Radiobutton(
            opt, text=_CAPACITY_REDUCE_TEXT, variable=self._capacity_method_var,
            value="reduce_resolution", command=self._update_strategy_combo_state)
        self._reduce_resolution_radio.grid(row=7, column=0, sticky="w", padx=8)

        ttk.Label(opt, text="　削減方式：").grid(row=8, column=0, sticky="e", padx=(24, 0))
        self._strategy_combo = ttk.Combobox(
            opt, textvariable=self._reduction_strategy_var, state="readonly", width=12,
            values=list(_REDUCTION_STRATEGY_LABELS.values()))
        self._strategy_combo.grid(row=8, column=1, sticky="w")

        self._split_radio = ttk.Radiobutton(
            opt, text=_CAPACITY_SPLIT_TEXT, variable=self._capacity_method_var,
            value="split", command=self._update_strategy_combo_state)
        self._split_radio.grid(row=9, column=0, sticky="w", padx=8)

        ttk.Radiobutton(
            opt, text=_NOTHING_TEXT, variable=self._capacity_method_var,
            value="none", command=self._update_strategy_combo_state).grid(
            row=10, column=0, sticky="w", padx=8)

        self._normalize_check = tk.Checkbutton(opt, text=_NORMALIZE_TEXT, variable=self._normalize_var)
        self._normalize_check.grid(row=11, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 0))
        self._page_numbers_check = tk.Checkbutton(opt, text=_PAGE_NUMBERS_TEXT, variable=self._page_numbers_var)
        self._page_numbers_check.grid(row=12, column=0, columnspan=3, sticky="w", padx=8)

        self._none_note_var = tk.StringVar()
        ttk.Label(opt, textvariable=self._none_note_var, foreground=theme.COLOR_MUTED,
                  justify="left").grid(row=13, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 4))

        self._update_compression_states()

    def _update_compression_states(self) -> None:
        """ディザ方式・JPEG品質は、対応する圧縮方式を選択している場合のみ選択可能とする。
        「何もしない」のときに効かない項目（解像度・品質を下げて再実行、用紙正規化）は、
        選べない状態にする（メイン画面と同じ。F-11）。"""
        policy = self._compression_var.get()
        nothing = policy == "none"
        self._dither_combo.configure(state="readonly" if policy == "always_binarize" else "disabled")
        self._quality_combo.configure(state="readonly" if policy == "always_grayscale" else "disabled")
        if nothing and self._capacity_method_var.get() == "reduce_resolution":
            self._capacity_method_var.set("none")
        self._reduce_resolution_radio.configure(state="disabled" if nothing else "normal")
        self._normalize_check.configure(state="disabled" if nothing else "normal")
        self._none_note_var.set(_NONE_MODE_NOTE if nothing else "")
        self._update_strategy_combo_state()

    def _update_strategy_combo_state(self) -> None:
        """削減方式は、「解像度を下げて再実行」を選択している場合のみ選択可能とする。"""
        enabled = (self._capacity_method_var.get() == "reduce_resolution"
                   and self._compression_var.get() != "none")
        self._strategy_combo.configure(state="readonly" if enabled else "disabled")

    def result(self) -> ConversionDefaults:
        return ConversionDefaults(
            compression_policy=self._compression_var.get(),
            normalize_enabled=self._normalize_var.get(),
            dither_method=_DITHER_LABELS_REV[self._dither_label_var.get()],
            jpeg_quality_level=_QUALITY_INDEX_TO_LEVEL[
                _QUALITY_LABELS.index(self._quality_label_var.get())],
            capacity_exceeded_method=self._capacity_method_var.get(),
            size_reduction_strategy=_REDUCTION_STRATEGY_LABELS_REV[self._reduction_strategy_var.get()],
            page_numbers=bool(self._page_numbers_var.get()),
        )


class OptionsDialog(tk.Toplevel):
    """［ファイル］→［オプション］の画面（F-35）。

    タブは「保存」「処理内容の既定値」（F-26）「FAXサービス」（F-28）「送付書」（F-36。Ver210）。Ver200 で、［設定］メニューの
    「FAXサービスを変更」「変換の既定設定」をここにまとめた（2026-11-17 班長指示）。保存時は result に
    選んだ値の辞書を設定する（設定ファイルへの書き込みは、画面の側が行う）。
    """

    def __init__(self, master: tk.Misc, settings: Settings, current_profile_id: str):
        super().__init__(master)
        self.title("オプション")
        self.resizable(False, False)
        self.result: Optional[dict] = None
        self._settings = settings
        self._initial_profile_id = current_profile_id
        self._pending_custom: Optional[Profile] = None   # カスタムの内容（保存するまで書かない）

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=(12, 0))

        # ---- 保存
        save_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(save_tab, text="保存")
        self.save_tab = save_tab
        self._append_suffix_var = tk.BooleanVar(value=settings.append_fax_to_save_name)
        self._suffix_var = tk.StringVar(value=settings.save_name_suffix)
        self._open_folder_var = tk.BooleanVar(value=settings.open_folder_after_save)

        tk.Checkbutton(
            save_tab, text="［保存］の画面に初めに入るファイル名に、次の文字を付ける",
            variable=self._append_suffix_var, command=self._update_suffix_state,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(save_tab, text="付ける文字：").grid(row=1, column=0, sticky="w", padx=(24, 4), pady=(2, 0))
        self._suffix_entry = ttk.Entry(save_tab, textvariable=self._suffix_var, width=16)
        self._suffix_entry.grid(row=1, column=1, sticky="w", pady=(2, 0))
        self._suffix_example_var = tk.StringVar()
        ttk.Label(save_tab, textvariable=self._suffix_example_var, foreground=theme.COLOR_MUTED,
                  justify="left").grid(row=2, column=0, columnspan=2, sticky="w", padx=(24, 0), pady=(2, 10))
        self._suffix_var.trace_add("write", lambda *_a: self._update_suffix_state())

        tk.Checkbutton(
            save_tab, text="保存の後に、保存したフォルダを開く", variable=self._open_folder_var,
        ).grid(row=3, column=0, columnspan=2, sticky="w")
        ttk.Label(save_tab, text="FAXサービスの画面へアップロードするときに、ファイルを探す手間が減ります。",
                  foreground=theme.COLOR_MUTED).grid(row=4, column=0, columnspan=2, sticky="w", padx=(24, 0))

        # ---- 処理内容の既定値
        self.defaults_frame = DefaultsFrame(self.notebook, settings.defaults)
        self.defaults_frame.configure(padding=10)
        self.notebook.add(self.defaults_frame, text="処理内容の既定値")

        # ---- FAXサービス
        profile_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(profile_tab, text="FAXサービス")
        self.profile_frame = ProfileChoiceFrame(profile_tab, current_profile_id)
        self.profile_frame.pack(fill="x")
        self._custom_button = theme.create_button(
            profile_tab, text="カスタムの内容を入力...", command=self._edit_custom)
        self._custom_button.pack(anchor="w", pady=(8, 0))
        self.profile_frame.var.trace_add("write", lambda *_a: self._update_custom_button())

        # ---- 送付書（送信者。F-36。Ver210）
        self.sender_frame = SenderFrame(self.notebook, settings.cover_sender)
        self.sender_frame.configure(padding=10)
        self.notebook.add(self.sender_frame, text="送付書")

        button_row = ttk.Frame(self)
        button_row.pack(fill="x", padx=12, pady=12)
        theme.create_button(
            button_row, accent=True, text="保存", width=10, command=self._on_save).pack(side="right")
        theme.create_button(
            button_row, text="キャンセル", width=10, command=self.destroy).pack(side="right", padx=(0, 6))

        self._update_suffix_state()
        self._update_custom_button()
        self.transient(master)
        theme.place_over(self, master)
        self.grab_set()
        self.bind("<Escape>", lambda _e: self.destroy())
        _bring_to_front(self)

    def _update_suffix_state(self) -> None:
        on = bool(self._append_suffix_var.get())
        self._suffix_entry.configure(state="normal" if on else "disabled")
        suffix = valid_name_suffix(self._suffix_var.get())
        if not on:
            self._suffix_example_var.set("例：契約書.pdf → 契約書.pdf（文字を付けません）")
        elif suffix is None:
            self._suffix_example_var.set(
                f"付ける文字を入力してください（{NAME_SUFFIX_MAX_LENGTH}文字まで。"
                '\\ / : * ? " < > | は使えません）')
        else:
            self._suffix_example_var.set(
                f"例：契約書.pdf → 契約書{suffix}.pdf（保存の画面に初めに入る名前です。画面で変えることもできます）")

    def _update_custom_button(self) -> None:
        is_custom = self.profile_frame.selected_id == "custom"
        self._custom_button.configure(state="normal" if is_custom else "disabled")

    def _edit_custom(self) -> bool:
        """カスタムの内容を入力する画面を開く。入力したらTrue（保存するまでは書かない）。"""
        current = self._pending_custom or self.profile_frame.profiles["custom"]
        editor = CustomProfileDialog(self, current)
        self.wait_window(editor)
        if editor.result is None:
            return False
        self._pending_custom = editor.result
        return True

    def _on_save(self) -> None:
        append = bool(self._append_suffix_var.get())
        suffix = valid_name_suffix(self._suffix_var.get())
        if append and suffix is None:
            self.notebook.select(self.save_tab)
            messagebox.showerror(
                APP_NAME,
                f"ファイル名に付ける文字を入力してください（{NAME_SUFFIX_MAX_LENGTH}文字まで。"
                '\\ / : * ? " < > | は使えません）。', parent=self)
            return
        profile_id = self.profile_frame.selected_id
        if (profile_id == "custom" and profile_id != self._initial_profile_id
                and self._pending_custom is None and not self._edit_custom()):
            # ほかのサービスからカスタムに替えるときは、内容の入力を挟む（初回の選択画面と同じ。F-27）
            return
        self.result = {
            "profile_id": profile_id,
            "custom_profile": self._pending_custom,
            "defaults": self.defaults_frame.result(),
            "append_suffix": append,
            "suffix": suffix or self._settings.save_name_suffix,
            "open_folder_after_save": bool(self._open_folder_var.get()),
            "cover_sender": self.sender_frame.result(),
        }
        self.destroy()


class ChoiceDialog(tk.Toplevel):
    """文と、いくつかのボタンを出し、押されたボタンの値を返す画面（保存の確認など）。

    buttonsは (ボタンの文字, 返す値, 主要な操作か) の並び（左から）。閉じるボタン・Escapeでは
    cancel_valueを返す。
    """

    def __init__(self, master: tk.Misc, title: str, message: str,
                 buttons: list[tuple[str, str, bool]], cancel_value: Optional[str] = None):
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.result: Optional[str] = cancel_value
        ttk.Label(self, text=message, anchor="w", justify="left", wraplength=480).pack(
            fill="x", padx=16, pady=(16, 12))
        row = ttk.Frame(self)
        row.pack(fill="x", padx=16, pady=(0, 14))
        for label, value, accent in reversed(buttons):
            theme.create_button(row, accent=accent, text=label,
                                command=lambda v=value: self._choose(v)).pack(side="right", padx=(8, 0))
        self.transient(master)
        theme.place_over(self, master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _e: self.destroy())
        _bring_to_front(self)

    def _choose(self, value: str) -> None:
        self.result = value
        self.destroy()


def ask_choice(master: tk.Misc, title: str, message: str, buttons: list[tuple[str, str, bool]],
               cancel_value: Optional[str] = None) -> Optional[str]:
    dialog = ChoiceDialog(master, title, message, buttons, cancel_value)
    master.wait_window(dialog)
    return dialog.result


class VaFaxApp(TkinterDnD.Tk):
    """VA FAX メインウィンドウ。"""

    def __init__(self, settings: Settings, profile: Profile):
        super().__init__()
        theme.apply_flat_theme(self)
        theme.apply_app_icon(self)
        self.settings = settings
        self.profile = profile
        self.logger = setup_logger()

        # 文書の状態（F-01・F-32）。ファイルの名前と場所は、ここ（メモリ内）にだけ持つ。
        self.document: Optional[Document] = None
        self._save_path: Optional[Path] = None      # 保存先（Noneは、名前のない文書）
        self._name_base: Optional[Path] = None      # 名前のない文書の、名前の元にするファイル（F-32）
        self._doc_dir: Optional[Path] = None        # 保存の画面の、初めの場所
        self._material_paths: list[Path] = []       # 材料にしたファイル（開いた・追加した）
        self._added_paths: list[Path] = []          # そのうち、追加したファイル（保存先にできない）
        # 文書の材料ごとのファイル（材料の番号の順。送付書の材料はNone。F-36の書類の一覧に使う）
        self._source_paths: list[Optional[Path]] = []
        self._covers: list[CoverRecord] = []        # 文書に入れた送付書（F-36）
        # 保存している文書の送付書（入力, 送付書のページ数, 総ページ数）。保存の後に入力を書く（F-36）
        self._saving_cover: Optional[tuple[cover_module.CoverInput, int, int]] = None
        # 開いてから（又は最後に保存してから）、まだ保存していないか。題名の「＊」・［保存］の青枠・
        # 閉じるときの確認は、これと「保存すると違うものができるか」の両方で決める（_has_unsaved_work）
        self._dirty = False
        self._assembled: Optional[bytes] = None     # いまのページの表を組み立てたPDF（診断済みのもの）
        self._pending_selection: tuple[list[int], Optional[int]] = ([], None)
        # 圧縮方式を「何もしない」にしたとき、容量超過時の「解像度・品質を下げて再実行」から
        # 「何もしない」へ切り替えたか（圧縮方式を戻したときに、元に戻すため）
        self._capacity_switched_for_none = False
        self._normal_geometry: Optional[str] = None   # 最大化していないときの大きさ・位置（F-23）
        self._applying_defaults = False

        self.diagnosis: Optional[diagnose_module.Diagnosis] = None
        self._message_queue: "queue.Queue[dict]" = queue.Queue()
        # 起きた誤りの記録（型と場所だけ。［ヘルプ］→［診断情報をコピー］に載せる）
        self._errors = diagnostics.ErrorLog()
        self._saving = False
        self._diagnosis_token = 0

        self.title(APP_NAME)
        self.resizable(True, True)
        self.minsize(900, 700)
        self.configure(bg=theme.COLOR_BG)
        self._restore_window_state()

        self._build_variables()
        self._build_menu()
        self._build_widgets()
        self._bind_preview_option_traces()
        self._bind_keys()
        self.protocol("WM_DELETE_WINDOW", self._on_quit)
        if theme.is_aqua(self):
            self._bind_mac_keys()
        self._update_command_states()
        self._poll_queue()
        _bring_to_front(self)

    def report_callback_exception(self, exc_type, exc, tb) -> None:
        """画面の処理（ボタン・キー等）で起きた予期しない誤りを覚える（Tkの既定どおりログにも出す）。"""
        self._errors.record(exc, "画面の処理")
        self.logger.error("画面の処理で予期しないエラーが発生しました：%s",
                          _format_exception_for_log(exc))
        super().report_callback_exception(exc_type, exc, tb)

    # ------------------------------------------------------------------ 初期化

    def _build_variables(self) -> None:
        d = self.settings.defaults
        self.profile_label_var = tk.StringVar(value=_profile_summary(self.profile))
        self.normalize_var = tk.BooleanVar(value=d.normalize_enabled)
        self.page_numbers_var = tk.BooleanVar(value=d.page_numbers)
        self.mode_var = tk.StringVar(value=_POLICY_TO_MODE.get(d.compression_policy, "binarize"))
        self.dither_label_var = tk.StringVar(
            value=_DITHER_LABELS.get(d.dither_method, _DITHER_LABELS["auto"]))
        self.quality_label_var = tk.StringVar(
            value=_QUALITY_LABELS[_QUALITY_LEVEL_TO_INDEX.get(d.jpeg_quality_level, 1)])
        self.capacity_method_var = tk.StringVar(value=d.capacity_exceeded_method)
        self.reduction_strategy_label_var = tk.StringVar(
            value=_REDUCTION_STRATEGY_LABELS.get(
                d.size_reduction_strategy, _REDUCTION_STRATEGY_LABELS["culprit_only"]))
        self.progress_var = tk.DoubleVar(value=0.0)
        self.status_var = tk.StringVar(value="")

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)

        # ［ファイル］（F-32。リボンの「ファイル」の組と同じ操作）
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="新規作成", accelerator="Ctrl+N", command=self._on_new)
        file_menu.add_command(label="開く...", accelerator="Ctrl+O", command=self._on_open)
        file_menu.add_separator()
        # 保存は［保存...］の1つ（保存の画面で場所と名前を指定する。上書きは、同じ名前を選ぶと
        # Windows が確かめる。2026-11-18 班長指示）
        file_menu.add_command(label="保存...", accelerator="Ctrl+S", command=self._on_save)
        file_menu.add_separator()
        file_menu.add_command(label="閉じる", command=self._on_close_document)
        file_menu.add_separator()
        file_menu.add_command(label="オプション...", command=self._on_options)
        file_menu.add_separator()
        file_menu.add_command(label="終了", command=self._on_quit)
        menubar.add_cascade(label="ファイル", menu=file_menu)

        # Ver130 までの［設定］メニュー（FAXサービスを変更・変換の既定設定）は、［ファイル］→
        # ［オプション］にまとめた（2026-11-17 班長指示。F-35）。

        # ［ヘルプ］→［診断情報をコピー］（2026-11-10 班長決定：Pythonのツールに、
        # 次の版上げのときに入れる）。不具合をAI・作成者に相談するときに渡す文を作る。
        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="診断情報をコピー", command=self._copy_diagnostics)
        help_menu.add_separator()
        help_menu.add_command(label="VA FAX について", command=self._show_about)
        menubar.add_cascade(label="ヘルプ", menu=help_menu)

        self.config(menu=menubar)
        self._file_menu = file_menu

    def _show_about(self) -> None:
        messagebox.showinfo(
            "VA FAX",
            f"VA FAX  {__version__}\n"
            "インターネットFAX送信用PDF変換ツール\n\n"
            "VBAアセット（vba-assets.net）\nMIT License",
            parent=self,
        )

    # ------------------------------------------------------------------ 診断情報

    def _copy_diagnostics(self) -> None:
        """診断情報をクリップボードへコピーする（［ヘルプ］→［診断情報をコピー］）。"""
        self.clipboard_clear()
        self.clipboard_append(self._diagnostic_text())
        messagebox.showinfo(
            "VA FAX",
            "診断情報をコピーしました。\n\n"
            "不具合を相談するときは、これを貼り付けて渡してください。版・環境・設定の数値・"
            "件数・誤りの起きた場所だけが入っていて、開いた・保存したファイルの名前や、ファイル・"
            "フォルダの場所は入っていません。\n"
            "渡す前に、メモ帳などに貼り付けて、中身を確かめることができます。",
            parent=self,
        )

    def _diagnostic_text(self) -> str:
        """診断情報の文。

        中身（開いた・保存したファイルの名前、フォルダの場所、例外のメッセージの本文）は
        一切入れない（diagnostics.pyの冒頭・F-24と同じ方針）。
        """
        d = diagnostics
        profile = self.profile
        defaults = self.settings.defaults
        lines = [
            f"VA FAX 診断情報（{datetime.now():%Y-%m-%d %H:%M}）",
            "※ 開いた・保存したファイルの名前、ファイルやフォルダの場所、利用者とPCの名前は"
            "入っていません。",
            "",
        ]
        lines += ["■ 環境"] + d.environment_lines(self, "VA FAX", __version__) + [""]

        page_limit = "なし" if profile.page_count_limit is None else f"{profile.page_count_limit}ページ"
        lines += [
            "■ 対象サービス（プロファイル。F-27）",
            _profile_constraint_text(profile),
            f"確認状態：{profile.verification_status}・ページ数上限：{page_limit}",
            f"解像度の段階：{'→'.join(str(v) for v in profile.resolution_dpi_steps)}dpi・"
            f"JPEG品質の段階：{'→'.join(str(v) for v in profile.jpeg_quality_steps)}",
            "",
        ]

        lines += [
            "■ 変換の既定設定（F-26）",
            f"圧縮方式：{defaults.compression_policy}・ディザ：{defaults.dither_method}・"
            f"JPEG品質：{defaults.jpeg_quality_level}",
            f"容量超過：{defaults.capacity_exceeded_method}・削減方式：{defaults.size_reduction_strategy}・"
            f"用紙正規化：{'する' if defaults.normalize_enabled else 'しない'}・"
            f"ページ番号：{'入れる' if defaults.page_numbers else '入れない'}",
            "",
        ]

        diagnosis = self.diagnosis
        document = self.document
        if document is None:
            doc_text = "開いていない"
        else:
            doc_text = (f"{document.page_count}ページ・材料のファイル {document.used_source_count()}件・"
                        f"保存先 {'あり' if self._save_path is not None else 'なし（名前のない文書）'}・"
                        f"未保存の作業 {'あり' if self._has_unsaved_work() else 'なし'}・"
                        f"元に戻せる {'はい' if document.can_undo else 'いいえ'}")
        lines += [
            "■ いまの画面の状態",
            f"文書：{doc_text}",
            "診断：" + ("まだ" if diagnosis is None else
                          f"済み（{diagnosis.page_count}ページ・"
                          f"{_format_size(diagnosis.file_size_bytes)}・"
                          f"テキスト{'あり' if diagnosis.has_text else 'なし'}・"
                          f"対応外サイズ {len(diagnosis.unsupported_pages)}ページ）"),
            f"処理内容：圧縮方式 {self.mode_var.get()}・ディザ {self.dither_label_var.get()}・"
            f"JPEG品質 {self.quality_label_var.get()}・容量超過 {self.capacity_method_var.get()}・"
            f"削減方式 {self.reduction_strategy_label_var.get()}・"
            f"用紙正規化 {'する' if self.normalize_var.get() else 'しない'}・"
            f"ページ番号 {'入れる' if self.page_numbers_var.get() else '入れない'}",
            f"保存：{'実行中' if self._saving else 'していない'}",
            "",
        ]

        records = self._errors.records
        lines.append(f"■ 起きた誤り（新しいものから {min(len(records), d.SHOW_ERRORS)} 件。"
                     f"覚えているのは全 {len(records)} 件）")
        if not records:
            lines.append("なし")
        for record in reversed(records[-d.SHOW_ERRORS:]):
            lines += d.format_record(record)
        return "\n".join(lines) + "\n"

    def _set_settings_menu_enabled(self, enabled: bool) -> None:
        """保存の実行中はプロファイル・既定設定の変更を禁止する（B-1。容量判定等の途中すり替わり防止）。"""
        state = "normal" if enabled else "disabled"
        self._file_menu.entryconfig("オプション...", state=state)

    def _build_widgets(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # 画面の操作ボタンは、すべて最上段のリボンに集約する（F-15）。
        self._build_ribbon()

        # プレビュー欄（左側・F-30）とコントロール一式（右側）をPanedWindowで分割し、
        # 境界のドラッグでプレビュー欄の幅を変更できるようにする（リサイズ可能）。
        # ttk.Panedwindowはadd()がweight指定のみでminsize/width/stretchに対応しない
        # （初期幅の挙動が変わる）ため、classic tk.PanedWindowのまま残す。色はbg指定が
        # そのまま確実に反映されるため、Buttonで生じたような表示崩れの懸念はない。
        self._paned = tk.PanedWindow(
            self, orient="horizontal", sashrelief="flat", sashwidth=6,
            bg=theme.COLOR_BORDER, bd=0)
        self._paned.grid(row=1, column=0, sticky="nsew")

        # プレビュー欄のサムネイルが、ページの一覧を兼ねる（F-15の4・F-33）。
        self.preview_pane = PreviewPane(self._paned)
        self.preview_pane.set_editable(True)
        self.preview_pane.on_delete = self._delete_pages
        self.preview_pane.on_move = self._move_pages
        self.preview_pane.on_selection_changed = self._update_command_states
        self.preview_pane.on_context_menu = self._show_page_menu
        self.preview_pane.on_page_double_click = self._on_page_double_click
        # 欄の外へ持ち出している間に表示するカーソル（ゴミ箱。同梱の.curを使う）
        self.preview_pane.remove_cursor = theme.resolve_remove_cursor(self.preview_pane)

        controls_frame = ttk.Frame(self._paned)
        controls_frame.columnconfigure(1, weight=1)
        controls_frame.rowconfigure(1, weight=1)

        self._paned.add(self.preview_pane, minsize=220, width=480, stretch="always")
        self._paned.add(controls_frame, minsize=520, width=680, stretch="always")

        pad = {"padx": 10, "pady": 4}
        label_pad = {"padx": (10, 2), "pady": 4}
        field_pad = {"padx": (2, 10), "pady": 4}

        ttk.Label(controls_frame, textvariable=self.profile_label_var, font=("", 10, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", **pad)

        ttk.Label(controls_frame, text="診断結果：").grid(row=1, column=0, sticky="nw", **label_pad)
        self.diagnosis_text = tk.Text(controls_frame, height=7, width=64, state="disabled", wrap="word")
        self.diagnosis_text.grid(row=1, column=1, columnspan=2, sticky="nswe", **field_pad)

        options = ttk.Labelframe(controls_frame, text="処理内容の選択（保存の前にご確認ください）")
        options.grid(row=2, column=0, columnspan=3, sticky="we", padx=10, pady=6)
        self._build_options(options)

        self.progress_bar = ttk.Progressbar(
            controls_frame, variable=self.progress_var, maximum=100, length=300)
        self.progress_bar.grid(row=3, column=0, columnspan=3, sticky="we", **pad)
        self.progress_bar.grid_remove()

        # 処理ログの欄は、Ver200でなくした（2026-11-17 班長指示）。開いた・保存した等の結果を、
        # 1行で示す（ページごとの処理内容は、ログファイルにだけ残す。F-17・F-24）。
        ttk.Label(controls_frame, textvariable=self.status_var, anchor="w", justify="left",
                  wraplength=640).grid(row=4, column=0, columnspan=3, sticky="we", **pad)

        ttk.Label(
            controls_frame,
            text="※ FAXの送信操作は、各インターネットFAXサービスの画面で別途行ってください。",
            foreground=theme.COLOR_MUTED, anchor="w", justify="left",
        ).grid(row=5, column=0, columnspan=3, sticky="w", padx=10, pady=(2, 10))

        # ファイルのドラッグ＆ドロップ（F-32・F-33）：文書を開いていないときは開き、
        # 開いているときはページの追加とする。ウィンドウのどこに落としても受ける。
        for widget in (self, self.preview_pane):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<DropPosition>>", self._on_drop_position)
            widget.dnd_bind("<<DropLeave>>", self._on_drop_leave)
            widget.dnd_bind("<<Drop>>", self._on_drop)

    # ------------------------------------------------------------------ リボン（F-15）

    def _build_ribbon(self) -> None:
        """画面最上段のリボン（Wordのリボンと同じ形。VA mintsと同じribbon.pyを用いる）。

        画面の操作ボタンはすべてここに集約する。組は「ファイル」（開く・保存）と
        「ページ」（追加・削除・回転〔角度はメニューで選ぶ〕・元に戻す・やり直す）と「送付書」
        （作成〔作成済みは編集〕。F-36）の3つ。
        主要な操作（新規作成・開く・保存・追加・作成）は大きいボタンとし、保存だけをアクセント色の
        枠線で強調する（決定の操作。開くは、新規作成と同じ「始める」操作なので強調しない。保存の強調は、
        保存すると開いたファイルと違うものができるときだけとする。2026-11-18 班長指示）。
        各ボタンはマウスを重ねると説明書きを出す（tooltip.py）。
        FAXサービスの変更・処理内容の既定値は、［ファイル］→［オプション］に置く（F-35）。
        """
        self._icons = IconSet(self)
        self.ribbon = ribbon = Ribbon(self, self._icons)
        ribbon.grid(row=0, column=0, sticky="we", padx=8, pady=(6, 2))

        group = ribbon.group("ファイル")
        # 保存は［保存］の1つとし、保存の画面で場所と名前を指定する（Ver200 の［名前を付けて保存］と
        # 小さい［保存］〔上書き保存〕を、2026-11-18 班長指示で1つにした）。
        # 新規作成（2026-11-18 班長指示）：白紙の文書から、［追加］でページを入れ、送付書を作って保存する
        self.new_button = group.large(
            "新規作成", self._on_new, icon="document_text",
            tip="ページのない新しい文書を作ります（Ctrl+N）。"
                "［追加］か、ファイルのドラッグ＆ドロップでページを入れ、必要なら送付書を［作成］して、"
                "［保存］します。")
        self.open_button = group.large(
            "開く", self._on_open,
            tip="PDFファイルを開きます（Ctrl+O）。"
                "ウィンドウへのドラッグ＆ドロップでも開けます。")
        self.save_button = group.large(
            "保存", self._on_save, style=theme.RIBBON_ACCENT_BUTTON_STYLE,
            state="disabled", tip=self._save_button_tip)

        group = ribbon.group("ページ")
        self.add_button = group.large(
            "追加", self._on_add_pages, state="disabled",
            tip="PDF・画像（JPEG・PNG）のファイルを選び、そのページを、"
                "選んでいるページの後ろに入れます（選んでいなければ末尾）。"
                "ウィンドウへのドラッグ＆ドロップでも追加できます。"
                "追加したファイル自体は変更しません。")
        self.delete_button = group.small(
            "削除", self._delete_pages, state="disabled", tip=self._delete_button_tip)
        # 回転は、1つのボタンにまとめ、角度をメニューで選ぶ（2026-11-17 班長指示）
        self.rotate_button = group.dropdown(
            "回転", [(label, lambda d=degrees: self._rotate_pages(d)) for label, degrees in _ROTATIONS],
            state="disabled",
            tip="選んでいるページを回します。"
                "押すと、角度（左に90°・右に90°・180°）を選ぶメニューが開きます。")
        group.new_column()
        self.undo_button = group.small(
            "元に戻す", self._on_undo, state="disabled",
            tip="ページの編集（追加・削除・並べ替え・回転）を、1つ戻します（Ctrl+Z）。")
        self.redo_button = group.small(
            "やり直す", self._on_redo, state="disabled",
            tip="元に戻したページの編集を、やり直します（Ctrl+Y）。")

        group = ribbon.group("送付書")
        # 文書に送付書があるときは、ボタンの文字を「編集」にする（2026-11-18 班長指示）
        self.cover_button = group.large(
            "作成", self._on_cover, icon="document_header", state="disabled", tip=self._cover_button_tip)

    def _cover_button_tip(self) -> str:
        """「送付書」の組のボタンの説明書き（作成済みかで変える）。"""
        if self._current_cover() is not None:
            return ("文書の送付書（兼受領書）の入力の画面を開きます。［OK］で、直した送付書に差し替えます。"
                    "送付書のページのダブルクリックでも開けます。")
        return ("送付書（兼受領書）を作って、文書の先頭に入れます。"
                "書類の一覧は、追加したファイルの名前から作ります（VA mints の「甲001 売買契約書」の"
                "形の名前は、「甲第1号証」と書きます）。"
                "事務所名などは、［ファイル］→［オプション］の「送付書」で入力します。")

    def _save_makes_change(self) -> bool:
        """保存すると、開いたファイルと違うものができるか（［保存］の青枠。2026-11-18 班長指示）。

        違うものができるのは、圧縮方式が二値化・グレースケールのとき、ページ番号を入れるとき、
        送付書があるとき、ページを編集したとき（開いたファイルの全ページを元の順・向きのまま並べた
        だけではないとき。新規作成した文書・画像を開いた文書を含む）。
        """
        document = self.document
        if document is None or document.page_count == 0:
            return False
        if self.mode_var.get() != "none" or self.page_numbers_var.get():
            return True
        if self._current_cover() is not None:
            return True
        if self._name_base is None or not document.is_untouched_single_source():
            return True
        return document.sources[document.pages[0].source].kind != "pdf"

    def _has_unsaved_work(self) -> bool:
        """まだ保存していない作業があるか（2026-11-18 班長指示で、題名の「＊」・［保存］の青枠・
        閉じるときの確認を、この1つの基準にそろえた）。開いてから（又は最後に保存してから）まだ
        保存しておらず、かつ、保存すると開いたファイルと違うものができるとき。"""
        return self.document is not None and self._dirty and self._save_makes_change()

    def _update_save_emphasis(self) -> None:
        """［保存］の青枠（アクセント色の枠線）は、まだ保存していない作業があるときだけ出す
        （_has_unsaved_work。保存した直後や、保存しても同じものしかできないときは、普通の大きい
        ボタンの見た目にする）。"""
        if not hasattr(self, "save_button"):
            return
        style = (theme.RIBBON_ACCENT_BUTTON_STYLE if self._has_unsaved_work()
                 else theme.RIBBON_LARGE_BUTTON_STYLE)
        if str(self.save_button.cget("style")) != style:
            self.save_button.configure(style=style)

    def _save_unavailable_reason(self) -> str:
        """保存のボタンが押せない理由（押せるときは空）。"""
        if self.document is None:
            return "ファイルを開くと押せるようになります。"
        if self._saving:
            return "保存しています。終わるまでお待ちください。"
        if self.diagnosis is None:
            return "診断が終わると押せるようになります。"
        return ""

    def _save_button_tip(self) -> str:
        """［保存］ボタンの説明書き（押せないときは、その理由を示す）。"""
        return self._save_unavailable_reason() + (
            "選択中の処理内容（画像への変換など）を行って、場所と名前を指定して保存します（Ctrl+S）。"
            "名前の初めの値は「元の名前_fax.pdf」で、元のファイルは残ります。"
            "同じ名前を選ぶと、置き換えてよいかを確かめてから上書きします。"
            "FAXの送信操作は、各インターネットFAXサービスの画面で別途行ってください。")

    def _delete_button_tip(self) -> str:
        """［削除］ボタンの説明書き（押せないときは、その理由を示す）。"""
        base = "選んでいるページを、文書から取り除きます（Deleteキー）。元のファイルは変更しません。"
        if self.document is not None and self.document.page_count <= 1:
            return "最後の1ページは削除できません。" + base
        return base

    def _build_options(self, parent: tk.Misc) -> None:
        # 選択肢の数が少ない項目から順に並べる：圧縮方式→容量超過→用紙正規化。
        ttk.Label(parent, text="圧縮方式：").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(4, 0))

        ttk.Radiobutton(parent, text=_MODE_BINARIZE_TEXT, variable=self.mode_var, value="binarize",
                        command=self._update_mode_widgets).grid(row=1, column=0, sticky="w", padx=8)
        ttk.Label(parent, text="※ カラー・階調情報が失われますが、FAXで最も一般的な原稿形式です。",
                  foreground=theme.COLOR_MUTED).grid(row=1, column=1, columnspan=2, sticky="w")

        ttk.Label(parent, text="　ディザ方式：").grid(row=2, column=0, sticky="e", padx=(24, 0))
        self.dither_combo = ttk.Combobox(
            parent, textvariable=self.dither_label_var, state="readonly", width=10,
            values=list(_DITHER_LABELS.values()))
        self.dither_combo.grid(row=2, column=1, sticky="w")

        ttk.Radiobutton(parent, text=_MODE_GRAYSCALE_TEXT, variable=self.mode_var, value="grayscale",
                        command=self._update_mode_widgets).grid(row=3, column=0, sticky="w", padx=8)
        ttk.Label(parent, text="※ サービス側で黒つぶれ・白飛びが起こる場合があります。"
                              "実際に送信してご確認ください。", foreground=theme.COLOR_MUTED).grid(
            row=3, column=1, columnspan=2, sticky="w")

        ttk.Label(parent, text="　JPEG品質：").grid(row=4, column=0, sticky="e", padx=(24, 0))
        self.quality_combo = ttk.Combobox(
            parent, textvariable=self.quality_label_var, state="readonly", width=10,
            values=_QUALITY_LABELS)
        self.quality_combo.grid(row=4, column=1, sticky="w")

        # 「何もしない」（F-11。2026-11-17 班長指示）：ページを画像にせず、そのまま保存する
        ttk.Radiobutton(parent, text=_NOTHING_TEXT, variable=self.mode_var, value="none",
                        command=self._update_mode_widgets).grid(row=5, column=0, sticky="w", padx=8)
        ttk.Label(parent, text="※ ページを画像にしません。文字データ・カラーが残り、"
                              "サービスによっては送信できない場合があります。",
                  foreground=theme.COLOR_MUTED).grid(row=5, column=1, columnspan=2, sticky="w")

        ttk.Label(parent, text="容量超過時の処理方式：").grid(
            row=6, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 0))

        self.reduce_resolution_radio = ttk.Radiobutton(
            parent, text=_CAPACITY_REDUCE_TEXT, variable=self.capacity_method_var,
            value="reduce_resolution", command=self._update_strategy_combo_state)
        self.reduce_resolution_radio.grid(row=7, column=0, sticky="w", padx=8)
        ttk.Label(parent, text="※ 送信回数を増やさずに済みます。", foreground=theme.COLOR_MUTED).grid(
            row=7, column=1, columnspan=2, sticky="w")

        ttk.Label(parent, text="　削減方式：").grid(row=8, column=0, sticky="e", padx=(24, 0))
        self.strategy_combo = ttk.Combobox(
            parent, textvariable=self.reduction_strategy_label_var, state="readonly", width=12,
            values=list(_REDUCTION_STRATEGY_LABELS.values()))
        self.strategy_combo.grid(row=8, column=1, sticky="w")

        self.split_radio = ttk.Radiobutton(
            parent, text=_CAPACITY_SPLIT_TEXT, variable=self.capacity_method_var,
            value="split", command=self._update_strategy_combo_state)
        self.split_radio.grid(row=9, column=0, sticky="w", padx=8)
        ttk.Label(parent, text="※ 画質を下げずに済みます。", foreground=theme.COLOR_MUTED).grid(
            row=9, column=1, columnspan=2, sticky="w")

        # 「何もしない」（F-13。2026-11-17 班長指示）：上限を超えていても、そのまま保存する
        ttk.Radiobutton(
            parent, text=_NOTHING_TEXT, variable=self.capacity_method_var,
            value="none", command=self._update_strategy_combo_state).grid(
            row=10, column=0, sticky="w", padx=8)
        ttk.Label(parent, text="※ 上限を超えていても、そのまま保存します（保存の後に知らせます）。",
                  foreground=theme.COLOR_MUTED).grid(row=10, column=1, columnspan=2, sticky="w")

        self.normalize_check = tk.Checkbutton(parent, text=_NORMALIZE_TEXT, variable=self.normalize_var)
        self.normalize_check.grid(row=11, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 0))
        # ページ番号（F-37。Ver210。2026-11-18 班長決定）
        self.page_numbers_check = tk.Checkbutton(parent, text=_PAGE_NUMBERS_TEXT, variable=self.page_numbers_var)
        self.page_numbers_check.grid(row=12, column=0, columnspan=3, sticky="w", padx=8)

        # 圧縮方式が「何もしない」のときに、効かない項目を選べなくしている理由（F-11）
        self.none_note_var = tk.StringVar()
        ttk.Label(parent, textvariable=self.none_note_var, foreground=theme.COLOR_MUTED,
                  justify="left").grid(row=13, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 4))

        self._update_option_visibility()
        self._update_mode_widgets()

    def _update_option_visibility(self) -> None:
        """用紙正規化・「ページ番号を入れる」は、［オプション］の既定値でオンのときだけ、処理内容の
        選択に出す。オフのときは出さず、その処理を行わない（2026-11-18 班長指示）。"""
        for enabled, check, var in ((self.settings.defaults.normalize_enabled, self.normalize_check,
                                     self.normalize_var),
                                    (self.settings.defaults.page_numbers, self.page_numbers_check,
                                     self.page_numbers_var)):
            if enabled:
                check.grid()
            else:
                check.grid_remove()
                var.set(False)

    def _update_strategy_combo_state(self) -> None:
        """削減方式は、「解像度を下げて再実行」を選択している場合のみ選択可能とする。"""
        enabled = (self.capacity_method_var.get() == "reduce_resolution"
                   and self.mode_var.get() != "none")
        self.strategy_combo.configure(state="readonly" if enabled else "disabled")

    def _update_mode_widgets(self) -> None:
        """圧縮方式に応じて、詳細設定と、効かない項目の選べる・選べないを切り替える（F-11）。"""
        mode = self.mode_var.get()
        nothing = mode == "none"
        self.dither_combo.configure(state="readonly" if mode == "binarize" else "disabled")
        self.quality_combo.configure(state="readonly" if mode == "grayscale" else "disabled")
        if nothing:
            # 「解像度・品質を下げて再実行」は効かないので、「何もしない」に切り替える
            if self.capacity_method_var.get() == "reduce_resolution":
                self._capacity_switched_for_none = True
                self.capacity_method_var.set("none")
        elif self._capacity_switched_for_none:
            # 圧縮方式を戻したら、容量超過時の処理方式も元に戻す
            self._capacity_switched_for_none = False
            if self.capacity_method_var.get() == "none":
                self.capacity_method_var.set("reduce_resolution")
        self.reduce_resolution_radio.configure(state="disabled" if nothing else "normal")
        self.normalize_check.configure(state="disabled" if nothing else "normal")
        self.none_note_var.set(_NONE_MODE_NOTE if nothing else "")
        self._update_strategy_combo_state()

    # ------------------------------------------------------------------ プレビュー（F-30）

    def _bind_preview_option_traces(self) -> None:
        """処理内容の変更を監視し、プレビューへ反映する。

        圧縮方式・ディザ方式・用紙正規化は変換イメージ（シミュレーション）の見た目を
        変えるため、変更のたびに再生成する。JPEG品質・容量超過時の処理方式・削減方式は
        変換イメージの見た目には影響しない（ConvertOptionsに含まれない）が、変換結果
        （実物）の表示中に変更された場合は、表示中の結果が現在の選択と対応しなくなる
        ため、変換イメージ表示へ戻すきっかけとして監視する（PreviewPane.update_optionsは
        オプションが実質同一でも、実物表示中であればシミュレーション表示へ戻す）。
        """
        self.mode_var.trace_add("write", self._on_preview_options_changed)
        self.dither_label_var.trace_add("write", self._on_preview_options_changed)
        self.normalize_var.trace_add("write", self._on_preview_options_changed)
        self.page_numbers_var.trace_add("write", self._on_preview_options_changed)
        self.quality_label_var.trace_add("write", self._on_preview_options_changed)
        self.capacity_method_var.trace_add("write", self._on_preview_options_changed)
        self.reduction_strategy_label_var.trace_add("write", self._on_preview_options_changed)

    def _current_preview_options(self) -> convert_module.ConvertOptions:
        return convert_module.ConvertOptions(
            mode=self.mode_var.get(),
            normalize_enabled=self.normalize_var.get(),
            dither_method=_DITHER_LABELS_REV[self.dither_label_var.get()],
            page_numbers=bool(self.page_numbers_var.get()),
        )

    def _on_preview_options_changed(self, *_args) -> None:
        if self.document is not None and not self._applying_defaults:
            # 処理の選択を変えたら、未保存とする（F-15）
            self._dirty = True
        # 題名の「＊」・［保存］の青枠は、処理の選択によって変わる（_has_unsaved_work）
        self._update_save_emphasis()
        if self.document is None:
            return
        self._update_title()
        self.preview_pane.update_options(self._current_preview_options())

    # ------------------------------------------------------------------ キー操作

    def _bind_keys(self) -> None:
        """メイン画面のどこにフォーカスがあっても効くキー操作（F-32・F-33）。大文字は、
        キャップスロック中・Shift併用の取りこぼし対策。"""
        for sequence, handler in (
            ("<Control-z>", self._on_undo), ("<Control-Z>", self._on_undo),
            ("<Control-y>", self._on_redo), ("<Control-Y>", self._on_redo),
            ("<Control-o>", self._on_open), ("<Control-O>", self._on_open),
            ("<Control-n>", self._on_new), ("<Control-N>", self._on_new),
            ("<Control-s>", self._on_save),
            ("<Control-Shift-S>", self._on_save), ("<Control-Shift-s>", self._on_save),
        ):
            self.bind_all(sequence, lambda event, h=handler: self._on_global_key(event, h), add="+")

    def _bind_mac_keys(self) -> None:
        """Mac（ソースから動かす場合。N-03）のキー操作を、Windowsのキー操作にそろえる。

        ⌘Z・⌘Y・⌘O・⌘S・⌘⇧S を、Ctrl と同じ働きにする。⌘Q は、Tkの既定ではTclのexitで
        直ちにプロセスを終えるため、未保存の確認とプレビューの描画スレッドの後始末を通らない。
        閉じるボタンと同じ処理にする。
        """
        for sequence, handler in (
            ("<Command-z>", self._on_undo), ("<Command-Z>", self._on_undo),
            ("<Command-y>", self._on_redo), ("<Command-Y>", self._on_redo),
            ("<Command-o>", self._on_open), ("<Command-n>", self._on_new), ("<Command-s>", self._on_save),
            ("<Command-Shift-S>", self._on_save), ("<Command-Shift-s>", self._on_save),
        ):
            self.bind_all(sequence, lambda event, h=handler: self._on_global_key(event, h), add="+")
        self.createcommand("::tk::mac::Quit", self._on_quit)

    def _on_global_key(self, event, handler: Callable[[], None]) -> Optional[str]:
        """別ウィンドウ（設定の画面等）での押下は対象外とする。"""
        widget = event.widget
        try:
            if not isinstance(widget, tk.Misc) or widget.winfo_toplevel() is not self:
                return None
        except tk.TclError:
            return None
        handler()
        return "break"

    # ------------------------------------------------------------------ 題名・ボタンの状態

    def _document_name(self) -> Optional[str]:
        if self.document is None:
            return None
        if self._save_path is not None:
            return self._save_path.name
        if self._name_base is None:
            return NEW_DOCUMENT_NAME          # 新規作成した文書（保存するまで）
        return suggested_save_name(self._name_base, self._name_suffix())

    def _save_base(self) -> Optional[Path]:
        """保存の画面の名前・場所の初めの値の元にするファイル（F-32）。保存した後は保存したファイル、
        開いた文書は開いたファイル。新規作成した文書は、初めに入れたファイル（ファイルのページがなく、
        送付書だけのときは「送付書.pdf」。ページがなければNone）。"""
        if self._save_path is not None:
            return self._save_path
        if self._name_base is not None:
            return self._name_base
        first = next((path for path in self._source_paths if path is not None), None)
        if first is not None:
            return first
        if self.document is not None and self.document.page_count:
            folder = self._doc_dir or Path(self._initial_open_dir())
            return folder / COVER_ONLY_NAME
        return None

    def _name_suffix(self) -> Optional[str]:
        """名前の初めの値に付ける文字（付けないならNone。［ファイル］→［オプション］。F-35）。"""
        return self.settings.save_name_suffix if self.settings.append_fax_to_save_name else None

    def _update_title(self) -> None:
        self.title(window_title(self._document_name(), self._has_unsaved_work()))

    def _update_command_states(self) -> None:
        """リボンとメニューのボタンの、押せる・押せないを、いまの状態に合わせる。"""
        document = self.document
        idle = not self._saving
        has_doc = document is not None and idle
        selected = self.preview_pane.selected_pages() if has_doc else []
        can_save = has_doc and self.diagnosis is not None and self._assembled is not None

        def state(enabled: bool) -> str:
            return "normal" if enabled else "disabled"

        self.new_button.configure(state=state(idle))
        self.open_button.configure(state=state(idle))
        self.save_button.configure(state=state(can_save))
        self._update_save_emphasis()
        self.add_button.configure(state=state(has_doc))
        self.delete_button.configure(
            state=state(has_doc and bool(selected) and len(selected) < document.page_count))
        self.rotate_button.configure(state=state(has_doc and bool(selected)))
        self.undo_button.configure(state=state(has_doc and document.can_undo))
        self.redo_button.configure(state=state(has_doc and document.can_redo))
        self.cover_button.configure(state=state(has_doc),
                                    text="編集" if self._current_cover() is not None else "作成")

        menu = self._file_menu
        menu.entryconfig("新規作成", state=state(idle))
        menu.entryconfig("開く...", state=state(idle))
        menu.entryconfig("保存...", state=state(can_save))
        menu.entryconfig("閉じる", state=state(has_doc))
        menu.entryconfig("オプション...", state=state(idle))

    # ------------------------------------------------------------------ ファイルを開く（F-32）

    def _paper_mm(self) -> tuple[float, float]:
        """画像のページの大きさを決めるのに使う用紙（プロファイルの正規化先。F-29・F-34）。"""
        target = self.profile.normalize_target
        return (target.width_mm, target.height_mm)

    def _read_source(self, path: Path) -> Optional[Source]:
        """ファイルを読み、材料にする。読めないときは、画面に知らせてNoneを返す
        （ログにはファイル名を書かない。F-24）。"""
        try:
            data = path.read_bytes()
        except OSError as exc:
            self.logger.info("ファイル読込失敗：%s", type(exc).__name__)
            messagebox.showerror(
                APP_NAME,
                f"「{path.name}」を読み込めませんでした。ファイルが移動・削除されていないか、"
                "他のアプリで開かれていないかご確認ください。", parent=self)
            return None
        try:
            return document_module.load_source(data, self._paper_mm())
        except SourceError as exc:
            self.logger.info("ファイルを材料にできない：%s", type(exc).__name__)
            messagebox.showerror(APP_NAME, f"「{path.name}」を処理できませんでした。\n{exc}", parent=self)
            return None
        except Exception as exc:  # 予期しない例外（F-22）
            self.logger.error("ファイルの読込で予期しないエラー：%s", _format_exception_for_log(exc))
            self._errors.record(exc, "ファイルの読込")
            messagebox.showerror(
                APP_NAME, f"「{path.name}」の読み込み中に問題が発生しました。\n{CONTACT_NOTICE}", parent=self)
            return None

    def _initial_open_dir(self) -> str:
        return self.settings.last_input_dir or str(Path.home())

    def _on_new(self) -> None:
        """［新規作成］：ページのない新しい文書を作る（2026-11-18 班長指示）。"""
        if self._saving:
            return
        self._guard_unsaved(self._new_document)

    def _new_document(self) -> None:
        self._close_document()
        self.document = Document()
        self._doc_dir = Path(self._initial_open_dir())
        self._dirty = False                          # 何も入れていない間は、閉じるときに尋ねない
        self._apply_defaults_to_screen()
        self.logger.info("新しい文書を作った")
        self._set_status("新しい文書を作りました。")
        self._document_changed([], None)

    def _on_open(self) -> None:
        if self._saving:
            return
        path_str = filedialog.askopenfilename(
            title="開く", initialdir=self._initial_open_dir(), filetypes=_OPEN_FILETYPES, parent=self)
        if path_str:
            self._guard_unsaved(lambda: self._open_path(Path(path_str)))

    def _open_path(self, path: Path, more_paths: Iterable[Path] = ()) -> bool:
        """ファイルを開いて、新しい文書にする。more_pathsは、続けて後ろに追加するファイル。"""
        source = self._read_source(path)
        if source is None:
            return False
        document = Document()
        document.add_source(source)
        document.clear_history()   # 開く前には戻さない

        # 画像を開いたときは、名前のない文書にする（画像のファイルをPDFで書き換えないため。F-32）。
        # Ver200 の［コピーして開く］は、［保存］を1つにまとめて働きに差がなくなったため、Ver210 で
        # なくした（2026-11-18 班長指示）。
        untitled = source.kind == "image"
        self.document = document
        self._save_path = None if untitled else path
        self._name_base = path
        self._doc_dir = path.parent
        self._material_paths = [path]
        self._added_paths = []
        self._source_paths = [path]
        self._covers = []
        # このツールで送付書を入れて保存したファイルなら、その入力を読み、1ページ目からを送付書とみなす（F-36）
        saved_cover = cover_module.load_input(path) if source.kind == "pdf" else None
        if saved_cover is not None and saved_cover.cover_pages < source.page_count:
            total = saved_cover.total_pages if saved_cover.total_pages == source.page_count else None
            self._covers.append(CoverRecord(
                frozenset((0, i) for i in range(saved_cover.cover_pages)), saved_cover.input, total))
            self.logger.info("送付書の入力を読んだ：送付書%dページ", saved_cover.cover_pages)
        # 開いた直後は、まだ保存していない（保存すると違うものができるなら、題名の「＊」・青枠・
        # 閉じるときの確認を出す。2026-11-18 班長指示）
        self._dirty = True
        self.diagnosis = None
        self._assembled = None

        self.settings.last_input_dir = str(path.parent)
        save_settings(self.settings)
        # 処理の選択は、ファイルを開いたときに既定設定から決め直す（F-26）。本ツールで保存した
        # ファイルなら、圧縮方式の初めの値を「何もしない」にする（F-06）。
        self._apply_defaults_to_screen(made_by_va_fax=document.all_made_by_va_fax())
        if document.all_made_by_va_fax():
            self.logger.info("本ツールで保存したファイル：圧縮方式の初めの値を「何もしない」にした")
        self.logger.info("ファイルを開いた：%dページ（%s）", source.page_count, source.kind)
        self._set_status(f"ファイルを開きました：{path.name}（{source.page_count}ページ）")

        selection: Iterable[int] = [0]
        more = list(more_paths)
        if more:
            added = self._load_and_add(more, at=None)
            if added is not None:
                document.clear_history()
                selection = [0]
        self._document_changed(selection, 0)
        return True

    def _on_close_document(self) -> None:
        if self.document is not None and not self._saving:
            self._guard_unsaved(self._close_document)

    def _close_document(self) -> None:
        self.document = None
        self._save_path = None
        self._name_base = None
        self._material_paths = []
        self._added_paths = []
        self._source_paths = []
        self._covers = []
        self._dirty = False
        self.diagnosis = None
        self._assembled = None
        self._diagnosis_token += 1   # 進行中の診断の結果を無効にする
        self._set_diagnosis_text("")
        self._set_status("")
        self.progress_var.set(0.0)
        self.progress_bar.grid_remove()
        self.preview_pane.clear()
        self._update_title()
        self._update_command_states()

    def _on_quit(self) -> None:
        if self._saving:
            messagebox.showinfo(APP_NAME, "保存しています。終わるまでお待ちください。", parent=self)
            return
        self._guard_unsaved(self._quit)

    def _quit(self) -> None:
        self._remember_window_state()
        self.destroy()

    # ------------------------------------------------------------------ ウィンドウの大きさ・位置（F-23）

    def _restore_window_state(self) -> None:
        """前に閉じたときの大きさ・位置・最大化に戻す。位置がいまの画面の外になるときは、
        大きさだけを戻す。"""
        geometry = fit_geometry_to_screen(
            self.settings.window_geometry, self.winfo_screenwidth(), self.winfo_screenheight())
        if geometry:
            self.geometry(geometry)
            self._normal_geometry = self.settings.window_geometry
        if self.settings.window_maximized:
            try:
                self.state("zoomed")
            except tk.TclError:  # 「zoomed」のない環境（Mac等）では、そのままにする
                pass
        self.bind("<Configure>", self._on_window_configure, add="+")

    def _on_window_configure(self, event) -> None:
        """最大化していないときの大きさ・位置を覚えておく（最大化したまま閉じても、元の大きさを
        保存できるように）。"""
        if event.widget is self and self.state() == "normal":
            self._normal_geometry = self.geometry()

    def _remember_window_state(self) -> None:
        try:
            self.settings.window_maximized = self.state() == "zoomed"
            if self._normal_geometry:
                self.settings.window_geometry = self._normal_geometry
            save_settings(self.settings)
        except (tk.TclError, OSError):  # 保存できなくても、終了は続ける
            pass

    def _guard_unsaved(self, action: Callable[[], object]) -> None:
        """未保存の文書があれば、保存するかを尋ねてから、actionを行う（F-32の5）。"""
        if not self._has_unsaved_work():
            action()
            return
        choice = self._ask_save_changes(self._document_name() or "")
        if choice == "save":
            self._on_save(then=action)
        elif choice == "discard":
            action()

    def _ask_save_changes(self, name: str) -> Optional[str]:
        return ask_choice(
            self, "保存の確認", f"「{name}」を保存していません。保存しますか？",
            [("保存する", "save", True), ("保存しない", "discard", False), ("キャンセル", "cancel", False)],
            cancel_value="cancel")

    # ------------------------------------------------------------------ ドラッグ＆ドロップ

    def _on_drop_position(self, event) -> str:
        if self.document is not None and not self._saving:
            self.preview_pane.external_drop_index(event.x_root, event.y_root)
        return event.action

    def _on_drop_leave(self, _event=None) -> None:
        self.preview_pane.hide_drop_indicator()

    def _on_drop(self, event) -> str:
        paths = [Path(p) for p in _parse_dropped_paths(event.data)]
        if self.document is None:
            self.preview_pane.hide_drop_indicator()
            if paths and not self._saving:
                self._open_path(paths[0], more_paths=paths[1:])
            return event.action
        at = self.preview_pane.external_drop_index(event.x_root, event.y_root)
        self.preview_pane.hide_drop_indicator()
        if paths and not self._saving:
            self._add_paths(paths, at)
        return event.action

    # ------------------------------------------------------------------ ページの編集（F-33）

    def _can_edit(self) -> bool:
        return self.document is not None and not self._saving

    def _page_menu_items(self) -> list[Optional[tuple[str, Callable[[], object], bool]]]:
        """サムネイルの右クリックメニューの項目（F-33。リボンの「ページ」の組と同じ操作）。

        (文字, 実行する処理, 押せるか) の並び。Noneは区切り線。押せる・押せないは、リボンの
        ボタンと同じ条件にする。
        """
        document = self.document
        can_edit = self._can_edit()
        selected = self.preview_pane.selected_pages() if can_edit else []
        has_selection = bool(selected)
        items: list[Optional[tuple[str, Callable[[], object], bool]]] = [
            ("追加...", self._on_add_pages, can_edit),
            ("削除", self._delete_pages, has_selection and len(selected) < document.page_count),
            None,
        ]
        items += [(label, lambda d=degrees: self._rotate_pages(d), has_selection)
                  for label, degrees in _ROTATIONS]
        items += [
            None,
            ("元に戻す", self._on_undo, can_edit and document.can_undo),
            ("やり直す", self._on_redo, can_edit and document.can_redo),
        ]
        return items

    def _show_page_menu(self, x_root: int, y_root: int) -> None:
        """サムネイルの右クリックメニューを、ポインタの位置に出す。"""
        if self.document is None:
            return
        menu = tk.Menu(self, tearoff=False)
        for item in self._page_menu_items():
            if item is None:
                menu.add_separator()
                continue
            label, command, enabled = item
            menu.add_command(label=label, command=command, state="normal" if enabled else "disabled")
        self._page_menu = menu   # 表示している間、破棄されないように持っておく
        try:
            menu.tk_popup(x_root, y_root)
        finally:
            menu.grab_release()

    def _on_add_pages(self) -> None:
        if not self._can_edit():
            return
        path_strs = filedialog.askopenfilenames(
            title="追加するファイルを選択", initialdir=self._initial_open_dir(),
            filetypes=_OPEN_FILETYPES, parent=self)
        if not path_strs:
            return
        selected = self.preview_pane.selected_pages()
        self._add_paths([Path(p) for p in path_strs], max(selected) + 1 if selected else None)

    def _confirm_adding_many_files(self, count: int) -> bool:
        """一度の操作で多数件を追加する場合の確認（誤って大量に落とした場合の歯止め）。"""
        if count <= CONFIRM_ADD_FILES_THRESHOLD:
            return True
        return messagebox.askyesno(APP_NAME, f"{count}件のファイルを追加します。よろしいですか？", parent=self)

    def _load_and_add(self, paths: list[Path], at: Optional[int]) -> Optional[range]:
        """ファイルを読んで、ページの表に足す。読めないファイルは、知らせて飛ばす。
        1つも足せなかったときはNone。"""
        assert self.document is not None
        sources: list[Source] = []
        loaded: list[Path] = []
        for path in paths:
            source = self._read_source(path)
            if source is not None:
                sources.append(source)
                loaded.append(path)
        if not sources:
            return None
        added = self.document.add_sources(sources, at)
        self._material_paths += loaded
        self._added_paths += loaded
        self._source_paths += loaded
        self.settings.last_input_dir = str(loaded[-1].parent)
        save_settings(self.settings)
        self.logger.info("ファイルを追加：%d件・%dページ", len(sources), len(added))
        self._set_status(f"{len(sources)}件のファイル（{len(added)}ページ）を追加しました。")
        return added

    def _add_paths(self, paths: list[Path], at: Optional[int]) -> None:
        if not self._can_edit() or not paths:
            return
        if not self._confirm_adding_many_files(len(paths)):
            return
        added = self._load_and_add(paths, at)
        if added is not None:
            self._edited(added, added.start)

    def _delete_pages(self, pages: Optional[list[int]] = None) -> None:
        if not self._can_edit():
            return
        document = self.document
        pages = self.preview_pane.selected_pages() if pages is None else pages
        if not pages:
            return
        if len(pages) >= document.page_count:
            messagebox.showinfo(
                APP_NAME, "すべてのページを削除することはできません（最後の1ページは残ります）。",
                parent=self)
            return
        first = min(pages)
        if document.delete(pages):
            self._edited([min(first, document.page_count - 1)])

    def _move_pages(self, pages: list[int], drop_index: int) -> None:
        if not self._can_edit():
            return
        moved = self.document.move(pages, drop_index)
        if moved is not None:
            self._edited(moved, moved.start)

    def _rotate_pages(self, degrees: int) -> None:
        if not self._can_edit():
            return
        pages = self.preview_pane.selected_pages()
        if pages and self.document.rotate(pages, degrees):
            self._edited(pages, self.preview_pane.active_page())

    def _on_undo(self) -> None:
        if self._can_edit() and self.document.undo():
            self._edited(self._clipped_selection())

    def _on_redo(self) -> None:
        if self._can_edit() and self.document.redo():
            self._edited(self._clipped_selection())

    def _clipped_selection(self) -> list[int]:
        count = self.document.page_count
        kept = [i for i in self.preview_pane.selected_pages() if i < count]
        return kept or [count - 1]

    def _edited(self, selection: Iterable[int], active: Optional[int] = None) -> None:
        """ページの表を書き換えた後の更新。"""
        self._dirty = True
        self._document_changed(selection, active)

    def _document_changed(self, selection: Optional[Iterable[int]] = None,
                          active: Optional[int] = None) -> None:
        """文書（ページの表）が変わった後、題名・ボタン・事前診断・プレビューを更新する
        （F-04・F-30）。selectionは、更新の後に選択しておくページ。"""
        if selection is None:
            selection = self.preview_pane.selected_pages()
            active = self.preview_pane.active_page()
        self._pending_selection = (list(selection), active)
        # 前の保存の進捗表示が残らないよう初期化する
        self.progress_var.set(0.0)
        self.progress_bar.grid_remove()
        self._update_title()
        if self.document is not None and self.document.page_count == 0:
            # ページのない文書（新規作成の直後や、元に戻して空になったとき）：診断・保存はしない
            self._diagnosis_token += 1
            self.diagnosis = None
            self._assembled = None
            self.preview_pane.clear()
            self._set_diagnosis_text(EMPTY_DOCUMENT_TEXT)
        else:
            self._run_diagnosis()
        self._update_command_states()

    # ------------------------------------------------------------------ 事前診断（F-04）

    def _run_diagnosis(self) -> None:
        """ページの表の組み立てと事前診断を、バックグラウンドスレッドで行う（GUIスレッドを
        ブロックしない）。

        トークン（世代番号）を発行し、応答時に照合することで、診断中に文書が変更された
        場合の古い結果を無視する（診断対象と［保存］の状態の食い違い防止）。
        """
        document = self.document
        if document is None:
            return
        self._set_diagnosis_text("診断中です...")
        self.diagnosis = None
        self._assembled = None
        self._diagnosis_token += 1
        token = self._diagnosis_token
        profile = self.profile  # 診断中にプロファイルが変更されても、この診断はこの世代の値で行う
        # 材料とページの表の写しを渡す（スレッドの側では、文書そのものに触れない）
        args = (document.sources, document.pages, document.used_source_count(), profile, token)
        threading.Thread(target=self._do_diagnose, args=args, daemon=True).start()

    def _do_diagnose(self, sources, pages, source_count: int, profile: Profile, token: int) -> None:
        try:
            assembled = document_module.assemble(sources, pages)
            diagnosis = diagnose_module.diagnose(assembled, profile)
        except (diagnose_module.EncryptedPdfError, diagnose_module.CorruptedPdfError) as exc:
            self.logger.info("診断エラー：%s", type(exc).__name__)
            self._message_queue.put({"type": "diagnosis_error", "token": token, "message": str(exc)})
            return
        except Exception as exc:  # 予期しない例外（F-22）：詳細は出さずログと診断情報にのみ記録
            self.logger.error("診断中に予期しないエラーが発生しました：%s", _format_exception_for_log(exc))
            self._errors.record(exc, "診断")
            self._message_queue.put({
                "type": "diagnosis_error", "token": token,
                "message": f"ファイルの診断中に問題が発生しました。\n{CONTACT_NOTICE}",
            })
            return

        self._log_diagnosis_summary(diagnosis)
        self._message_queue.put({
            "type": "diagnosis_done", "token": token, "diagnosis": diagnosis,
            "profile": profile, "assembled": assembled, "source_count": source_count,
        })

    def _log_diagnosis_summary(self, d: diagnose_module.Diagnosis) -> None:
        """診断結果を通常ログへ記録する（F-24。ファイル名・パスは含めない）。"""
        self.logger.info(
            "事前診断：%dページ、入力サイズ%dバイト、テキスト%s、ページ数上限%s",
            d.page_count, d.file_size_bytes, "あり" if d.has_text else "なし",
            "超過" if d.page_count_exceeds_limit else "内",
        )
        for p in d.pages:
            self.logger.info(
                "  ページ%d：%.1f×%.1fmm 対応%s 向き%s 内容%s",
                p.page_index + 1, p.width_mm, p.height_mm,
                "○" if p.is_supported_size else "×", "横" if p.is_landscape else "縦",
                p.content_type,
            )

    def _apply_defaults_to_screen(self, made_by_va_fax: bool = False) -> None:
        """既定設定（F-26）から画面の処理選択を再計算する。

        圧縮方式・用紙正規化とも既定設定の固定値を用い、文書内容や事前診断結果による
        自動推奨は行わない（F-05・F-06）。ただし、本ツールで二値化・グレースケールにして保存した
        ファイルを開いたとき（made_by_va_fax）は、二重に変換しないよう、圧縮方式の初めの値を
        「何もしない」にする（F-06）。
        """
        d = self.settings.defaults
        self._applying_defaults = True
        try:
            self._capacity_switched_for_none = False
            self.capacity_method_var.set(d.capacity_exceeded_method)
            self.mode_var.set("none" if made_by_va_fax else _POLICY_TO_MODE.get(d.compression_policy, "binarize"))
            self.normalize_var.set(d.normalize_enabled)
            self.page_numbers_var.set(d.page_numbers)
            self.dither_label_var.set(_DITHER_LABELS.get(d.dither_method, _DITHER_LABELS["auto"]))
            self.quality_label_var.set(
                _QUALITY_LABELS[_QUALITY_LEVEL_TO_INDEX.get(d.jpeg_quality_level, 1)])
            self.reduction_strategy_label_var.set(
                _REDUCTION_STRATEGY_LABELS.get(
                    d.size_reduction_strategy, _REDUCTION_STRATEGY_LABELS["culprit_only"]))
            self._update_mode_widgets()
        finally:
            self._applying_defaults = False

    def _format_diagnosis(self, d: diagnose_module.Diagnosis, source_count: int = 1) -> str:
        lines = []
        if source_count > 1:
            lines.append(f"材料のファイル数：{source_count}件")
        lines += [
            f"総ページ数：{d.page_count}ページ",
            f"文書の大きさ（変換する前の目安）：{_format_size(d.file_size_bytes)}",
            "テキストデータ：" + ("なし" if not d.has_text else
                              "あり（そのまま残ります）" if self.mode_var.get() == "none"
                              else "あり（画像化されます）"),
        ]

        color_count = sum(1 for p in d.pages if p.content_type == "color")
        mono_count = sum(1 for p in d.pages if p.content_type == "mono")
        gray_count = d.page_count - color_count - mono_count
        lines.append(f"色の判定：カラー{color_count}ページ・グレースケール{gray_count}ページ・"
                     f"白黒{mono_count}ページ")

        if self.profile.page_count_limit is not None:
            status = "上限超過（ページを削除してください）" if d.page_count_exceeds_limit else "上限内"
            lines.append(f"ページ数上限（{self.profile.page_count_limit}ページ）：{status}")
        if d.unsupported_pages:
            lines.append(self._format_unsupported_pages(d))
        else:
            lines.append("すべてのページが対応サイズです")

        return "\n".join(lines)

    def _format_unsupported_pages(self, d: diagnose_module.Diagnosis) -> str:
        """対応外サイズのページを用紙規格名ごとにまとめ、縮小率の注意を添えて表示する（F-04・F-08）。"""
        target = self.profile.normalize_target
        groups: dict[str, list[tuple[int, float]]] = {}
        for p in d.pages:
            if p.is_supported_size:
                continue
            name = p.identified_size_name or "不明な規格"
            scale = min(target.width_mm / p.width_mm, target.height_mm / p.height_mm)
            groups.setdefault(name, []).append((p.page_index + 1, scale))

        parts = []
        for name, entries in groups.items():
            nums_text = "、".join(str(page_num) for page_num, _ in entries)
            min_scale = min(scale for _, scale in entries)
            note = ""
            if min_scale < 0.999:
                note = f"。用紙正規化により約{min_scale * 100:.0f}%に縮小され、判読性が低下する場合があります"
            parts.append(f"{len(entries)}ページが{name}です（{nums_text}ページ目{note}）")

        return f"{d.page_count}ページ中" + "、".join(parts)

    def _set_diagnosis_text(self, text: str) -> None:
        self.diagnosis_text.configure(state="normal")
        self.diagnosis_text.delete("1.0", "end")
        self.diagnosis_text.insert("1.0", text)
        self.diagnosis_text.configure(state="disabled")

    def _set_status(self, text: str) -> None:
        """いちばん新しい結果（開いた・追加した・保存した等）を、1行で示す。"""
        self.status_var.set(text)

    # ------------------------------------------------------------------ 保存（F-02・F-03・F-32）

    def _can_save(self) -> bool:
        return (self.document is not None and not self._saving
                and self.diagnosis is not None and self._assembled is not None)

    def _on_save(self, then: Optional[Callable[[], object]] = None) -> None:
        """［保存］：保存の画面で場所と名前を指定して保存する（F-32）。

        保存は、この1つだけとする（Ver200 の［保存］〔上書き保存〕と［名前を付けて保存］を、
        2026-11-18 班長指示で1つにした）。既にあるファイルの名前を選んだときは、保存の画面
        （Windows）が、置き換えてよいかを確かめる（F-03）。
        """
        if not self._can_save() or not self._confirm_cover_count():
            return
        # 名前の初めの値は、開いたファイル（保存した後は、保存したファイル）の名前に「_fax」を
        # 付けたもの（［ファイル］→［オプション］で付けないこともできる。F-32・F-35）
        base = self._save_base()
        initial_dir = str(base.parent if base is not None else self._doc_dir or self._initial_open_dir())
        initial_file = suggested_save_name(base, self._name_suffix()) if base else ""
        path_str = filedialog.asksaveasfilename(
            title="保存", initialdir=initial_dir, initialfile=initial_file,
            defaultextension=".pdf", filetypes=_PDF_FILETYPES, parent=self)
        if not path_str:
            return
        target = Path(path_str)
        if any(saving_module.same_path(target, added) for added in self._added_paths):
            messagebox.showerror(
                APP_NAME,
                "追加したファイルと同じ名前では保存できません"
                "（ページの材料にしたファイルは書き換えません）。別の名前を指定してください。",
                parent=self)
            return
        self._start_save(target, then)

    def _start_save(self, target: Path, then: Optional[Callable[[], object]]) -> None:
        quality_index = _QUALITY_LABELS.index(self.quality_label_var.get())
        options = convert_module.ConvertOptions(
            mode=self.mode_var.get(),
            normalize_enabled=self.normalize_var.get(),
            dither_method=_DITHER_LABELS_REV[self.dither_label_var.get()],
            jpeg_quality=self.profile.jpeg_quality_steps[quality_index],
            page_numbers=bool(self.page_numbers_var.get()),
        )
        capacity_method = self.capacity_method_var.get()
        reduction_strategy = _REDUCTION_STRATEGY_LABELS_REV[self.reduction_strategy_label_var.get()]

        # 送付書があれば、保存の後に、その入力を「FAX送付書データ」に書く（F-36）
        cover = self._current_cover()
        self._saving_cover = None
        if cover is not None:
            cover_pages = sum(1 for ref in self.document.pages if (ref.source, ref.page) in cover.refs)
            self._saving_cover = (cover.input.copy(), cover_pages, self.document.page_count)

        self._saving = True
        self.progress_var.set(0.0)
        self.progress_bar.grid()
        self._set_status("保存しています...")
        self._set_settings_menu_enabled(False)
        self._update_command_states()

        # このワーカーはこの保存の間、常にこのプロファイルと、いまのページの表を組み立てた
        # PDFを使う（途中で画面の状態が変わっても、進行中の保存には影響しない）。
        thread = threading.Thread(
            target=self._do_save,
            args=(self._assembled, target, self.profile, options, capacity_method,
                  reduction_strategy, list(self._material_paths), then),
            daemon=True,
        )
        thread.start()

    def _do_save(self, doc_bytes: bytes, target: Path, profile: Profile,
                 options: convert_module.ConvertOptions, capacity_method: str,
                 reduction_strategy: str, material_paths: list[Path],
                 then: Optional[Callable[[], object]]) -> None:
        def on_page_done(done: int, total: int, page_log: convert_module.PageConversionLog) -> None:
            detail = f"{page_log.resolution_dpi}dpi"
            if page_log.dither_method:
                detail += f"・{_DITHER_LABELS.get(page_log.dither_method, page_log.dither_method)}"
            if page_log.jpeg_quality is not None:
                detail += f"・品質{page_log.jpeg_quality}"
            self._message_queue.put({"type": "progress", "done": done, "total": total, "detail": detail})
            self.logger.info("ページ %d/%d 処理完了（%s）", done, total, detail)

        def on_retry(page_index: int, old_size: int, new_size: int,
                     dpi: int, quality: Optional[int]) -> None:
            quality_text = f"・品質{quality}" if quality is not None else ""
            msg = (f"ページ{page_index + 1}：{old_size}バイトで容量超過→"
                   f"{dpi}dpi{quality_text}で再生成→{new_size}バイト")
            self._message_queue.put({"type": "retry", "message": msg})
            self.logger.info(msg)

        try:
            outcome = saving_module.save_document(
                doc_bytes, target, profile, options, capacity_method, reduction_strategy,
                material_paths, on_page_done=on_page_done, on_retry=on_retry)
        except saving_module.SaveError as exc:
            self.logger.info("保存エラー：%s", exc.kind)
            self._message_queue.put({"type": "save_error", "message": str(exc)})
        except size_control_module.SizeLimitExceededError as exc:
            self.logger.info("保存エラー（容量超過）：%s", exc)
            self._message_queue.put({"type": "save_error", "message": str(exc)})
        except convert_module.ConversionError as exc:
            self.logger.info("保存エラー（検証・変換）：%s", exc)
            self._message_queue.put({"type": "save_error", "message": str(exc)})
        except Exception as exc:  # 予期しない例外（F-22）：詳細は出さずログと診断情報にのみ記録
            self.logger.error("保存中に予期しないエラーが発生しました：%s", _format_exception_for_log(exc))
            self._errors.record(exc, "保存")
            self._message_queue.put({
                "type": "save_error",
                "message": f"保存中に問題が発生しました。\n{CONTACT_NOTICE}",
            })
        else:
            self.logger.info("保存完了：%d個のファイル、%dページ、合計 %d バイト",
                             len(outcome.paths), sum(outcome.page_counts), sum(outcome.sizes))
            self._message_queue.put({"type": "saved", "outcome": outcome, "target": target,
                                     "then": then, "mode": options.mode})

    def _poll_queue(self) -> None:
        try:
            while True:
                message = self._message_queue.get_nowait()
                self._handle_message(message)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _handle_message(self, message: dict) -> None:
        kind = message["type"]
        if kind == "diagnosis_error":
            if message["token"] != self._diagnosis_token:
                return  # 文書が変わっている・古い診断結果は無視する
            self.diagnosis = None
            self._assembled = None
            self._set_diagnosis_text(message["message"])
            self._update_command_states()
            messagebox.showerror(APP_NAME, message["message"], parent=self)
        elif kind == "diagnosis_done":
            if message["token"] != self._diagnosis_token:
                return  # 文書が変わっている・古い診断結果は無視する
            self.diagnosis = message["diagnosis"]
            self._assembled = message["assembled"]
            self._set_diagnosis_text(self._format_diagnosis(self.diagnosis, message["source_count"]))
            selection, active = self._pending_selection
            self.preview_pane.set_document(
                message["assembled"], message["profile"], self._current_preview_options(),
                selection=selection, active=active)
            self._update_command_states()
        elif kind == "progress":
            done, total = message["done"], message["total"]
            self.progress_var.set(done / total * 100 if total else 0)
            self._set_status(f"保存しています...（{done}／{total}ページ）")
        elif kind == "retry":
            pass  # 容量超過での作り直しの経過は、ログファイルにだけ残す（F-17）
        elif kind == "save_error":
            self._saving = False
            self._set_settings_menu_enabled(True)
            self.progress_bar.grid_remove()
            self._update_command_states()
            self._set_status("保存できませんでした。")
            messagebox.showerror(APP_NAME, message["message"], parent=self)
        elif kind == "saved":
            self._on_saved(message["outcome"], message["target"], message["then"], message["mode"])

    def _constraint_notes(self, outcome: saving_module.SaveOutcome, mode: str) -> list[str]:
        """保存したファイルが、プロファイルの制約に合っていない点（F-13・F-14）。

        容量は、容量超過時の処理方式が「何もしない」のときに超えうる。ページ数・用紙サイズは、
        圧縮方式が「何もしない」のとき（検証で止めず、保存の後に知らせる）に合わないことがある。
        """
        profile = self.profile
        notes: list[str] = []
        limit = profile.max_file_size_bytes
        if max(outcome.sizes) > limit:
            notes.append(
                f"ファイルの大きさ（{_format_size(max(outcome.sizes))}）が、{profile.display_name}の"
                f"容量上限（{_format_size(limit)}）を超えています。このままでは送信できない見込みです。")
        diagnosis = self.diagnosis
        if mode == "none" and diagnosis is not None:
            if diagnosis.page_count_exceeds_limit:
                notes.append(
                    f"ページ数（{diagnosis.page_count}ページ）が、{profile.display_name}の上限"
                    f"（{profile.page_count_limit}ページ）を超えています。")
            if diagnosis.unsupported_pages:
                pages_text = "、".join(str(n) for n in diagnosis.unsupported_pages)
                notes.append(
                    f"{profile.display_name}の対応外の用紙サイズのページがあります（{pages_text}ページ目）。")
        return notes

    def _on_saved(self, outcome: saving_module.SaveOutcome, target: Path,
                  then: Optional[Callable[[], object]], mode: str = "binarize") -> None:
        self._saving = False
        self._set_settings_menu_enabled(True)
        self.progress_var.set(100.0)
        self.progress_bar.grid_remove()
        limit_text = f"上限 {_format_size(self.profile.max_file_size_bytes)}"

        if not outcome.split:
            # 以後は、保存したファイルが、この文書の保存先になる。文書のページの表は、
            # 材料のページを指したままにする（もう一度保存するときも、材料から作り直す。F-02）。
            self._save_path = target
            self._doc_dir = target.parent
            self._dirty = False
            self._set_status(
                f"保存しました：{target.name}（{outcome.page_counts[0]}ページ、"
                f"{_format_size(outcome.sizes[0])}。{limit_text}）")
        else:
            # 分割が起きたときは、連番の付いたファイルだけを書いている。保存先のファイルは
            # 変えていないので、文書は未保存のままとする（F-13）。
            self._set_status(
                f"{len(outcome.paths)}個のファイルに分けて保存しました"
                f"（合計{sum(outcome.page_counts)}ページ。{limit_text}）。")

        # プレビューを、実際に保存したPDFの表示に切り替える（処理内容の変更・ページの編集で、
        # 変換イメージの表示に戻る。F-30）。分割のときは、最初のファイルを表示する。
        self.preview_pane.show_converted_result(outcome.first_pdf)
        self._update_title()
        self._update_command_states()

        self._write_cover_input(target)

        notes = self._constraint_notes(outcome, mode)
        if notes:
            self.logger.info("保存したファイルが制約に合わない点：%d件", len(notes))
            messagebox.showwarning(
                APP_NAME, "保存しましたが、次の点が送信の制約に合っていません。\n\n"
                + "\n".join(f"・{note}" for note in notes), parent=self)

        # 保存の後に、保存したフォルダを開く（［ファイル］→［オプション］。F-35）
        open_folder = self.settings.open_folder_after_save
        if open_folder:
            self._open_folder(outcome.paths[0].parent)

        if outcome.split:
            names = "\n".join(f"・{path.name}" for path in outcome.paths)
            note = (f"\n\n「{target.name}」は変えていません。" if target.exists()
                    else "")
            choice = self._notify_split(
                f"容量の上限（{_format_size(self.profile.max_file_size_bytes)}）を超えるため、"
                f"{len(outcome.paths)}個のファイルに分けて保存しました。\n{names}{note}",
                offer_folder=not open_folder)
            if choice == "open":
                self._open_folder(outcome.paths[0].parent)
        elif then is not None:
            then()

    def _notify_split(self, text: str, offer_folder: bool = True) -> Optional[str]:
        buttons = [("フォルダを開く", "open", False)] if offer_folder else []
        return ask_choice(self, "保存の完了", text, buttons + [("閉じる", "close", True)],
                          cancel_value="close")

    def _open_folder(self, folder: Path) -> None:
        try:
            _open_folder(folder)
        except OSError:
            messagebox.showerror(
                APP_NAME, "フォルダを開けませんでした。保存先のフォルダを直接開いてください。", parent=self)

    # ------------------------------------------------------------------ 送付書（F-36）

    def _current_cover(self) -> Optional[CoverRecord]:
        """いまの文書にある送付書（いちばん新しく入れたもの。なければNone）。"""
        if self.document is None:
            return None
        present = {(ref.source, ref.page) for ref in self.document.pages}
        for record in reversed(self._covers):
            if record.refs & present:
                return record
        return None

    def _cover_refs(self) -> set:
        """これまでに入れた送付書のページ（（材料の番号, ページ）の組）すべて。"""
        refs: set = set()
        for record in self._covers:
            refs |= record.refs
        return refs

    def _material_items(self) -> list[tuple[str, int]]:
        """書類の一覧の材料：（ファイルの名前〔拡張子を除く〕, いまの文書に残っているページ数）を、
        ページの表で最初に現れる順に並べたもの（送付書のページは除く）。"""
        cover_refs = self._cover_refs()
        counts: dict[int, int] = {}
        for ref in self.document.pages:
            if (ref.source, ref.page) in cover_refs:
                continue
            counts[ref.source] = counts.get(ref.source, 0) + 1
        items = []
        for source, count in counts.items():
            path = self._source_paths[source] if source < len(self._source_paths) else None
            if path is not None:
                items.append((path.stem, count))
        return items

    def _cover_documents(self) -> list[cover_module.DocItem]:
        return cover_module.documents_from_materials(self._material_items())

    def _cover_initial(self, rebuild_documents: bool = False) -> cover_module.CoverInput:
        """入力の画面の初めの値（F-36の4）。"""
        documents = self._cover_documents()
        record = self._current_cover()
        previous = record.input.copy() if record is not None else None
        if previous is not None and rebuild_documents:
            previous.documents = documents
        folder_latest = None
        mints = None
        if previous is None:
            base = self._save_base()
            folder = base.parent if base is not None else self._doc_dir
            if folder is not None:
                folder_latest = cover_module.latest_input_in_folder(folder, exclude=self._save_path)
            if folder_latest is None:
                mints = cover_module.read_mints_header(p for p in self._source_paths if p is not None)
        return cover_module.initial_input(date.today(), documents, previous, folder_latest, mints)

    def _on_cover(self, rebuild_documents: bool = False) -> None:
        """［送付書］：入力の画面を開き、送付書を作って文書の先頭に入れる（前の送付書は差し替える）。"""
        if not self._can_edit():
            return
        dialog = CoverSheetDialog(self, self._cover_initial(rebuild_documents), self.settings.cover_sender,
                                  self._cover_documents, bring_to_front=_bring_to_front)
        self.wait_window(dialog)
        result = dialog.result
        del dialog
        _collect_closed_window()
        if result is None or not self._can_edit():
            return
        self._put_cover(result)

    def _on_page_double_click(self, index: int) -> None:
        """ページのダブルクリック：送付書のページなら、入力の画面を開く（F-36。2026-11-18 班長指示）。"""
        record = self._current_cover()
        if record is None or not self._can_edit() or not 0 <= index < self.document.page_count:
            return
        ref = self.document.pages[index]
        if (ref.source, ref.page) in record.refs:
            self._on_cover()

    def _put_cover(self, cover: cover_module.CoverInput) -> bool:
        """送付書を作って、文書の先頭に入れる（前の送付書は取り除く）。"""
        document = self.document
        replacing = self._cover_refs()
        other_pages = sum(1 for ref in document.pages if (ref.source, ref.page) not in replacing)
        try:
            data, cover_pages, total = cover_module.build_cover(
                cover, self.settings.cover_sender, other_pages, self._paper_mm())
            source = document_module.load_source(data, self._paper_mm())
        except Exception as exc:  # 予期しない例外（F-22）：入力の中身はログに書かない（F-24）
            self.logger.error("送付書の作成で予期しないエラー：%s", _format_exception_for_log(exc))
            self._errors.record(exc, "送付書の作成")
            messagebox.showerror(APP_NAME, f"送付書を作れませんでした。\n{CONTACT_NOTICE}", parent=self)
            return False
        added = document.put_front(source, replacing)
        self._source_paths += [None] * (len(document.sources) - len(self._source_paths))
        index = len(document.sources) - 1
        self._covers.append(CoverRecord(frozenset((index, i) for i in range(cover_pages)), cover, total))
        self.logger.info("送付書を作成：%dページ（送信枚数%d）", cover_pages, total)
        self._set_status(f"送付書を作り、先頭に入れました（{cover_pages}ページ。送信枚数 本書を含め{total}枚）。")
        self._edited(added, 0)
        return True

    def _confirm_cover_count(self) -> bool:
        """保存の前に、送付書の送信枚数と、文書のページ数を比べる（F-36の7）。保存を続けるならTrue。"""
        record = self._current_cover()
        if record is None or record.total is None or record.total == self.document.page_count:
            return True
        choice = ask_choice(
            self, "送信枚数の確認",
            f"送付書に書いた送信枚数（{record.total}枚）が、いまの文書のページ数"
            f"（{self.document.page_count}ページ）と合いません。\n"
            "送付書を作った後に、ページを追加・削除したためです。\n\n"
            "［送付書を直す］では、入力の画面を開きます（書類の一覧は、いまの文書から作り直します）。",
            [("送付書を直す", "fix", True), ("このまま保存", "save", False), ("キャンセル", "cancel", False)],
            cancel_value="cancel")
        if choice == "fix":
            self._on_cover(rebuild_documents=True)
            return False
        return choice == "save"

    def _write_cover_input(self, target: Path) -> None:
        """保存した文書に送付書があれば、その入力を「FAX送付書データ」に書く（F-36の5）。"""
        saving_cover, self._saving_cover = self._saving_cover, None
        if saving_cover is None:
            return
        cover, cover_pages, total = saving_cover
        try:
            cover_module.save_input(target, cover, cover_pages, total)
        except OSError as exc:
            self.logger.info("送付書の入力を保存できない：%s", type(exc).__name__)
            messagebox.showwarning(
                APP_NAME, "PDFは保存しましたが、送付書の入力を保存できませんでした"
                f"（「{cover_module.DATA_DIRNAME}」フォルダに書き込めません）。\n"
                "次にこのファイルを開いたときは、送付書の入力の初めの値が空になります。", parent=self)

    # ------------------------------------------------------------------ オプション（F-35）

    def _on_options(self) -> None:
        """［ファイル］→［オプション］の画面を開き、保存されたら設定に書いて画面へ反映する（F-35）。"""
        if self._saving:
            return
        dialog = OptionsDialog(self, self.settings, self.profile.id)
        self.wait_window(dialog)
        result = dialog.result
        del dialog
        _collect_closed_window()
        if result is None:
            return
        self._apply_options(result)

    def _apply_options(self, result: dict) -> None:
        # ---- FAXサービス（F-28）。カスタムは同じidのまま内容だけが変わりうるので、入力したら読み直す。
        if result["custom_profile"] is not None:
            save_custom_profile(result["custom_profile"])
        profile_changed = result["profile_id"] != self.profile.id or result["custom_profile"] is not None
        if profile_changed:
            try:
                self.profile = load_all_profiles()[result["profile_id"]]
            except (KeyError, ProfileError):
                profile_changed = False
            else:
                self.settings.profile_id = self.profile.id
                self.profile_label_var.set(_profile_summary(self.profile))
        # ---- 処理内容の既定値（F-26）。変えたときだけ、画面の処理内容を既定値に戻す。
        defaults_changed = result["defaults"] != self.settings.defaults
        self.settings.defaults = result["defaults"]
        # ---- 保存（F-35）
        self.settings.append_fax_to_save_name = result["append_suffix"]
        self.settings.save_name_suffix = result["suffix"]
        self.settings.open_folder_after_save = result["open_folder_after_save"]
        self.settings.cover_sender = result["cover_sender"]
        save_settings(self.settings)

        if defaults_changed:
            made_by_va_fax = self.document is not None and self.document.all_made_by_va_fax()
            self._apply_defaults_to_screen(made_by_va_fax=made_by_va_fax)
        self._update_option_visibility()
        if profile_changed and self.document is not None:
            self._document_changed()
        self._update_title()   # 名前のない文書の題名（保存するときの名前）が変わりうる
