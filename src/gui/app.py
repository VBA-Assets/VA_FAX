# -*- coding: utf-8 -*-
"""VA FAX のtkinter GUI（F-15〜F-19・F-28）。

本モジュールはエンジン（core.diagnose / core.convert / core.size_control）を呼び出すのみで、
変換ロジック自体は持たない（GUIとエンジンの分離）。
"""
from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
import traceback
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from tkinterdnd2 import DND_FILES, TkinterDnD

from config.profiles import PageSize, Profile, ProfileError, load_all_profiles, save_custom_profile
from config.settings import (
    ConversionDefaults,
    OutputPathError,
    Settings,
    compute_default_output_path,
    save_settings,
    validate_output_path,
)
from core import convert as convert_module
from core import diagnose as diagnose_module
from core import merge as merge_module
from core import size_control as size_control_module
from core.size_control import format_bytes_as_size as _format_size
from gui import theme
from gui.preview import PreviewPane
from logging_setup import setup_logger
from report import ReportData, save_report

CONTACT_NOTICE = "問題が解決しない場合は、VBAアセット（vba-assets.net）のお問い合わせ窓口までご連絡ください。"

_INPUT_PLACEHOLDER_TEXT = (
    "ここにファイルをドラッグ＆ドロップ、または「入力元」で選択\n"
    "（複数可・上から順に結合。並べ替え・削除は行をクリックして選んでから）"
)
_PLACEHOLDER_COLOR = theme.COLOR_PLACEHOLDER

# 一度の操作（ドロップ・ダイアログ選択・貼り付け）でこの件数を超えて追加する場合は
# 確認ダイアログを挟む（フォルダ内の大量PDFを誤ってまとめてドロップした場合等の
# 歯止め。件数のハード上限は設けない。サービス制約値ではないUI上の設計値のため
# プロファイル〔F-29〕の対象外）。
CONFIRM_INPUT_FILES_THRESHOLD = 10

# 入力ファイル一覧のCtrl+Zで遡れる履歴の最大数（メモリはパスのリストのみで軽微だが、
# 無制限には伸ばさない）。
INPUT_UNDO_LIMIT = 50

# 入力ファイル欄での行のドラッグによる並べ替え（F-15）の調整値。
# 押下位置からこのピクセル数を超えて縦に動いた時点で「並べ替えのドラッグ」と判定する
# （クリックのつもりの微小な動きで並べ替えが始まらないようにするため）。
_INPUT_DRAG_THRESHOLD_PX = 3
# 欄の上端・下端からこのピクセル数以内にポインタがある間は自動スクロールする
# （欄は3行表示のため、表示外の位置へも移動できるようにする）。
_INPUT_DRAG_EDGE_PX = 12
_INPUT_DRAG_SCROLL_INTERVAL_MS = 100
# 欄の縁からこのピクセル数を超えて外へ持ち出して放した場合は、その行を一覧から
# 取り除く。縁ぎりぎりでの取りこぼし（末尾へ移動しようとして少しはみ出した場合等）を
# 削除と誤判定しないための余裕であり、自動スクロールの反応域より広く取る。
_INPUT_DRAG_OUTSIDE_MARGIN_PX = 16

# 入力ファイル欄（tk.Text）で選択行を動かすキー。これ以外の文字キーは一切
# 受け付けない（A-2：表示中の一覧と実際の変換対象の食い違い防止）。左右キーは
# 行単位の一覧では意味を持たないため対象外とする。
_INPUT_SELECTION_MOVE_KEYSYMS = frozenset({
    "Up", "Down", "Home", "End", "Prior", "Next",
})
# キーイベントのevent.stateで修飾キーを表すビット。Ctrlはどの環境でも0x4。
# MacのTk（aqua）ではCommand（⌘）がMod1（0x8）になる（WindowsのTkでは0x8は
# NumLockを表すため、Mac以外では見ない）。
_CONTROL_STATE_MASK = 0x4
_MAC_COMMAND_STATE_MASK = 0x8

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


def _parse_dropped_paths(data: str) -> list[str]:
    """ドロップされたパス一覧を解析する。

    tkinterdnd2の慣例であるtk.splitlist()はTclの文字列としてバックスラッシュを
    エスケープ解釈するため、Windowsのパス（円記号区切り）を壊してしまう。
    そのため波括弧（空白を含むパス）と空白区切りのみで単純に分割する。
    """
    return [braced if braced else bare for braced, bare in _DND_PATH_PATTERN.findall(data)]


def _move_input_paths(paths: list[Path], start: int, end: int,
                      drop_index: int) -> tuple[list[Path], int]:
    """一覧の[start, end]（0始まり・両端含む）をdrop_indexの位置へ移動する（F-15の並べ替え）。

    drop_indexは「行と行の境界」を表し、0が先頭の前、len(paths)が末尾の後を指す
    （移動前の一覧における位置で与える）。戻り値は並べ替え後の一覧と、その中での
    移動した塊の先頭位置（移動後にその範囲を選択状態にするために使う）。
    """
    block = paths[start:end + 1]
    rest = paths[:start] + paths[end + 1:]
    # drop_indexより前から抜き取った件数だけ、挿入位置が手前にずれる。
    if drop_index <= start:
        removed_before = 0
    elif drop_index > end:
        removed_before = len(block)
    else:
        removed_before = drop_index - start
    insert_at = drop_index - removed_before
    return rest[:insert_at] + block + rest[insert_at:], insert_at


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


def _split_output_paths(base_path: Path, count: int) -> list[Path]:
    """分割時の出力ファイル名を決定する（例：foo_fax.pdf → foo_fax_1of3.pdf）。

    count==1（分割不要）の場合は、元のパスをそのまま返す（F-02の単一ファイル出力を
    既定の見た目として維持する。命名変更が発生するのは実際に分割された場合のみ）。
    """
    if count == 1:
        return [base_path]
    stem, suffix = base_path.stem, base_path.suffix
    return [base_path.with_name(f"{stem}_{i + 1}of{count}{suffix}") for i in range(count)]


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


class ProfileChoiceDialog(tk.Toplevel):
    """FAXサービス（プロファイル）選択ダイアログ（F-28）。"""

    def __init__(self, master: Optional[tk.Misc], current_profile_id: Optional[str] = None):
        super().__init__(master)
        self.title("お使いのFAXサービスを選んでください")
        self.resizable(False, False)
        self.result_profile_id: Optional[str] = None

        self.profiles = load_all_profiles()
        ordered_ids = ["motfax", "movfax", "messageplus", "sokusoku_fax", "efax", "jfax", "custom"]
        self._ids = [pid for pid in ordered_ids if pid in self.profiles] + [
            pid for pid in self.profiles if pid not in ordered_ids
        ]

        ttk.Label(self, text="お使いのインターネットFAXサービスを選択してください。",
                  anchor="w", justify="left").pack(fill="x", padx=12, pady=(12, 6))

        # current_profile_idが不正（存在しないid）な場合、どのラジオボタンにも一致せず
        # 未選択のまま「決定」で無効なidが返ってしまうため、先頭を既定選択にフォールバックする。
        initial_id = current_profile_id if current_profile_id in self.profiles else self._ids[0]
        self._var = tk.StringVar(value=initial_id)
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=12)

        for pid in self._ids:
            profile = self.profiles[pid]
            frame = ttk.Frame(body)
            frame.pack(fill="x", pady=3, anchor="w")
            ttk.Radiobutton(frame, text=_profile_constraint_text(profile), variable=self._var,
                            value=pid).pack(anchor="w")
            if profile.reference_notice:
                ttk.Label(frame, text=profile.reference_notice, foreground=theme.COLOR_WARNING,
                          anchor="w", justify="left", wraplength=460).pack(anchor="w", padx=(20, 0))

        button_row = ttk.Frame(self)
        button_row.pack(fill="x", padx=12, pady=12)
        ok_button = theme.create_button(
            button_row, accent=True, text="決定", width=10, command=self._on_ok)
        ok_button.pack(side="right")

        self.transient(master)
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


class DefaultsDialog(tk.Toplevel):
    """変換の既定設定ダイアログ（F-26）。保存時はresultにConversionDefaultsを設定する。"""

    def __init__(self, master: tk.Misc, current: ConversionDefaults):
        super().__init__(master)
        self.title("変換の既定設定")
        self.resizable(False, False)
        self.result: Optional[ConversionDefaults] = None

        self._folder_policy_var = tk.StringVar(value=current.output_folder_policy)
        self._fixed_folder_var = tk.StringVar(value=current.fixed_output_folder or "")
        self._name_policy_var = tk.StringVar(value=current.output_name_policy)
        self._compression_var = tk.StringVar(value=current.compression_policy)
        self._normalize_var = tk.BooleanVar(value=current.normalize_enabled)
        self._dither_label_var = tk.StringVar(
            value=_DITHER_LABELS.get(current.dither_method, _DITHER_LABELS["auto"]))
        self._quality_label_var = tk.StringVar(
            value=_QUALITY_LABELS[_QUALITY_LEVEL_TO_INDEX.get(current.jpeg_quality_level, 1)])
        self._capacity_method_var = tk.StringVar(value=current.capacity_exceeded_method)
        self._reduction_strategy_var = tk.StringVar(
            value=_REDUCTION_STRATEGY_LABELS.get(
                current.size_reduction_strategy, _REDUCTION_STRATEGY_LABELS["culprit_only"]))
        self._report_var = tk.BooleanVar(value=current.report_enabled)

        out = ttk.Labelframe(self, text="出力先")
        out.pack(fill="x", padx=12, pady=(12, 6))
        ttk.Radiobutton(out, text="入力ファイルと同じフォルダ", variable=self._folder_policy_var,
                        value="same_as_input", command=self._update_states).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(4, 0))
        ttk.Radiobutton(out, text="固定フォルダ：", variable=self._folder_policy_var,
                        value="fixed", command=self._update_states).grid(
            row=1, column=0, sticky="w", padx=8)
        self._fixed_entry = ttk.Entry(out, textvariable=self._fixed_folder_var, width=38)
        self._fixed_entry.grid(row=1, column=1, sticky="we", padx=(0, 4))
        self._fixed_button = theme.create_button(
            out, text="フォルダ選択...", command=self._on_browse_folder)
        self._fixed_button.grid(row=1, column=2, sticky="w", padx=(0, 8))

        ttk.Label(out, text="出力ファイル名：").grid(row=2, column=0, sticky="w", padx=8, pady=(6, 0))
        ttk.Radiobutton(out, text="入力ファイル名＋「_fax」", variable=self._name_policy_var,
                        value="append_fax").grid(row=3, column=0, columnspan=3, sticky="w", padx=24)
        self._same_name_radio = ttk.Radiobutton(
            out, text="入力ファイル名と同じ", variable=self._name_policy_var, value="same_as_input")
        self._same_name_radio.grid(row=4, column=0, columnspan=3, sticky="w", padx=24)
        ttk.Label(out, text="「入力ファイルと同じフォルダ」の場合、入力ファイルの上書きとなるため\n"
                           "「入力ファイル名と同じ」は選択できません。",
                  foreground=theme.COLOR_MUTED, justify="left").grid(
            row=5, column=0, columnspan=3, sticky="w", padx=24, pady=(0, 6))

        opt = ttk.Labelframe(self, text="変換オプションの既定値")
        opt.pack(fill="x", padx=12, pady=6)

        # 選択肢の数が少ない項目から順に並べる：圧縮方式→容量超過→用紙正規化→変換レポート。
        # 配置・文言はメイン画面の処理内容選択欄（_build_options）と同一にする。ただし
        # 各選択肢の副作用を説明する「※」注記は、既定値を機械的に設定するだけの本画面
        # では判断材料として不要なため省く。
        ttk.Label(opt, text="圧縮方式：").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(4, 0))

        ttk.Radiobutton(opt, text="二値化（白黒・G4圧縮）", variable=self._compression_var,
                        value="always_binarize", command=self._update_compression_states).grid(
            row=1, column=0, sticky="w", padx=8)

        ttk.Label(opt, text="　ディザ方式：").grid(row=2, column=0, sticky="e", padx=(24, 0))
        self._dither_combo = ttk.Combobox(
            opt, textvariable=self._dither_label_var, state="readonly", width=10,
            values=list(_DITHER_LABELS.values()))
        self._dither_combo.grid(row=2, column=1, sticky="w")

        ttk.Radiobutton(opt, text="グレースケール（JPEG圧縮）", variable=self._compression_var,
                        value="always_grayscale", command=self._update_compression_states).grid(
            row=3, column=0, sticky="w", padx=8)

        ttk.Label(opt, text="　JPEG品質：").grid(row=4, column=0, sticky="e", padx=(24, 0))
        self._quality_combo = ttk.Combobox(
            opt, textvariable=self._quality_label_var, state="readonly", width=6,
            values=_QUALITY_LABELS)
        self._quality_combo.grid(row=4, column=1, sticky="w")

        ttk.Label(opt, text="容量超過時の処理方式：").grid(
            row=5, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 0))

        self._reduce_resolution_radio = ttk.Radiobutton(
            opt, text="解像度・品質を下げて再実行", variable=self._capacity_method_var,
            value="reduce_resolution", command=self._update_strategy_combo_state)
        self._reduce_resolution_radio.grid(row=6, column=0, sticky="w", padx=8)

        ttk.Label(opt, text="　削減方式：").grid(row=7, column=0, sticky="e", padx=(24, 0))
        self._strategy_combo = ttk.Combobox(
            opt, textvariable=self._reduction_strategy_var, state="readonly", width=12,
            values=list(_REDUCTION_STRATEGY_LABELS.values()))
        self._strategy_combo.grid(row=7, column=1, sticky="w")

        self._split_radio = ttk.Radiobutton(
            opt, text="分割して再実行", variable=self._capacity_method_var,
            value="split", command=self._update_strategy_combo_state)
        self._split_radio.grid(row=8, column=0, sticky="w", padx=8)

        tk.Checkbutton(
            opt, text="用紙正規化（用紙サイズ・方向が対応外のページを自動で修正します）",
            variable=self._normalize_var,
        ).grid(row=9, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 0))

        tk.Checkbutton(
            opt, text="変換レポートを出力先フォルダに保存する",
            variable=self._report_var,
        ).grid(row=10, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 4))

        button_row = ttk.Frame(self)
        button_row.pack(fill="x", padx=12, pady=12)
        save_button = theme.create_button(
            button_row, accent=True, text="保存", width=10, command=self._on_save)
        save_button.pack(side="right")
        theme.create_button(
            button_row, text="キャンセル", width=10, command=self.destroy).pack(
            side="right", padx=(0, 6))

        self._update_states()
        self._update_strategy_combo_state()
        self._update_compression_states()
        self.transient(master)
        self.grab_set()
        _bring_to_front(self)

    def _update_compression_states(self) -> None:
        """ディザ方式・JPEG品質は、対応する圧縮方式を選択している場合のみ選択可能とする。"""
        is_binarize = self._compression_var.get() == "always_binarize"
        self._dither_combo.configure(state="readonly" if is_binarize else "disabled")
        self._quality_combo.configure(state="disabled" if is_binarize else "readonly")

    def _update_strategy_combo_state(self) -> None:
        """削減方式は、「解像度を下げて再実行」を選択している場合のみ選択可能とする。"""
        enabled = self._capacity_method_var.get() == "reduce_resolution"
        self._strategy_combo.configure(state="readonly" if enabled else "disabled")

    def _update_states(self) -> None:
        """固定フォルダ欄の活性と、禁止組合せ（同一フォルダ×同一名）の選択不可を制御する。"""
        fixed = self._folder_policy_var.get() == "fixed"
        state = "normal" if fixed else "disabled"
        self._fixed_entry.configure(state=state)
        self._fixed_button.configure(state=state)
        if fixed:
            self._same_name_radio.configure(state="normal")
        else:
            if self._name_policy_var.get() == "same_as_input":
                self._name_policy_var.set("append_fax")
            self._same_name_radio.configure(state="disabled")

    def _on_browse_folder(self) -> None:
        path = filedialog.askdirectory(title="固定出力フォルダを選択", parent=self)
        if path:
            self._fixed_folder_var.set(path)

    def _on_save(self) -> None:
        folder_policy = self._folder_policy_var.get()
        fixed = self._fixed_folder_var.get().strip() or None
        if folder_policy == "fixed":
            if not fixed:
                messagebox.showerror("VA FAX", "固定フォルダを指定してください。", parent=self)
                return
            if not Path(fixed).is_dir():
                proceed = messagebox.askyesno(
                    "VA FAX",
                    "指定されたフォルダが現在見つかりません（共有フォルダ未接続の可能性があります）。\n"
                    "このまま保存しますか？", parent=self)
                if not proceed:
                    return

        name_policy = self._name_policy_var.get()
        if folder_policy == "same_as_input" and name_policy == "same_as_input":
            name_policy = "append_fax"  # UI上は選択不可だが念のため矯正（F-02・F-03）

        self.result = ConversionDefaults(
            output_folder_policy=folder_policy,
            fixed_output_folder=fixed,
            output_name_policy=name_policy,
            compression_policy=self._compression_var.get(),
            normalize_enabled=self._normalize_var.get(),
            dither_method=_DITHER_LABELS_REV[self._dither_label_var.get()],
            jpeg_quality_level=_QUALITY_INDEX_TO_LEVEL[
                _QUALITY_LABELS.index(self._quality_label_var.get())],
            capacity_exceeded_method=self._capacity_method_var.get(),
            size_reduction_strategy=_REDUCTION_STRATEGY_LABELS_REV[self._reduction_strategy_var.get()],
            report_enabled=self._report_var.get(),
        )
        self.destroy()


class VaFaxApp(TkinterDnD.Tk):
    """VA FAX メインウィンドウ。"""

    def __init__(self, settings: Settings, profile: Profile):
        super().__init__()
        theme.apply_flat_theme(self)
        theme.apply_app_icon(self)
        self.settings = settings
        self.profile = profile
        self.logger = setup_logger()

        self.input_paths: list[Path] = []
        self._input_undo_stack: list[list[Path]] = []
        # 入力ファイル欄の行選択の状態（1始まりの行番号。Noneは未選択）。
        # _sel_anchorは選択の起点、_sel_activeはShift＋クリック・矢印キーで動かす側の端。
        self._sel_anchor: Optional[int] = None
        self._sel_active: Optional[int] = None
        # 入力ファイル欄の行ドラッグ（並べ替え）の状態。_drag_blockは移動対象の行範囲
        # （1始まり・両端含む）で、Noneはドラッグ操作中でないことを表す。
        self._drag_block: Optional[tuple[int, int]] = None
        self._drag_press_line = 1
        self._shift_selecting = False
        self._drag_origin_x = 0
        self._drag_origin_y = 0
        self._drag_pointer_x = 0
        self._drag_pointer_y = 0
        self._dragging = False
        self._drag_scroll_after_id: Optional[str] = None
        self.diagnosis: Optional[diagnose_module.Diagnosis] = None
        self._diagnosed_input_count = 0
        self._message_queue: "queue.Queue[dict]" = queue.Queue()
        self._last_output_dir: Optional[Path] = None
        self._converting = False
        self._diagnosis_token = 0
        # 入力ファイル欄でCtrl併用のキー（Ctrl+A等）と判定するevent.stateのビット。
        # Mac（N-03）ではCommand（⌘。TkではMod1＝0x8）でも同じ働きにする。
        self._shortcut_state_mask = _CONTROL_STATE_MASK
        if theme.is_aqua(self):
            self._shortcut_state_mask |= _MAC_COMMAND_STATE_MASK

        self.title("VA FAX")
        self.resizable(True, True)
        self.minsize(900, 620)
        self.configure(bg=theme.COLOR_BG)

        self._build_variables()
        self._build_menu()
        self._build_widgets()
        self._bind_preview_option_traces()
        # Ctrl+Zは、メイン画面内のどこにフォーカスがあっても入力ファイル一覧の
        # 取り消しとして機能させる（プレビュー欄のクリック等でフォーカスが入力欄の
        # 外に移っていても効くように）。大文字Zはキャップスロック中の取りこぼし対策。
        self.bind_all("<Control-z>", self._on_global_undo, add="+")
        self.bind_all("<Control-Z>", self._on_global_undo, add="+")
        if theme.is_aqua(self):
            self._bind_mac_keys()
        self._poll_queue()
        _bring_to_front(self)

    # ------------------------------------------------------------------ 初期化

    def _build_variables(self) -> None:
        d = self.settings.defaults
        self.profile_label_var = tk.StringVar(value=_profile_summary(self.profile))
        self.output_path_var = tk.StringVar()
        self.normalize_var = tk.BooleanVar(value=d.normalize_enabled)
        self.mode_var = tk.StringVar(
            value="grayscale" if d.compression_policy == "always_grayscale" else "binarize")
        self.dither_label_var = tk.StringVar(
            value=_DITHER_LABELS.get(d.dither_method, _DITHER_LABELS["auto"]))
        self.quality_label_var = tk.StringVar(
            value=_QUALITY_LABELS[_QUALITY_LEVEL_TO_INDEX.get(d.jpeg_quality_level, 1)])
        self.capacity_method_var = tk.StringVar(value=d.capacity_exceeded_method)
        self.reduction_strategy_label_var = tk.StringVar(
            value=_REDUCTION_STRATEGY_LABELS.get(
                d.size_reduction_strategy, _REDUCTION_STRATEGY_LABELS["culprit_only"]))
        self.report_var = tk.BooleanVar(value=d.report_enabled)
        self.progress_var = tk.DoubleVar(value=0.0)

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)

        settings_menu = tk.Menu(menubar, tearoff=False)
        settings_menu.add_command(label="FAXサービスを変更...", command=self._on_change_profile)
        settings_menu.add_command(label="変換の既定設定...", command=self._on_open_defaults)
        menubar.add_cascade(label="設定", menu=settings_menu)

        self.config(menu=menubar)
        self._settings_menu = settings_menu

    def _set_settings_menu_enabled(self, enabled: bool) -> None:
        """変換実行中はプロファイル・既定設定の変更を禁止する（B-1。容量判定等の途中すり替わり防止）。"""
        state = "normal" if enabled else "disabled"
        self._settings_menu.entryconfig(0, state=state)
        self._settings_menu.entryconfig(1, state=state)

    def _build_widgets(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        # プレビュー欄（左側・F-30）とコントロール一式（右側）をPanedWindowで分割し、
        # 境界のドラッグでプレビュー欄の幅を変更できるようにする（リサイズ可能）。
        # ttk.Panedwindowはadd()がweight指定のみでminsize/width/stretchに対応しない
        # （初期幅の挙動が変わる）ため、classic tk.PanedWindowのまま残す。色はbg指定が
        # そのまま確実に反映されるため、Buttonで生じたような表示崩れの懸念はない。
        self._paned = tk.PanedWindow(
            self, orient="horizontal", sashrelief="flat", sashwidth=6,
            bg=theme.COLOR_BORDER, bd=0)
        self._paned.grid(row=0, column=0, sticky="nsew")

        self.preview_pane = PreviewPane(self._paned)
        controls_frame = ttk.Frame(self._paned)
        controls_frame.columnconfigure(1, weight=1)
        controls_frame.rowconfigure(2, weight=1)
        controls_frame.rowconfigure(6, weight=1)

        self._paned.add(self.preview_pane, minsize=220, width=480, stretch="always")
        self._paned.add(controls_frame, minsize=520, width=680, stretch="always")

        pad = {"padx": 10, "pady": 4}
        label_pad = {"padx": (10, 2), "pady": 4}
        field_pad = {"padx": (2, 10), "pady": 4}

        ttk.Label(controls_frame, textvariable=self.profile_label_var, font=("", 10, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", **pad)

        ttk.Label(controls_frame, text="入力ファイル：").grid(row=1, column=0, sticky="nw", **label_pad)
        # 複数ファイル対応（F-01）：1行1ファイルで表示するスクロール可能なテキスト欄。
        # 直接編集（文字の挿入・部分削除）は許可しない（A-2）：許すと、表示中のパスと
        # 実際に変換されるself.input_pathsが食い違い、誤ったファイルが変換される危険が
        # あるため。追加は「入力元」ボタンとドラッグ＆ドロップに限定し、削除は
        # Delete／BackSpaceキーによる行単位の削除のみを受け付ける（_on_input_text_key）。
        input_frame = ttk.Frame(controls_frame)
        input_frame.grid(row=1, column=1, sticky="we", **field_pad)
        input_frame.columnconfigure(0, weight=1)
        # 操作対象は「選択中の行」（帯状の反転表示）で示すため、点滅する挿入カーソルは
        # 表示しない（insertwidth=0）。文字単位の入力位置には意味がないため。
        input_text = tk.Text(input_frame, height=3, width=55, wrap="none",
                             font="TkDefaultFont", insertwidth=0)
        input_scroll = ttk.Scrollbar(input_frame, orient="vertical", command=input_text.yview)
        input_text.configure(yscrollcommand=input_scroll.set)
        input_text.grid(row=0, column=0, sticky="we")
        input_scroll.grid(row=0, column=1, sticky="ns")

        input_text.drop_target_register(DND_FILES)
        input_text.dnd_bind("<<Drop>>", self._on_drop_input_file)
        input_text.bind("<Key>", self._on_input_text_key)
        # 行のマウスドラッグは結合順の並べ替えとする（F-15）。Textの既定動作である
        # ドラッグによる範囲選択はShiftキー併用時のみ（Shift+クリック・Shift+ドラッグ）
        # に残す。ファイル一覧としては「つかんで動かす＝順番の変更」の方が直感的なため。
        input_text.bind("<ButtonPress-1>", self._on_input_drag_press)
        input_text.bind("<B1-Motion>", self._on_input_drag_motion)
        input_text.bind("<B1-Leave>", self._on_input_drag_leave)
        input_text.bind("<ButtonRelease-1>", self._on_input_drag_release)
        input_text.bind("<Escape>", self._on_input_escape)
        # 挿入位置を示す線（ドラッグ中のみ欄の上に重ねて表示する）。
        self._drop_indicator = tk.Frame(
            input_frame, height=2, bd=0, highlightthickness=0, bg=theme.COLOR_ACCENT)
        # コピー・カット・ペーストは行単位（1行1ファイル）の操作として自前で処理する
        # （並べ替え用途。仮想イベントは同一バインドタグ内で<Key>より優先されるため、
        # <Key>側ではなくここで実処理に接続する）。
        input_text.bind("<<Copy>>", self._on_input_copy)
        input_text.bind("<<Cut>>", self._on_input_cut)
        input_text.bind("<<Paste>>", self._on_input_paste)
        input_text.bind("<<Undo>>", self._on_input_undo)
        # 上記以外の編集経路（中クリック貼り付け・メニュー由来の仮想イベント）は遮断する。
        input_text.bind("<ButtonRelease-2>", lambda _e: "break")
        for sequence in ("<<PasteSelection>>", "<<Clear>>", "<<Redo>>"):
            input_text.bind(sequence, lambda _e: "break")

        theme.create_button(controls_frame, text="入力元...", command=self._browse_input).grid(
            row=1, column=2, sticky="nw", **pad)

        self.input_text = input_text
        self._input_text_default_fg = input_text.cget("foreground")
        self._input_text_default_cursor = input_text.cget("cursor")
        # 欄の外へ持ち出している間に表示するカーソル（ゴミ箱。同梱の.curを使う）
        self._remove_cursor = theme.resolve_remove_cursor(input_text)
        self._refresh_input_text()

        ttk.Label(controls_frame, text="事前診断結果：").grid(row=2, column=0, sticky="nw", **label_pad)
        self.diagnosis_text = tk.Text(controls_frame, height=7, width=64, state="disabled", wrap="word")
        self.diagnosis_text.grid(row=2, column=1, columnspan=2, sticky="nswe", **field_pad)

        options = ttk.Labelframe(controls_frame, text="処理内容の選択（変換前にご確認ください）")
        options.grid(row=3, column=0, columnspan=3, sticky="we", padx=10, pady=6)
        self._build_options(options)

        ttk.Label(controls_frame, text="出力ファイル：").grid(row=4, column=0, sticky="w", **label_pad)
        output_entry = ttk.Entry(controls_frame, textvariable=self.output_path_var, width=55)
        output_entry.grid(row=4, column=1, sticky="we", **field_pad)
        theme.create_button(controls_frame, text="出力先...", command=self._browse_output).grid(
            row=4, column=2, sticky="w", **pad)

        # 入力ファイル未選択の間は、現在の既定設定（F-26）から導かれる出力先方針
        # （固定フォルダのパス、または「入力ファイルと同じフォルダ」）をグレーで示す。
        self.output_entry = output_entry
        self._output_entry_default_fg = output_entry.cget("foreground")
        output_entry.bind("<FocusIn>", self._on_output_entry_focus_in)
        output_entry.bind("<FocusOut>", self._on_output_entry_focus_out)
        self._show_output_placeholder()

        self.progress_bar = ttk.Progressbar(
            controls_frame, variable=self.progress_var, maximum=100, length=300)
        self.progress_bar.grid(row=5, column=0, columnspan=2, sticky="we", **pad)
        self.progress_bar.grid_remove()
        self.convert_button = theme.create_button(
            controls_frame, accent=True, text="変換して出力", command=self._on_convert,
            width=16, state="disabled")
        self.convert_button.grid(row=5, column=2, sticky="e", **pad)

        ttk.Label(controls_frame, text="処理ログ：").grid(row=6, column=0, sticky="nw", **label_pad)
        self.log_text = tk.Text(controls_frame, height=8, width=64, state="disabled", wrap="word")
        self.log_text.grid(row=6, column=1, columnspan=2, sticky="nswe", **field_pad)

        self.open_folder_button = theme.create_button(
            controls_frame, text="出力フォルダを開く", command=self._open_output_folder,
            width=16, state="disabled")
        self.open_folder_button.grid(row=7, column=2, sticky="e", **pad)

        ttk.Label(
            controls_frame,
            text="※ FAXの送信操作は、各インターネットFAXサービスの画面で別途行ってください。",
            foreground=theme.COLOR_MUTED, anchor="w", justify="left",
        ).grid(row=8, column=0, columnspan=3, sticky="w", padx=10, pady=(2, 10))

    def _build_options(self, parent: tk.Misc) -> None:
        # 選択肢の数が少ない項目から順に並べる：圧縮方式→容量超過→用紙正規化→変換レポート。
        ttk.Label(parent, text="圧縮方式：").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(4, 0))

        ttk.Radiobutton(parent, text="二値化（白黒・G4圧縮）", variable=self.mode_var, value="binarize",
                        command=self._update_mode_widgets).grid(row=1, column=0, sticky="w", padx=8)
        ttk.Label(parent, text="※ カラー・階調情報が失われますが、FAXで最も一般的な原稿形式です。",
                  foreground=theme.COLOR_MUTED).grid(row=1, column=1, columnspan=2, sticky="w")

        ttk.Label(parent, text="　ディザ方式：").grid(row=2, column=0, sticky="e", padx=(24, 0))
        self.dither_combo = ttk.Combobox(
            parent, textvariable=self.dither_label_var, state="readonly", width=10,
            values=list(_DITHER_LABELS.values()))
        self.dither_combo.grid(row=2, column=1, sticky="w")

        ttk.Radiobutton(parent, text="グレースケール（JPEG圧縮）", variable=self.mode_var, value="grayscale",
                        command=self._update_mode_widgets).grid(row=3, column=0, sticky="w", padx=8)
        ttk.Label(parent, text="※ サービス側で黒つぶれ・白飛びが起こる場合があります。"
                              "実際に送信してご確認ください。", foreground=theme.COLOR_MUTED).grid(
            row=3, column=1, columnspan=2, sticky="w")

        ttk.Label(parent, text="　JPEG品質：").grid(row=4, column=0, sticky="e", padx=(24, 0))
        self.quality_combo = ttk.Combobox(
            parent, textvariable=self.quality_label_var, state="readonly", width=10,
            values=_QUALITY_LABELS)
        self.quality_combo.grid(row=4, column=1, sticky="w")

        ttk.Label(parent, text="容量超過時の処理方式：").grid(
            row=5, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 0))

        self.reduce_resolution_radio = ttk.Radiobutton(
            parent, text="解像度・品質を下げて再実行", variable=self.capacity_method_var,
            value="reduce_resolution", command=self._update_strategy_combo_state)
        self.reduce_resolution_radio.grid(row=6, column=0, sticky="w", padx=8)
        ttk.Label(parent, text="※ 送信回数を増やさずに済みます。", foreground=theme.COLOR_MUTED).grid(
            row=6, column=1, columnspan=2, sticky="w")

        ttk.Label(parent, text="　削減方式：").grid(row=7, column=0, sticky="e", padx=(24, 0))
        self.strategy_combo = ttk.Combobox(
            parent, textvariable=self.reduction_strategy_label_var, state="readonly", width=12,
            values=list(_REDUCTION_STRATEGY_LABELS.values()))
        self.strategy_combo.grid(row=7, column=1, sticky="w")

        self.split_radio = ttk.Radiobutton(
            parent, text="分割して再実行", variable=self.capacity_method_var,
            value="split", command=self._update_strategy_combo_state)
        self.split_radio.grid(row=8, column=0, sticky="w", padx=8)
        ttk.Label(parent, text="※ 画質を下げずに済みます。", foreground=theme.COLOR_MUTED).grid(
            row=8, column=1, columnspan=2, sticky="w")

        tk.Checkbutton(
            parent, text="用紙正規化（用紙サイズ・方向が対応外のページを自動で修正します）",
            variable=self.normalize_var,
        ).grid(row=9, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 0))

        tk.Checkbutton(
            parent, text="変換レポートを出力先フォルダに保存する",
            variable=self.report_var,
        ).grid(row=10, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 4))

        self._update_mode_widgets()
        self._update_strategy_combo_state()

    def _update_strategy_combo_state(self) -> None:
        """削減方式は、「解像度を下げて再実行」を選択している場合のみ選択可能とする。"""
        enabled = self.capacity_method_var.get() == "reduce_resolution"
        self.strategy_combo.configure(state="readonly" if enabled else "disabled")

    def _update_mode_widgets(self) -> None:
        is_binarize = self.mode_var.get() == "binarize"
        self.dither_combo.configure(state="readonly" if is_binarize else "disabled")
        self.quality_combo.configure(state="disabled" if is_binarize else "readonly")

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
        self.quality_label_var.trace_add("write", self._on_preview_options_changed)
        self.capacity_method_var.trace_add("write", self._on_preview_options_changed)
        self.reduction_strategy_label_var.trace_add("write", self._on_preview_options_changed)

    def _current_preview_options(self) -> convert_module.ConvertOptions:
        return convert_module.ConvertOptions(
            mode=self.mode_var.get(),
            normalize_enabled=self.normalize_var.get(),
            dither_method=_DITHER_LABELS_REV[self.dither_label_var.get()],
        )

    def _on_preview_options_changed(self, *_args) -> None:
        if not self.input_paths:
            return
        self.preview_pane.update_options(self._current_preview_options())

    # ------------------------------------------------------------------ 入力ファイル選択・診断

    def _browse_input(self) -> None:
        initial_dir = self.settings.last_input_dir or str(Path.home())
        # 入力済みの状態で再度開いた場合は「追加」となる（F-01。送信票の後に本文を
        # 追加する運用）。ダイアログ自体も複数選択に対応する。
        title = "追加するPDFファイルを選択" if self.input_paths else "変換するPDFファイルを選択"
        path_strs = filedialog.askopenfilenames(
            title=title,
            initialdir=initial_dir,
            filetypes=[("PDFファイル", "*.pdf")],
        )
        if path_strs:
            self._add_input_paths([Path(p) for p in path_strs])

    def _on_drop_input_file(self, event) -> None:
        paths = _parse_dropped_paths(event.data)
        if paths:
            self._add_input_paths([Path(p) for p in paths])

    def _add_input_paths(self, new_paths: list[Path]) -> None:
        """入力ファイル一覧の末尾にファイルを追加する（「入力元」・D&D共通。F-01）。"""
        non_pdf = [p.name for p in new_paths if p.suffix.lower() != ".pdf"]
        if non_pdf:
            messagebox.showerror(
                "VA FAX", "PDFファイル以外は入力できません：\n" + "、".join(non_pdf))
        pdfs = [p for p in new_paths if p.suffix.lower() == ".pdf"]
        if not pdfs:
            return
        if not self._confirm_adding_many_files(pdfs):
            return

        self.settings.last_input_dir = str(pdfs[-1].parent)
        save_settings(self.settings)
        self._set_input_paths(self.input_paths + pdfs)

    def _confirm_adding_many_files(self, pdfs: list[Path]) -> bool:
        """一度の操作で多数件を追加する場合の確認（誤って大量ドロップした場合の歯止め）。"""
        if len(pdfs) <= CONFIRM_INPUT_FILES_THRESHOLD:
            return True
        total = len(self.input_paths) + len(pdfs)
        return messagebox.askyesno(
            "VA FAX",
            f"{len(pdfs)}件のファイルを追加し、合計{total}件を結合します。よろしいですか？",
        )

    def _refresh_input_text(self) -> None:
        """入力ファイル欄の表示をself.input_pathsから作り直す（1行1ファイル）。"""
        self.input_text.delete("1.0", "end")
        if self.input_paths:
            self.input_text.configure(foreground=self._input_text_default_fg)
            self.input_text.insert("1.0", "\n".join(str(p) for p in self.input_paths))
        else:
            self.input_text.configure(foreground=_PLACEHOLDER_COLOR)
            self.input_text.insert("1.0", _INPUT_PLACEHOLDER_TEXT)

    def _on_input_text_key(self, event) -> Optional[str]:
        """入力ファイル欄のキー操作を制限する（A-2）。

        削除・カット・コピー・ペーストはいずれも「選択中の行」に対する操作とし、
        矢印キー等は選択行そのものを動かす（行単位の一覧として扱い、操作対象が
        常に反転表示で見えている状態を保つ）。文字の挿入・部分削除は一切
        受け付けない（表示中の一覧と実際の変換対象self.input_pathsの食い違いを防ぐ）。
        """
        keysym = event.keysym
        extend = bool(event.state & 0x1)  # Shift併用（選択範囲の拡張）
        if keysym in ("Delete", "BackSpace"):
            self._delete_selected_input_lines()
            return "break"
        if keysym in _INPUT_SELECTION_MOVE_KEYSYMS:
            self._move_input_selection(keysym, extend)
            return "break"
        if keysym == "Tab":
            # Textのタブは文字挿入となるため、通常のフォーカス移動に置き換える。
            event.widget.tk_focusNext().focus_set()
            return "break"
        if event.state & self._shortcut_state_mask:
            # Ctrl+X／C／Vは通常<<Cut>>等の仮想イベント側で処理される（同一バインド
            # タグ内で<Key>より優先されるため）が、環境差に備えてここでも受ける。
            key = keysym.lower()
            if key == "x":
                return self._on_input_cut()
            if key == "c":
                return self._on_input_copy()
            if key == "v":
                return self._on_input_paste()
            if key == "z":
                return self._on_input_undo()
            if key == "a":
                if self.input_paths:
                    self._set_input_selection(1, len(self.input_paths))
                return "break"
        return "break"

    # -------------------------------------------------- 入力ファイル欄の行選択

    def _selected_input_lines(self) -> Optional[tuple[int, int]]:
        """選択中の行の範囲を（開始行, 終了行）で返す（1始まり・両端含む）。未選択はNone。"""
        if not self.input_paths or self._sel_anchor is None or self._sel_active is None:
            return None
        start, end = sorted((self._sel_anchor, self._sel_active))
        return start, end

    def _set_input_selection(self, anchor: Optional[int], active: Optional[int] = None) -> None:
        """選択行を設定し、反転表示を更新する（anchor=Noneで選択解除）。

        anchorは選択の起点、activeはShift＋クリック／矢印キーで動かす側の端。
        いずれも1始まりの行番号で、一覧の範囲内に丸める。
        """
        if anchor is None or not self.input_paths:
            self._sel_anchor = self._sel_active = None
        else:
            last = len(self.input_paths)
            self._sel_anchor = max(1, min(anchor, last))
            self._sel_active = max(1, min(anchor if active is None else active, last))
        self._refresh_input_selection_display()

    def _refresh_input_selection_display(self) -> None:
        text = self.input_text
        text.tag_remove("sel", "1.0", "end")
        selection = self._selected_input_lines()
        if selection is None:
            return
        start, end = selection
        text.tag_add("sel", f"{start}.0", f"{end}.end")
        # 挿入カーソルは表示していない（幅0）が、選択の移動端に合わせておく。
        text.mark_set("insert", f"{self._sel_active}.0")
        text.see(f"{self._sel_active}.0")

    def _visible_input_line_count(self) -> int:
        """入力ファイル欄に一度に表示できる行数（PageUp／PageDownの移動量）。"""
        bbox = self.input_text.bbox("1.0")
        line_height = bbox[3] if bbox and bbox[3] else 0
        if line_height <= 0:
            return 1
        return max(1, self.input_text.winfo_height() // line_height)

    def _move_input_selection(self, keysym: str, extend: bool) -> None:
        """矢印キー等で選択行を動かす（extend=TrueのShift併用時は範囲を広げる）。"""
        if not self.input_paths:
            return
        last = len(self.input_paths)
        selection = self._selected_input_lines()
        if selection is None:
            # 未選択からの操作は、上方向なら末尾、下方向なら先頭を選ぶ。
            self._set_input_selection(last if keysym in ("Up", "Prior", "End") else 1)
            return

        current = self._sel_active or selection[0]
        page = self._visible_input_line_count()
        moves = {
            "Up": current - 1, "Down": current + 1,
            "Prior": current - page, "Next": current + page,
            "Home": 1, "End": last,
        }
        target = max(1, min(moves[keysym], last))
        self._set_input_selection(self._sel_anchor if extend else target, target)

    def _delete_selected_input_lines(self) -> None:
        """選択中の行のファイルを一覧から取り除く（未選択のときは何もしない）。"""
        selection = self._selected_input_lines()
        if selection is None:
            return
        start_line, end_line = selection
        removed = set(range(start_line - 1, end_line))
        self._set_input_paths(
            [p for i, p in enumerate(self.input_paths) if i not in removed])

    # -------------------------------------------------- 入力ファイル欄の行ドラッグ（並べ替え）

    def _input_line_at(self, y: int) -> Optional[int]:
        """入力ファイル欄のy座標にある行番号（1始まり）を返す。

        最終行より下の余白（ファイルのない領域）を指した場合はNoneを返す。
        """
        line = int(self.input_text.index(f"@0,{y}").split(".")[0])
        if line > len(self.input_paths):
            return None
        bbox = self.input_text.bbox(f"{line}.0")
        if bbox is not None and y > bbox[1] + bbox[3]:
            return None
        return max(1, line)

    def _input_drop_index_at(self, y: int) -> int:
        """入力ファイル欄のy座標が指す挿入位置（行と行の境界。0始まり）を返す。

        行の上半分なら その行の前、下半分なら その行の後を挿入位置とし、
        最終行より下の余白は末尾（＝一覧の最後）とする。
        """
        line = self._input_line_at(y)
        if line is None:
            return len(self.input_paths)
        bbox = self.input_text.bbox(f"{line}.0")
        if bbox is None:
            return line - 1
        _x, top, _width, height = bbox
        return line - 1 if y < top + height / 2 else line

    def _on_input_drag_press(self, event) -> Optional[str]:
        """入力ファイル欄の押下：クリックした行を選択し、ドラッグで動かす行を決める。"""
        self._drag_block = None
        self._shift_selecting = False
        self.input_text.focus_set()
        if not self.input_paths:
            # プレースホルダー表示中は操作対象がない。
            return "break"

        line = self._input_line_at(event.y)
        if line is None:
            # 最終行より下の余白をクリックした場合は選択を解除する。
            self._set_input_selection(None)
            return "break"

        if event.state & 0x1:
            # Shift併用時は選択範囲の拡張（起点は保ったまま、押した行まで広げる）。
            self._shift_selecting = True
            self._set_input_selection(
                self._sel_anchor if self._sel_anchor is not None else line, line)
            return "break"

        selection = self._selected_input_lines()
        if selection is not None and selection[0] <= line <= selection[1]:
            # 選択中の塊の中を押した場合は、その塊全体を動かす（ドラッグにならず
            # クリックで終わった場合は、離した時点で押した行だけの選択に戻す）。
            self._drag_block = selection
        else:
            self._set_input_selection(line)
            self._drag_block = (line, line)
        self._drag_press_line = line
        self._drag_origin_x = event.x
        self._drag_origin_y = event.y
        self._drag_pointer_x = event.x
        self._drag_pointer_y = event.y
        self._dragging = False
        return "break"

    def _on_input_drag_motion(self, event) -> Optional[str]:
        if self._shift_selecting:
            # Shift＋ドラッグ：ポインタが通った行まで選択範囲を広げる。
            line = self._input_line_at(event.y)
            if line is None:
                line = len(self.input_paths) if event.y > 0 else 1
            self._set_input_selection(self._sel_anchor, line)
            return "break"
        if self._drag_block is None:
            return None
        if not self._dragging:
            # 縦横いずれかに動いた時点でドラッグとみなす（欄の外へ真横に持ち出して
            # 取り除く操作も受け付けるため、縦方向だけでは判定しない）。
            if (max(abs(event.x - self._drag_origin_x), abs(event.y - self._drag_origin_y))
                    < _INPUT_DRAG_THRESHOLD_PX):
                return "break"
            self._dragging = True
            self.input_text.configure(cursor="hand2")
            self._set_input_selection(*self._drag_block)

        self._drag_pointer_x = event.x
        self._drag_pointer_y = event.y
        if self._is_outside_input_text(event.x, event.y):
            # 欄の外へ持ち出している間は「離すと一覧から取り除く」ことを、挿入位置の
            # 線を消し、カーソルを×印に変えて示す。
            self._drop_indicator.place_forget()
            self.input_text.configure(cursor=self._remove_cursor)
            return "break"

        self.input_text.configure(cursor="hand2")
        self._update_drop_indicator(event.y)
        height = self.input_text.winfo_height()
        if event.y < _INPUT_DRAG_EDGE_PX or event.y > height - _INPUT_DRAG_EDGE_PX:
            self._schedule_drag_scroll()
        return "break"

    def _on_input_drag_leave(self, _event=None) -> Optional[str]:
        """ドラッグ中に欄の外へ出たとき、Text既定の自動スキャンを開始させない。

        Tkは<B1-Leave>でtk::TextAutoScan（50ms間隔で選択範囲を拡張し続けるループ）を
        始め、これを止めるのはTextクラスの<ButtonRelease-1>バインド（tk::CancelRepeat）
        だが、この欄では並べ替えのため独自の<ButtonRelease-1>で"break"しており、
        そのままではループが止まらない。結果、欄外へのドロップで削除した後も残った行が
        選択され続け、Escapeやクリックで解除しても50ms後に付け直されてしまう。
        """
        if self._drag_block is None:
            return None
        return "break"

    def _is_outside_input_text(self, x: int, y: int) -> bool:
        """ドラッグ中のポインタが入力ファイル欄の外へ持ち出されているかを返す。

        欄の縁ぎりぎりでの取りこぼし（末尾へ移動しようとして少しはみ出した等）を
        削除と誤判定しないよう、縁から余裕（_INPUT_DRAG_OUTSIDE_MARGIN_PX）を取り、
        そこを明確に越えた場合のみ「外」とする。
        """
        margin = _INPUT_DRAG_OUTSIDE_MARGIN_PX
        return (
            x < -margin
            or y < -margin
            or x > self.input_text.winfo_width() + margin
            or y > self.input_text.winfo_height() + margin
        )

    def _on_input_drag_release(self, event) -> Optional[str]:
        if self._shift_selecting:
            self._shift_selecting = False
            return "break"
        if self._drag_block is None:
            return None
        start_line, end_line = self._drag_block
        was_dragging = self._dragging
        outside = self._is_outside_input_text(event.x, event.y)
        self._end_input_drag()
        if not was_dragging:
            # 動かさずに離した場合は通常のクリックとして扱い、押した行だけの選択に戻す。
            self._set_input_selection(self._drag_press_line)
            return "break"

        if outside:
            # 欄の外へ放した場合は、その行のファイルを一覧から取り除く（Deleteキーに
            # よる削除と同じ扱い。入力ファイル自体には手を触れない〔F-03〕。誤って
            # 放した場合はCtrl+Zで戻せる）。
            self._delete_selected_input_lines()
            return "break"

        drop_index = self._input_drop_index_at(event.y)
        if start_line - 1 <= drop_index <= end_line:
            # 元の位置（動かした塊自身の範囲内）へのドロップは並べ替えにならない。
            self._set_input_selection(start_line, end_line)
            return "break"

        paths, insert_at = _move_input_paths(
            self.input_paths, start_line - 1, end_line - 1, drop_index)
        self._set_input_paths(paths)
        self._set_input_selection(insert_at + 1, insert_at + (end_line - start_line + 1))
        return "break"

    def _on_input_escape(self, _event=None) -> str:
        """Escape：ドラッグ中なら並べ替え（取り除き）を取りやめ、行の選択を解除する。"""
        if self._drag_block is not None:
            self._end_input_drag()
        self._shift_selecting = False
        self._set_input_selection(None)
        return "break"

    def _end_input_drag(self) -> None:
        """ドラッグ状態（挿入位置の線・カーソル形状・自動スクロール）を後始末する。"""
        self._drag_block = None
        self._dragging = False
        self._drop_indicator.place_forget()
        self.input_text.configure(cursor=self._input_text_default_cursor)
        if self._drag_scroll_after_id is not None:
            self.after_cancel(self._drag_scroll_after_id)
            self._drag_scroll_after_id = None
        # Text既定の自動スキャンが動き出していた場合に備えて止める（_on_input_drag_leave
        # で通常は開始されないが、修飾キー併用時等の取りこぼしに対する保険。Textクラスの
        # <ButtonRelease-1>が行っている後始末と同じ処理）。
        try:
            self.tk.call("tk::CancelRepeat")
        except tk.TclError:
            pass

    def _update_drop_indicator(self, y: int) -> None:
        """挿入位置を示す線を、ドロップ先の行境界に合わせて表示する。"""
        text = self.input_text
        drop_index = self._input_drop_index_at(y)
        if drop_index < len(self.input_paths):
            bbox = text.bbox(f"{drop_index + 1}.0")
            line_y = bbox[1] if bbox else 0
        else:
            bbox = text.bbox(f"{len(self.input_paths)}.0")
            line_y = bbox[1] + bbox[3] if bbox else text.winfo_height()
        line_y = max(0, min(line_y, text.winfo_height() - 2))
        self._drop_indicator.place(in_=text, x=0, y=line_y, relwidth=1.0, height=2)
        self._drop_indicator.lift()

    def _schedule_drag_scroll(self) -> None:
        if self._drag_scroll_after_id is None:
            self._drag_scroll_after_id = self.after(
                _INPUT_DRAG_SCROLL_INTERVAL_MS, self._on_drag_scroll_tick)

    def _on_drag_scroll_tick(self) -> None:
        """ドラッグ中、ポインタが欄の上端・下端にある間は一覧を自動スクロールする。"""
        self._drag_scroll_after_id = None
        if not self._dragging or self._is_outside_input_text(
                self._drag_pointer_x, self._drag_pointer_y):
            return
        y = self._drag_pointer_y
        height = self.input_text.winfo_height()
        if y < _INPUT_DRAG_EDGE_PX:
            self.input_text.yview_scroll(-1, "units")
        elif y > height - _INPUT_DRAG_EDGE_PX:
            self.input_text.yview_scroll(1, "units")
        else:
            return
        self._update_drop_indicator(y)
        self._schedule_drag_scroll()

    def _on_input_copy(self, _event=None) -> str:
        """選択中の行のパスをクリップボードへコピーする（未選択のときは何もしない）。"""
        self._copy_input_lines_to_clipboard()
        return "break"

    def _copy_input_lines_to_clipboard(self) -> list[Path]:
        selection = self._selected_input_lines()
        if selection is None:
            return []
        start_line, end_line = selection
        paths = self.input_paths[start_line - 1:end_line]
        if paths:
            self.clipboard_clear()
            self.clipboard_append("\n".join(str(p) for p in paths))
        return paths

    def _on_input_cut(self, _event=None) -> str:
        """行単位のカット（コピー＋一覧からの削除）。ペーストと組み合わせて並べ替えに使う。"""
        copied = self._copy_input_lines_to_clipboard()
        if copied:
            self._delete_selected_input_lines()
        return "break"

    def _on_input_paste(self, _event=None) -> str:
        """クリップボードのパス（1行1ファイル）を選択中の行の前に挿入する。

        何も選択していない場合は一覧の末尾に追加する。この欄からカット・コピー
        した内容のほか、エクスプローラーの「パスのコピー」等で取得したPDFファイルの
        パスも貼り付けられる。
        """
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return "break"

        lines = [line.strip().strip('"') for line in text.splitlines()]
        lines = [line for line in lines if line]
        if not lines:
            return "break"

        paths = []
        for line in lines:
            path = Path(line)
            if path.suffix.lower() != ".pdf" or not path.is_file():
                messagebox.showerror(
                    "VA FAX",
                    "貼り付けられた内容をPDFファイルのパスとして認識できませんでした。\n"
                    "貼り付けできるのは、この欄でカット（Ctrl+X）・コピー（Ctrl+C）した行、"
                    "または実在するPDFファイルのパス（1行1ファイル）のみです。",
                )
                return "break"
            paths.append(path)

        if not self._confirm_adding_many_files(paths):
            return "break"

        selection = self._selected_input_lines()
        insert_at = len(self.input_paths) if selection is None else selection[0] - 1
        self._set_input_paths(self.input_paths[:insert_at] + paths + self.input_paths[insert_at:])
        # 貼り付けた行を選択状態にして、どこへ入ったかを示す。
        self._set_input_selection(insert_at + 1, insert_at + len(paths))
        return "break"

    def _bind_mac_keys(self) -> None:
        """Mac（ソースから動かす場合。N-03）のキー操作を、Windowsのキー操作にそろえる。

        - ⌘Z：Ctrl+Zと同じく、メイン画面のどこからでも入力ファイル一覧の取り消しにする
          （⌘C・⌘X・⌘V・⌘Zは、入力ファイル欄ではTkの<<Copy>>等の仮想イベントとして届く）。
        - ⌘Q：Tkの既定ではTclのexitで直ちにプロセスを終えるため、プレビューの描画
          スレッドの後始末（PreviewPane.destroy）を通らない。閉じるボタンと同じく
          ウィンドウを破棄して終える。
        """
        self.bind_all("<Command-z>", self._on_global_undo, add="+")
        self.bind_all("<Command-Z>", self._on_global_undo, add="+")
        self.createcommand("::tk::mac::Quit", self.destroy)

    def _on_global_undo(self, event) -> Optional[str]:
        """アプリ全体のCtrl+Zを入力ファイル一覧の取り消しとして処理する。

        入力ファイル欄自身にフォーカスがある場合は、ウィジェット側のバインド
        （_on_input_text_key・<<Undo>>）が先に処理して"break"するため、ここには
        届かない。他ウィジェットの編集操作と紛らわしくなるのを避けるため、
        文字入力欄（出力ファイル欄等のEntry）と、別ウィンドウ（設定ダイアログ等）
        での押下は対象外とする。
        """
        widget = event.widget
        try:
            if not isinstance(widget, tk.Misc) or widget.winfo_toplevel() is not self:
                return None
            if isinstance(widget, (ttk.Entry, tk.Entry)):
                return None
        except tk.TclError:
            return None
        return self._on_input_undo()

    def _on_input_undo(self, _event=None) -> str:
        """入力ファイル一覧への直前の変更（追加・削除・カット・ペースト）を取り消す。

        履歴が残っている限り、Ctrl+Zを繰り返すことでさらに前の状態まで遡れる
        （進む方向のやり直し〔Redo〕は設けない）。
        """
        if self._input_undo_stack:
            previous = self._input_undo_stack.pop()
            self._set_input_paths(previous, record_undo=False)
        return "break"

    def _set_input_paths(self, paths: list[Path], record_undo: bool = True) -> None:
        """入力ファイル一覧を確定し、表示・出力初期値・事前診断・プレビューを更新する。

        追加・削除・並べ替えのいずれでも呼ばれ、そのたびに事前診断とプレビューを
        更新する（F-04・F-30）。変更前の一覧はCtrl+Z用の履歴に積む
        （record_undo=FalseはCtrl+Zによる巻き戻し自身からの呼び出し用）。
        """
        if record_undo and paths != self.input_paths:
            self._input_undo_stack.append(list(self.input_paths))
            del self._input_undo_stack[:-INPUT_UNDO_LIMIT]
        self.input_paths = paths
        self._refresh_input_text()
        # 表示を作り直すと選択も失われるため、状態も未選択に戻す（並べ替え・貼り付け
        # のように移動先を示したい操作は、この後で改めて選択し直す）。
        self._set_input_selection(None)

        # 前回ファイルの進捗表示（満タンのまま等）が新しい内容に引き継がれないよう初期化する。
        self.progress_var.set(0.0)
        self.progress_bar.grid_remove()

        if not paths:
            # 全件削除された場合：診断・プレビューを空に戻し、進行中の診断結果も無効化する。
            self._diagnosis_token += 1
            self.diagnosis = None
            self._set_diagnosis_text("")
            self.convert_button.configure(state="disabled")
            self.preview_pane.clear()
            self._show_output_placeholder()
            return

        # 出力初期値は既定設定の方針から自動決定する（F-02）。ファイル名は先頭
        # （1件目）の入力ファイル名に基づく。画面での手動変更は当該変換限りで、
        # 一覧が変わるたびにここで再計算される（F-26）。
        self._set_output_path_text(
            str(compute_default_output_path(paths[0], self.settings.defaults)))

        self._run_diagnosis(list(paths))

    def _set_output_path_text(self, text: str) -> None:
        """出力ファイル欄へパスを設定する（プレースホルダー表示から通常文字色に戻す）。"""
        self.output_entry.configure(foreground=self._output_entry_default_fg)
        self.output_path_var.set(text)

    def _describe_output_destination(self) -> str:
        """現在の既定設定（F-26）から、出力先フォルダの説明文を作る（プレースホルダー表示用）。"""
        d = self.settings.defaults
        if d.output_folder_policy == "fixed" and d.fixed_output_folder:
            return d.fixed_output_folder
        return "入力ファイルと同じフォルダ"

    def _show_output_placeholder(self) -> None:
        self.output_entry.configure(foreground=_PLACEHOLDER_COLOR)
        self.output_path_var.set(self._describe_output_destination())

    def _on_output_entry_focus_in(self, event=None) -> None:
        # ttk.Entryのcget("foreground")は文字列ではなくTclの色オブジェクトを返すため、
        # str()で正規化してから比較する（==で直接比較すると常にFalseになる）。
        if str(self.output_entry.cget("foreground")) == _PLACEHOLDER_COLOR:
            self.output_entry.configure(foreground=self._output_entry_default_fg)
            self.output_path_var.set("")

    def _on_output_entry_focus_out(self, event=None) -> None:
        if self.output_path_var.get().strip():
            return
        if self.input_paths:
            # 入力ファイルは既に選択済みのため、方針から実際の出力パスを再計算する
            # （F-02。ファイル名は先頭の入力ファイル名に基づく）。
            self._set_output_path_text(
                str(compute_default_output_path(self.input_paths[0], self.settings.defaults)))
        else:
            self._show_output_placeholder()

    def _browse_output(self) -> None:
        d = self.settings.defaults
        if d.output_folder_policy == "fixed" and d.fixed_output_folder:
            initial_dir = d.fixed_output_folder
        elif self.input_paths:
            initial_dir = str(self.input_paths[0].parent)
        else:
            initial_dir = self.settings.last_input_dir or str(Path.home())

        # 現在の出力ファイル名をダイアログに初期表示する（毎回の手動入力を不要にする）。
        # プレースホルダー表示中（出力先方針の説明文でありファイル名ではない）は使わず、
        # 入力ファイル選択済みなら既定設定から導かれる出力ファイル名を用いる。
        # cget("foreground")はTclの色オブジェクトを返すためstr()で正規化して比較する
        # （_on_output_entry_focus_inと同じ注意点）。
        initial_file = ""
        if str(self.output_entry.cget("foreground")) != _PLACEHOLDER_COLOR:
            current = self.output_path_var.get().strip()
            if current:
                current_path = Path(current)
                initial_file = current_path.name
                if current_path.parent.is_dir():
                    initial_dir = str(current_path.parent)
        if not initial_file and self.input_paths:
            initial_file = compute_default_output_path(self.input_paths[0], d).name

        # 上書き確認は変換実行時に行う（F-02。「分割して再実行」ではここで指定した名前は
        # 分割時のベース名であり、実際にこの名前で書き込まれるとは限らないため、
        # ダイアログ側のOS標準の上書き確認は誤解を招く。二重確認の回避も兼ねる）。
        path_str = filedialog.asksaveasfilename(
            title="出力先を指定", initialdir=initial_dir, initialfile=initial_file,
            defaultextension=".pdf", filetypes=[("PDFファイル", "*.pdf")],
            confirmoverwrite=False,
        )
        if path_str:
            self._set_output_path_text(path_str)

    def _run_diagnosis(self, paths: list[Path]) -> None:
        """事前診断をバックグラウンドスレッドで実行する（B-4。GUIスレッドをブロックしない）。

        トークン（世代番号）を発行し、応答時に照合することで、診断中に入力一覧が
        変更された場合の古い結果を無視する（B-5。診断対象と変換ボタンの状態の食い違い防止）。
        """
        self._set_diagnosis_text("診断中です...")
        self.diagnosis = None
        self.convert_button.configure(state="disabled")
        self._diagnosis_token += 1
        token = self._diagnosis_token
        profile = self.profile  # 診断中にプロファイルが変更されても、この診断はこの世代の値で行う
        thread = threading.Thread(target=self._do_diagnose, args=(paths, profile, token), daemon=True)
        thread.start()

    def _read_and_merge_inputs(self, paths: list[Path]) -> tuple[Optional[bytes], list[bytes], Optional[str]]:
        """全入力ファイルを読み込み、1つのPDFへ結合する（診断・変換共通。F-01）。

        戻り値は（結合後バイト列, 各ファイルのバイト列, エラーメッセージ）。
        失敗時は結合後バイト列がNoneとなり、エラーメッセージに対象ファイル名を含める
        （画面表示用。ログにはファイル名を記録しない〔F-24〕）。
        """
        data_list: list[bytes] = []
        for index, path in enumerate(paths):
            try:
                data_list.append(path.read_bytes())
            except OSError as exc:
                self.logger.info("入力ファイル読込失敗（%d件目）：%s", index + 1, type(exc).__name__)
                return None, data_list, (
                    f"入力ファイル「{path.name}」を読み込めませんでした。ファイルが移動・削除"
                    "されていないか、他のアプリで開かれていないかご確認ください。"
                )
        try:
            merged = merge_module.merge_pdfs(data_list)
        except merge_module.MergeError as exc:
            self.logger.info("入力ファイル結合失敗（%d件目）：%s",
                             exc.file_index + 1, type(exc.__cause__ or exc).__name__)
            return None, data_list, (
                f"入力ファイル「{paths[exc.file_index].name}」を処理できませんでした。\n{exc}"
            )
        if len(paths) > 1:
            self.logger.info("入力ファイル：%d件を結合しました", len(paths))
        return merged, data_list, None

    def _do_diagnose(self, paths: list[Path], profile: Profile, token: int) -> None:
        merged, _, error_message = self._read_and_merge_inputs(paths)
        if merged is None:
            self._message_queue.put({
                "type": "diagnosis_error", "token": token, "message": error_message,
            })
            return

        try:
            diagnosis = diagnose_module.diagnose(merged, profile)
        except (diagnose_module.EncryptedPdfError, diagnose_module.CorruptedPdfError) as exc:
            self.logger.info("診断エラー：%s", exc)
            self._message_queue.put({"type": "diagnosis_error", "token": token, "message": str(exc)})
            return
        except Exception as exc:  # 予期しない例外（F-22）：詳細は出さずログにのみ記録
            self.logger.error("診断中に予期しないエラーが発生しました：%s", _format_exception_for_log(exc))
            self._message_queue.put({
                "type": "diagnosis_error", "token": token,
                "message": f"ファイルの診断中に問題が発生しました。\n{CONTACT_NOTICE}",
            })
            return

        self._log_diagnosis_summary(diagnosis)
        self._message_queue.put({
            "type": "diagnosis_done", "token": token, "diagnosis": diagnosis,
            "profile": profile, "input_bytes": merged, "input_count": len(paths),
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

    def _apply_defaults_to_screen(self) -> None:
        """既定設定（F-26）から画面の処理選択を再計算する。

        圧縮方式・用紙正規化とも常に既定設定の固定値を用い、文書内容や事前診断結果
        による自動推奨は行わない（F-05・F-06）。
        """
        d = self.settings.defaults

        self.mode_var.set("grayscale" if d.compression_policy == "always_grayscale" else "binarize")
        self.normalize_var.set(d.normalize_enabled)

        self.dither_label_var.set(_DITHER_LABELS.get(d.dither_method, _DITHER_LABELS["auto"]))
        self.quality_label_var.set(
            _QUALITY_LABELS[_QUALITY_LEVEL_TO_INDEX.get(d.jpeg_quality_level, 1)])
        self.capacity_method_var.set(d.capacity_exceeded_method)
        self.reduction_strategy_label_var.set(
            _REDUCTION_STRATEGY_LABELS.get(
                d.size_reduction_strategy, _REDUCTION_STRATEGY_LABELS["culprit_only"]))
        self.report_var.set(d.report_enabled)
        self._update_mode_widgets()
        self._update_strategy_combo_state()

    def _format_diagnosis(self, d: diagnose_module.Diagnosis) -> str:
        lines = []
        if self._diagnosed_input_count > 1:
            lines.append(f"入力ファイル数：{self._diagnosed_input_count}件（上から順に結合して変換します）")
        size_label = "結合後のファイルサイズ" if self._diagnosed_input_count > 1 else "入力ファイルサイズ"
        lines += [
            f"総ページ数：{d.page_count}ページ",
            f"{size_label}：{_format_size(d.file_size_bytes)}",
            f"テキストデータ：{'あり（画像化されます）' if d.has_text else 'なし'}",
        ]

        color_count = sum(1 for p in d.pages if p.content_type == "color")
        mono_count = sum(1 for p in d.pages if p.content_type == "mono")
        gray_count = d.page_count - color_count - mono_count
        lines.append(f"色の判定：カラー{color_count}ページ・グレースケール{gray_count}ページ・"
                     f"白黒{mono_count}ページ")

        if self.profile.page_count_limit is not None:
            status = "上限超過" if d.page_count_exceeds_limit else "上限内"
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

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # ------------------------------------------------------------------ 変換実行

    def _on_convert(self) -> None:
        if self._converting:
            return
        if not self.input_paths:
            messagebox.showerror("VA FAX", "入力ファイルを選択してください。")
            return
        output_str = self.output_path_var.get().strip()
        if not output_str:
            messagebox.showerror("VA FAX", "出力ファイルを指定してください。")
            return

        quality_index = _QUALITY_LABELS.index(self.quality_label_var.get())
        options = convert_module.ConvertOptions(
            mode=self.mode_var.get(),
            normalize_enabled=self.normalize_var.get(),
            dither_method=_DITHER_LABELS_REV[self.dither_label_var.get()],
            jpeg_quality=self.profile.jpeg_quality_steps[quality_index],
        )
        capacity_method = self.capacity_method_var.get()
        reduction_strategy = _REDUCTION_STRATEGY_LABELS_REV[self.reduction_strategy_label_var.get()]
        save_report_flag = self.report_var.get()
        input_paths = list(self.input_paths)
        output_path = Path(output_str)

        try:
            # 出力パスがいずれかの入力ファイルとも一致しないことを検証する（F-03の非破壊原則）。
            for input_path in input_paths:
                validate_output_path(input_path, output_path)
        except OutputPathError as exc:
            messagebox.showerror("VA FAX", str(exc))
            return
        if output_path.exists():
            # 「分割して再実行」で実際に複数ファイルに分かれる場合、最終的なファイル名は
            # ここでのoutput_pathとは異なる（_1of3等のサフィックスが付く）ため、この確認は
            # 分割が発生しない場合（従来どおりの単一ファイル出力）にのみ意味を持つ。
            # 分割後の各ファイルの重複は_do_convert側で別途検証する。
            overwrite = messagebox.askyesno(
                "VA FAX",
                f"出力先に同名のファイルが既に存在します：\n{output_path.name}\n上書きしますか？")
            if not overwrite:
                return

        self._converting = True
        self.progress_var.set(0.0)
        self.progress_bar.grid()
        self._set_diagnosis_text(self._format_diagnosis(self.diagnosis)) if self.diagnosis else None
        self._append_log(f"変換を開始します（{options.mode}モード）")
        self.convert_button.configure(state="disabled")
        self.open_folder_button.configure(state="disabled")
        self._set_settings_menu_enabled(False)

        # このワーカーはこの変換の間、常にこのプロファイルを使う（B-1）。
        # 途中で self.profile が変更されても、進行中の変換・容量判定・レポートには影響しない。
        profile = self.profile

        thread = threading.Thread(
            target=self._do_convert,
            args=(input_paths, output_path, profile, options, capacity_method,
                  reduction_strategy, save_report_flag),
            daemon=True,
        )
        thread.start()

    def _do_convert(self, input_paths: list[Path], output_path: Path, profile: Profile,
                     options: convert_module.ConvertOptions, capacity_method: str,
                     reduction_strategy: str, save_report_flag: bool) -> None:
        input_bytes, input_bytes_list, error_message = self._read_and_merge_inputs(input_paths)
        if input_bytes is None:
            self._message_queue.put({"type": "error", "message": error_message})
            return

        try:
            def on_page_done(done: int, total: int, page_log: convert_module.PageConversionLog) -> None:
                detail = f"{page_log.resolution_dpi}dpi"
                if page_log.dither_method:
                    detail += f"・{_DITHER_LABELS.get(page_log.dither_method, page_log.dither_method)}"
                if page_log.jpeg_quality is not None:
                    detail += f"・品質{page_log.jpeg_quality}"
                self._message_queue.put({"type": "progress", "done": done, "total": total, "detail": detail})
                self.logger.info("ページ %d/%d 処理完了（%s）", done, total, detail)

            result = convert_module.convert(input_bytes, profile, options, on_page_done=on_page_done)

            def on_retry(page_index: int, old_size: int, new_size: int,
                         dpi: int, quality: Optional[int]) -> None:
                quality_text = f"・品質{quality}" if quality is not None else ""
                msg = (f"ページ{page_index + 1}：{old_size}バイトで容量超過→"
                       f"{dpi}dpi{quality_text}で再生成→{new_size}バイト")
                self._message_queue.put({"type": "retry", "message": msg})
                self.logger.info(msg)

            # 容量超過時は常に自動で対処する（F-13）。既に容量上限以下の場合は
            # enforce_size_limit／split_by_capacity自体が何もせずそのまま返す。
            try:
                if capacity_method == "split":
                    results = size_control_module.split_by_capacity(
                        input_bytes, profile, options, result, on_page_reduced=on_retry)
                else:
                    results = [size_control_module.enforce_size_limit(
                        input_bytes, profile, options, result, on_retry=on_retry,
                        strategy=reduction_strategy)]
            except size_control_module.SizeLimitExceededError as exc:
                self.logger.info("変換エラー（容量超過）：%s", exc)
                self._message_queue.put({"type": "error", "message": str(exc)})
                return

            pdf_bytes_list = [r.build_pdf() for r in results]
            part_paths = _split_output_paths(output_path, len(results))
            if len(results) > 1:
                # 入力ファイル名が偶然「_1of2」等のサフィックス形式で、分割後のパート名と
                # 一致してしまう場合に備え、全入力パスとの一致を明示的に検証する（F-03の
                # 非破壊原則。_on_convertのvalidate_output_pathはoutput_path単体しか
                # 見ないため、サフィックス付与後のパスはここで検証する必要がある）。
                input_norms = {os.path.normcase(os.path.abspath(str(p))) for p in input_paths}
                for p in part_paths:
                    if os.path.normcase(os.path.abspath(str(p))) in input_norms:
                        self.logger.info("変換エラー（分割後のファイル名が入力ファイルと一致）")
                        self._message_queue.put({
                            "type": "error",
                            "message": f"分割後の出力ファイル名（{p.name}）が入力ファイルと同じに"
                                       "なってしまいます。入力ファイルは上書きできないため、"
                                       "出力ファイル名を変更してください。",
                        })
                        return

                # 分割によりファイル名が変わる（_1of3等のサフィックスが付く）ため、
                # _on_convertでの事前確認（output_path単体）はここには及ばない。
                # 実際に書き込む全パートの重複を改めて検証する（同一条件での再変換等）。
                existing = [p.name for p in part_paths if p.exists()]
                if existing:
                    self.logger.info("変換エラー（分割後のファイルが既に存在）：%d件", len(existing))
                    self._message_queue.put({
                        "type": "error",
                        "message": "分割後の出力ファイルが既に存在します：\n"
                                   + "、".join(existing)
                                   + "\n該当ファイルを移動・削除するか、出力ファイル名を変更してから、"
                                     "もう一度変換してください。",
                    })
                    return

            written_paths: list[Path] = []
            try:
                for path, pdf_bytes in zip(part_paths, pdf_bytes_list):
                    path.write_bytes(pdf_bytes)
                    written_paths.append(path)
            except OSError as exc:
                self.logger.info("変換エラー（出力書込失敗）：%s", type(exc).__name__)
                # 分割の途中で失敗した場合、書き込み済みの一部のパートだけが残ると、
                # 利用者が不完全なセットと気づかずに送信してしまう恐れがあるため、
                # 今回書き込んだ分は削除する（今回の実行で作成したファイルのみが対象）。
                undeleted: list[str] = []
                for p in written_paths:
                    try:
                        p.unlink()
                    except OSError:
                        undeleted.append(p.name)
                message = ("出力ファイルを書き込めませんでした。出力先フォルダの権限や"
                           "空き容量、他のアプリで開かれていないかをご確認ください。")
                if undeleted:
                    message += ("\n書き込み途中のファイルが残っています。削除してください：\n"
                                + "、".join(undeleted))
                self._message_queue.put({"type": "error", "message": message})
                return

            # 分割が発生した場合、無サフィックスの元の出力ファイル名（例：foo_fax.pdf）には
            # 書き込まれない。同名の古いファイルが残っていると分割後のファイルと取り違えて
            # 誤送信する恐れがあるため、完了メッセージで注意を促す（削除はしない。
            # そのファイルが本ツール以外で作られたものである可能性を排除できないため）。
            stale_base_note = ""
            if len(results) > 1 and output_path.exists():
                stale_base_note = (
                    f"\n※ 同名の既存ファイル（{output_path.name}）は今回の出力では更新されていません。"
                    "分割後のファイルとの取り違えにご注意ください。"
                )

            report_saved = True
            if save_report_flag:
                for path, pdf_bytes, part_result in zip(part_paths, pdf_bytes_list, results):
                    report_data = ReportData(
                        input_filename="、".join(p.name for p in input_paths),
                        input_size_bytes=sum(len(b) for b in input_bytes_list),
                        output_filename=path.name,
                        profile_display_name=profile.display_name,
                        mode=options.mode,
                        normalize_enabled=options.normalize_enabled,
                        capacity_exceeded_method=capacity_method,
                        page_logs=part_result.page_logs,
                        final_output_size_bytes=len(pdf_bytes),
                        jpeg_quality_requested=options.jpeg_quality,
                    )
                    try:
                        save_report(path, report_data)
                    except OSError as exc:
                        # 変換自体（PDF出力）は既に成功しているため、エラー扱いにはせず
                        # 完了メッセージに注記するに留める。
                        report_saved = False
                        self.logger.info("変換レポート保存エラー：%s", type(exc).__name__)

            total_pages = sum(len(r.page_logs) for r in results)
            total_size = sum(len(b) for b in pdf_bytes_list)
            self.logger.info("変換完了：%d個のファイル、%dページ、出力サイズ合計 %d バイト",
                              len(results), total_pages, total_size)
            self._message_queue.put({
                "type": "done",
                "output_paths": [str(p) for p in part_paths],
                "output_dir": str(part_paths[0].parent),
                "page_counts": [len(r.page_logs) for r in results],
                "output_sizes": [len(b) for b in pdf_bytes_list],
                "report_saved": report_saved,
                "stale_base_note": stale_base_note,
                # プレビューに実際の出力（容量超過対処後の最終結果）を表示するため、
                # 最初のパートのPDFバイト列をそのまま渡す（N-06：メモリ内でのみ受け渡す）。
                "pdf_bytes": pdf_bytes_list[0],
            })
        except (convert_module.PageCountExceededError, convert_module.UnsupportedPageSizeError) as exc:
            self.logger.info("変換エラー（検証エラー）：%s", exc)
            self._message_queue.put({"type": "error", "message": str(exc)})
        except convert_module.ConversionError as exc:
            self.logger.info("変換エラー：%s", exc)
            self._message_queue.put({"type": "error", "message": str(exc)})
        except Exception as exc:  # 予期しない例外（F-22）：詳細は出さずログにのみ記録
            self.logger.error("変換中に予期しないエラーが発生しました：%s", _format_exception_for_log(exc))
            self._message_queue.put({
                "type": "error",
                "message": f"変換中に問題が発生しました。\n{CONTACT_NOTICE}",
            })

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
                return  # 別のファイルが選択済み・古い診断結果は無視する（B-5）
            self.diagnosis = None
            self._set_diagnosis_text(message["message"])
            self.convert_button.configure(state="disabled")
            self.preview_pane.clear()
            messagebox.showerror("VA FAX", message["message"])
        elif kind == "diagnosis_done":
            if message["token"] != self._diagnosis_token:
                return  # 別のファイルが選択済み・古い診断結果は無視する（B-5）
            diagnosis = message["diagnosis"]
            self.diagnosis = diagnosis
            self._diagnosed_input_count = message["input_count"]
            self._apply_defaults_to_screen()
            self._set_diagnosis_text(self._format_diagnosis(diagnosis))
            self.convert_button.configure(state="normal")
            self.preview_pane.set_document(
                message["input_bytes"], message["profile"], self._current_preview_options())
        elif kind == "progress":
            done, total = message["done"], message["total"]
            self.progress_var.set(done / total * 100 if total else 0)
            self._append_log(f"ページ {done}/{total} を処理しました（{message['detail']}）")
        elif kind == "retry":
            self._append_log(message["message"])
        elif kind == "error":
            self._converting = False
            self._set_settings_menu_enabled(True)
            # 現在も有効な診断結果があるファイルに対してのみ、再実行を許可する（B-5）。
            # 変換中に別ファイルの診断が失敗していた場合はdiagnosisがNoneのままになる。
            if self.diagnosis is not None:
                self.convert_button.configure(state="normal")
            self.progress_bar.grid_remove()
            self._append_log(f"エラー：{message['message']}")
            messagebox.showerror("VA FAX", message["message"])
        elif kind == "done":
            self._converting = False
            self._set_settings_menu_enabled(True)
            if self.diagnosis is not None:
                self.convert_button.configure(state="normal")
            self.progress_var.set(100.0)
            self.progress_bar.grid_remove()
            self._last_output_dir = Path(message["output_dir"])
            self.open_folder_button.configure(state="normal")
            # プレビューを実際の出力PDFの表示に切り替える（容量超過対処の結果も含めて
            # 出力そのものが見える。処理内容の変更・別ファイル選択で元の表示に戻る）。
            self.preview_pane.show_converted_result(message["pdf_bytes"])

            output_paths = message["output_paths"]
            page_counts = message["page_counts"]
            output_sizes = message["output_sizes"]
            note = "（変換レポートの保存には失敗しました）" if not message["report_saved"] else ""

            if len(output_paths) == 1:
                self._append_log(
                    f"変換が完了しました：{output_paths[0]}"
                    f"（{page_counts[0]}ページ、{_format_size(output_sizes[0])}）{note}"
                )
                popup_message = "変換が完了しました。"
            else:
                for path, pages, size in zip(output_paths, page_counts, output_sizes):
                    self._append_log(f"　出力：{Path(path).name}（{pages}ページ、{_format_size(size)}）")
                self._append_log(
                    f"変換が完了しました：{len(output_paths)}個のファイルに分割して出力しました"
                    f"（合計{sum(page_counts)}ページ）。{note}{message['stale_base_note']}"
                )
                popup_message = f"変換が完了しました（{len(output_paths)}個のファイルに分割）。"
            if message["stale_base_note"]:
                popup_message += message["stale_base_note"]
            if not message["report_saved"]:
                popup_message += "\n（変換レポートの保存には失敗しました）"
            messagebox.showinfo("VA FAX", popup_message)

    def _open_output_folder(self) -> None:
        if self._last_output_dir is None:
            return
        if sys.platform == "win32":
            _open_folder(self._last_output_dir)
            return
        try:
            _open_folder(self._last_output_dir)
        except OSError:  # Mac等でopen／xdg-openを起動できない場合
            messagebox.showerror(
                "VA FAX", "出力フォルダを開けませんでした。出力先のフォルダを直接開いてください。")

    # ------------------------------------------------------------------ 設定メニュー

    def _on_change_profile(self) -> None:
        dialog = ProfileChoiceDialog(self, current_profile_id=self.profile.id)
        self.wait_window(dialog)
        if not dialog.result_profile_id:
            return
        # カスタムは同じidのまま内容（容量上限等）だけが更新されうるため、
        # idが変わっていなくてもcustomの場合は再読込する。
        if dialog.result_profile_id == self.profile.id and dialog.result_profile_id != "custom":
            return
        try:
            self.profile = load_all_profiles()[dialog.result_profile_id]
        except (KeyError, ProfileError):
            return
        self.settings.profile_id = self.profile.id
        save_settings(self.settings)
        self.profile_label_var.set(_profile_summary(self.profile))
        if self.input_paths:
            self._run_diagnosis(list(self.input_paths))

    def _on_open_defaults(self) -> None:
        """変換の既定設定ダイアログを開き、保存されたら画面へ反映する（F-26）。"""
        dialog = DefaultsDialog(self, self.settings.defaults)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        self.settings.defaults = dialog.result
        save_settings(self.settings)
        self._apply_defaults_to_screen()
        if self.input_paths:
            self._set_output_path_text(
                str(compute_default_output_path(self.input_paths[0], self.settings.defaults)))
        else:
            self._show_output_placeholder()
