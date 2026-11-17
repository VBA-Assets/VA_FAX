# -*- coding: utf-8 -*-
"""ページプレビュー欄（F-30）。

入力PDFの全ページをサムネイル一覧として表示し、選択中の1ページを拡大表示する。
表示内容は選択中の処理内容（圧縮方式・ディザ方式・用紙正規化）を反映し、
設定変更のたびに自動的に再生成する。拡大表示は本変換と同じ解像度（プロファイルの
既定dpi）でパイプラインを実行し、表示時にのみ縮小する（低解像度で処理すると、
大津法・自動ディザ判定等の結果が本変換と食い違うため）。変換完了後は、
シミュレーションではなく実際の出力PDFそのものを表示する（show_converted_result）。
表示範囲に応じた遅延読み込みを行い、レンダリングは常にバックグラウンドスレッドで
行いGUIをブロックしない（N-06：中間データはメモリ内のみで扱い、ディスクへは
書き出さない）。
"""
from __future__ import annotations

import io
import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk
from typing import Callable, Iterable, Optional, Sequence

import pypdf
from PIL import Image, ImageTk

from config.profiles import Profile
from core import convert
from core.convert import ConvertOptions
from gui import theme

THUMBNAIL_DPI = 10
VISIBLE_BUFFER = 3  # 表示範囲の前後、先読みするページ数
POLL_INTERVAL_MS = 80
# 文書を開いていないときの案内（F-15）
EMPTY_STATUS_TEXT = "［開く］を押すか、ここへファイルをドラッグしてください。"

# サムネイルのドラッグ（並べ替え・取り除き。F-33）
DRAG_START_PX = 5             # これだけ動かしたら、クリックではなくドラッグとして扱う
DRAG_OUTSIDE_MARGIN_PX = 16   # 欄の縁からこれを越えたら「外」（取り除き）と判定する
DRAG_SCROLL_ZONE_PX = 14      # 欄の上端・下端からこの範囲にある間、一覧を自動スクロールする
DRAG_SCROLL_INTERVAL_MS = 60
_SHIFT_MASK = 0x0001
_CONTROL_MASK = 0x0004


def click_selection(selection: Iterable[int], anchor: Optional[int], index: int,
                    shift: bool, ctrl: bool) -> tuple[set[int], int]:
    """サムネイルを押したときの、新しい選択と起点（anchor）を返す（F-33）。

    そのまま押す＝そのページだけ、Shift＝起点からそのページまでの範囲、Ctrl＝そのページの
    選択を入れ替える（ほかのページの選択は保つ）。
    """
    if shift:
        start = index if anchor is None else anchor
        return set(range(min(start, index), max(start, index) + 1)), start
    if ctrl:
        return set(selection) ^ {index}, index
    return {index}, index


def drop_index_for_y(slot_bounds: Sequence[tuple[float, float]], y: float) -> int:
    """ポインタの縦位置yから、挿入位置（ページとページの境界。0＝先頭の前）を求める。

    slot_boundsは、各サムネイルの（上端, 下端）。サムネイルの上半分にあればその前、
    下半分にあればその後ろとする。
    """
    for i, (top, bottom) in enumerate(slot_bounds):
        if y < (top + bottom) / 2:
            return i
    return len(slot_bounds)


def wheel_scroll_units(delta: int, aqua: bool) -> int:
    """マウスホイールのevent.deltaを、サムネイル一覧のスクロール行数に換算する（下向きが正）。

    WindowsのTkはホイール1刻みをdelta=±120で送るため、120で割る（従来どおり）。
    MacのTk（aqua。ソースから動かす場合。N-03）は、Tk 8.6では1刻みを±1程度の
    小さな値で送るため、120で割ると0になり一覧が動かない。そこでMacでは、
    120未満の値はそのまま行数とし、120以上（120刻みで送る版のTk）は120で割る。
    """
    if aqua and abs(delta) < 120:
        return -delta
    return int(-1 * (delta / 120))


def _is_self_or_descendant(widget: tk.Misc, ancestor: tk.Misc) -> bool:
    """widgetがancestor自身またはその子孫かどうかを、Tkのパス名の階層で判定する。"""
    widget_path, ancestor_path = str(widget), str(ancestor)
    return widget_path == ancestor_path or widget_path.startswith(ancestor_path + ".")


@dataclass(frozen=True)
class _RenderRequest:
    generation: int
    page_index: int
    is_large: bool
    raw: bool
    pdf_bytes: bytes
    profile: Profile
    options: ConvertOptions


def render_page_preview(pdf_bytes: bytes, page_index: int, profile: Profile,
                         options: ConvertOptions, dpi: int) -> Image.Image:
    """指定ページを選択中の処理内容を反映したプレビュー画像として生成する。

    core.convertの既存パイプラインを再利用する。拡大表示では本変換と同じdpiを
    渡すことで、二値化の閾値判定等も本変換と同一になる。
    表示用途のためRGBへ統一する（1bit/グレースケールいずれもPhotoImage化前提）。
    pdfiumはスレッドセーフでないため、事前診断・変換スレッドとの同時実行を
    PDFIUM_LOCKで直列化する。
    """
    with convert.PDFIUM_LOCK:
        doc = convert.open_document(pdf_bytes)
        try:
            page = doc[page_index]
            width_mm, height_mm = convert.get_page_size_mm(page)
            image = convert.rasterize_page(page, dpi)
        finally:
            doc.close()
    normalization = (
        convert.normalize_page(image, width_mm, height_mm, dpi, profile)
        if options.normalize_enabled
        else convert.PageNormalization(image, width_mm, height_mm, None, False, False, 1.0)
    )
    if options.mode == "binarize":
        bw, _ = convert.binarize_page(normalization.image, options.dither_method)
        return bw.convert("RGB")
    gray = convert.grayscale_page(normalization.image)
    return gray.convert("RGB")


def render_page_raw(pdf_bytes: bytes, page_index: int, dpi: int) -> Image.Image:
    """変換済みPDFのページを加工なしでラスタライズする（変換結果の実物表示用）。"""
    with convert.PDFIUM_LOCK:
        doc = convert.open_document(pdf_bytes)
        try:
            return convert.rasterize_page(doc[page_index], dpi)
        finally:
            doc.close()


class PreviewPane(ttk.Frame):
    """入力PDFのページプレビュー欄（F-30）。サムネイル一覧＋拡大表示。"""

    def __init__(self, master: tk.Misc):
        super().__init__(master)

        self._pdf_bytes: Optional[bytes] = None       # 現在表示中のPDF（入力または変換結果）
        self._sim_pdf_bytes: Optional[bytes] = None   # シミュレーション表示の元となる入力PDF
        self._raw_mode = False                        # True: 変換結果（実物）を表示中
        self._profile: Optional[Profile] = None
        self._current_options: Optional[ConvertOptions] = None
        self._page_count = 0
        self._generation = 0
        self._selected_index: Optional[int] = None    # 拡大表示しているページ

        # ページの選択と編集（F-33）。編集そのもの（ページの表の書き換え）は画面の側が行い、
        # ここは「どのページを、どうしたいか」を知らせるだけとする。
        self._selection: set[int] = set()
        self._anchor: Optional[int] = None            # Shiftでの範囲選択の起点
        self._editable = False
        self.on_delete: Optional[Callable[[list[int]], None]] = None       # 選んだページを取り除く
        self.on_move: Optional[Callable[[list[int], int], None]] = None    # 選んだページを境界へ移す
        self.on_selection_changed: Optional[Callable[[], None]] = None
        # サムネイル・一覧の余白を右クリックしたとき（画面の座標を渡す。メニューは画面の側が出す）
        self.on_context_menu: Optional[Callable[[int, int], None]] = None
        self.remove_cursor = theme.REMOVE_CURSOR_FALLBACK                  # 取り除きの間のカーソル
        self._press: Optional[tuple[int, int, int]] = None                 # 押したページ・位置
        self._press_widget: Optional[tk.Misc] = None
        self._collapse_on_release = False
        self._dragging = False
        self._drag_pointer = (0, 0)
        self._drag_drop_index: Optional[int] = None   # None＝欄の外（取り除き）
        self._drag_scroll_job: Optional[str] = None
        self._indicator: Optional[tk.Frame] = None

        self._thumbnail_images: dict[int, ImageTk.PhotoImage] = {}
        self._thumbnail_labels: dict[int, tk.Label] = {}
        self._thumbnail_slots: dict[int, ttk.Frame] = {}
        self._pending_thumbnails: set[int] = set()
        self._large_image: Optional[ImageTk.PhotoImage] = None
        self._building = False
        # ホイールの値（event.delta）の刻みがWindowsとMacで異なるため（wheel_scroll_units）
        self._aqua = theme.is_aqua(self)

        self._render_queue: "queue.Queue[Optional[_RenderRequest]]" = queue.Queue()
        self._result_queue: "queue.Queue[dict]" = queue.Queue()
        self._worker = threading.Thread(target=self._render_worker, daemon=True)
        self._worker.start()

        self._build_widgets()
        # ホイールでのページ送り（F-30）は、拡大表示欄のウィジェットへの直接バインド
        # だけでは不安定：Windowsではホイールイベントの配送先（フォーカスのある
        # ウィジェット／ポインタ直下のウィジェット）が状況により揺れ、入力ファイル欄の
        # 操作後などにイベントが拡大表示欄へ届かなくなる。bind_allで配送先を問わず
        # 受け取り、ポインタ位置が拡大表示欄内であればページ送りとして処理する
        # （拡大表示欄へ直接届いた場合はウィジェット側バインドが"break"を返すため、
        # 二重処理にはならない）。
        self.winfo_toplevel().bind_all("<MouseWheel>", self._on_any_mousewheel, add="+")
        self._poll_results()

    # ------------------------------------------------------------ 構築

    def _build_widgets(self) -> None:
        # サムネイル一覧（左・固定幅）と拡大表示（右・できるだけ大きく）を横に並べる。
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        thumb_frame = ttk.Frame(self)
        thumb_frame.grid(row=0, column=0, sticky="ns")
        thumb_frame.rowconfigure(0, weight=1)

        self._canvas = tk.Canvas(thumb_frame, bg=theme.COLOR_BG, highlightthickness=0,
                                  width=110, height=200, takefocus=1)
        scrollbar = ttk.Scrollbar(thumb_frame, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scrollbar.set)
        self._canvas.grid(row=0, column=0, sticky="ns")
        scrollbar.grid(row=0, column=1, sticky="ns")

        self._inner = ttk.Frame(self._canvas)
        self._canvas_window = self._canvas.create_window((0, 0), window=self._inner, anchor="nw")
        self._inner.bind("<Configure>", self._on_inner_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._canvas.bind("<MouseWheel>", self._on_mousewheel)
        self._canvas.bind("<Button-4>", self._on_mousewheel)
        self._canvas.bind("<Button-5>", self._on_mousewheel)
        # PageUp/PageDownで前後のページへ、Home/Endで先頭・末尾ページへ切り替える。
        # サムネイルをクリックした後や文書読込直後にキャンバスへフォーカスを移す
        # （select_page内でfocus_set）ことで、以降のキー操作を受け付ける。
        self._canvas.bind("<Prior>", self._on_page_up)
        self._canvas.bind("<Next>", self._on_page_down)
        self._canvas.bind("<Home>", self._on_home)
        self._canvas.bind("<End>", self._on_end)
        # ページの編集のキー操作（F-33）
        self._canvas.bind("<Shift-Prior>", lambda _e: self._extend_selection(-1))
        self._canvas.bind("<Shift-Next>", lambda _e: self._extend_selection(1))
        self._canvas.bind("<Control-a>", self._on_select_all)
        self._canvas.bind("<Control-A>", self._on_select_all)
        if self._aqua:  # Mac（N-03）では、⌘AもCtrl+Aと同じ働きにする
            self._canvas.bind("<Command-a>", self._on_select_all)
        self._canvas.bind("<Delete>", self._on_delete_key)
        self._canvas.bind("<BackSpace>", self._on_delete_key)
        self._canvas.bind("<Escape>", self._on_escape)
        # 一覧の余白の右クリック（選択は変えずに、メニューを出す）
        for sequence in self._context_sequences():
            self._canvas.bind(sequence, lambda e: self._on_thumb_context(None, e))

        large_frame = ttk.Frame(self, width=320, height=320)
        large_frame.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        # 拡大画像の実ピクセルサイズがレイアウトの最小サイズ要求として伝播しないよう、
        # このフレームのサイズは常に親（グリッドの列配分）が決める（画像サイズに引きずられない）。
        large_frame.grid_propagate(False)
        large_frame.columnconfigure(0, weight=1)
        large_frame.rowconfigure(0, weight=1)
        # 拡大表示欄の上でのマウスホイールはページ送りとする（サムネイル一覧側の
        # ホイールは一覧のスクロールに使うため、ここでは変えない）。
        large_frame.bind("<MouseWheel>", self._on_large_mousewheel)
        large_frame.bind("<Button-4>", self._on_large_mousewheel)
        large_frame.bind("<Button-5>", self._on_large_mousewheel)
        # ttk.Labelには-heightがなくwidth=0, height=0での画像追従サイズ調整（後述）が
        # できないため、classic tk.Labelのまま残す。
        self._large_label = tk.Label(large_frame, bg=theme.COLOR_BG)
        self._large_label.bind("<MouseWheel>", self._on_large_mousewheel)
        self._large_label.bind("<Button-4>", self._on_large_mousewheel)
        self._large_label.bind("<Button-5>", self._on_large_mousewheel)
        self._large_label.grid(row=0, column=0, sticky="nsew")

        self._status_var = tk.StringVar(value=EMPTY_STATUS_TEXT)
        ttk.Label(self, textvariable=self._status_var, foreground=theme.COLOR_MUTED, anchor="w").grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))

    # ------------------------------------------------------------ 外部API

    def set_document(self, pdf_bytes: bytes, profile: Profile, options: ConvertOptions,
                     selection: Optional[Iterable[int]] = None, active: Optional[int] = None) -> None:
        """表示する文書を切り替える。サムネイル一覧を作り直す。

        selection・activeは、作り直した後に選択しておくページ（ページの編集の後、動かした・
        足したページを選択した状態で示すため。省くと先頭のページ）。
        """
        self._sim_pdf_bytes = pdf_bytes
        self._profile = profile
        self._current_options = options
        self._raw_mode = False
        self._load_document(pdf_bytes, selection, active)

    def set_editable(self, editable: bool) -> None:
        """サムネイルでのページの編集（並べ替え・取り除き）を受け付けるか。"""
        self._editable = editable
        if not editable:
            self._cancel_drag()

    def active_page(self) -> Optional[int]:
        """拡大表示しているページ（0始まり）。"""
        return self._selected_index

    def external_drop_index(self, x_root: int, y_root: int) -> Optional[int]:
        """外（エクスプローラー等）からファイルをドラッグしているときの挿入位置を返し、線で示す。

        ポインタがサムネイル一覧の上にないときは、線を消してNoneを返す（末尾への追加として扱う）。
        """
        x = x_root - self._canvas.winfo_rootx()
        y = y_root - self._canvas.winfo_rooty()
        if (self._page_count == 0 or x < 0 or x > self._canvas.winfo_width()
                or y < 0 or y > self._canvas.winfo_height()):
            self._hide_indicator()
            return None
        bounds = self._slot_bounds()
        index = drop_index_for_y(bounds, self._canvas.canvasy(y))
        self._show_indicator(bounds, index)
        return index

    def hide_drop_indicator(self) -> None:
        self._hide_indicator()

    def selected_pages(self) -> list[int]:
        """選択中のページ（0始まり・昇順）。"""
        return sorted(self._selection)

    def select_all(self) -> None:
        if self._page_count:
            active = self._selected_index if self._selected_index is not None else 0
            self._apply_selection(set(range(self._page_count)), active, anchor=0, refresh_large=False)

    def show_converted_result(self, pdf_bytes: bytes) -> None:
        """変換完了後、実際の出力PDFをそのまま表示する（シミュレーションではなく実物）。

        容量超過時の処理方式による解像度・品質の低下も含め、出力された内容が
        そのまま見える。処理内容の選択が変更されたり、別の入力ファイルが選択されると
        入力PDFのシミュレーション表示に戻る。
        """
        if self._profile is None:
            return
        self._raw_mode = True
        self._load_document(pdf_bytes, self._selection, self._selected_index)

    def _load_document(self, pdf_bytes: bytes, selection: Optional[Iterable[int]] = None,
                       active: Optional[int] = None) -> None:
        """表示対象のPDFを読み込み、サムネイル一覧を作り直す。"""
        keep = set(selection or ())
        self._cancel_drag()
        self._pdf_bytes = pdf_bytes
        self._generation += 1
        self._selected_index = None
        self._selection = set()
        self._anchor = None
        self._indicator = None
        self._thumbnail_images.clear()
        self._pending_thumbnails.clear()
        for widget in self._inner.winfo_children():
            widget.destroy()
        self._thumbnail_labels.clear()
        self._thumbnail_slots.clear()
        self._large_label.configure(image="", text="")
        self._large_image = None

        # ページ数の取得はpypdf（純Python）で行う。pdfiumを使うとPDFIUM_LOCKの取得が
        # 必要になり、描画ワーカーが大きいページをレンダリング中の場合、本メソッドを
        # 実行するGUIスレッドがロック待ちで固まってしまうため。
        try:
            self._page_count = len(pypdf.PdfReader(io.BytesIO(pdf_bytes)).pages)
        except Exception:
            self._page_count = 0
            self._status_var.set("プレビューを生成できませんでした。")
            return

        # 構築中はConfigureイベント経由の_request_visible_rangeを抑止する
        # （scrollregion確定前のyview()は不正確な分数（0.0, 1.0）を返しうるため、
        # 全ページ分の描画要求が誤って発行されてしまう）。
        self._building = True
        for i in range(self._page_count):
            slot = ttk.Frame(self._inner, style=theme.THUMBNAIL_FRAME_STYLE)
            slot.grid(row=i, column=0, padx=8, pady=6)
            # ttk.Labelには-heightがなく、画像読込前の固定プレースホルダーサイズ
            # （width×height文字/行単位）を確保できないため、classic tk.Labelのまま残す。
            label = tk.Label(slot, text="…", bg=theme.COLOR_SURFACE, width=7, height=5)
            label.pack(padx=3, pady=(3, 0))
            # ページ番号（F-30）。並べ替えの後も、いまの並びでの番号を示す。
            number = tk.Label(slot, text=f"{i + 1}", bg=theme.COLOR_SURFACE, fg=theme.COLOR_MUTED)
            number.pack(pady=(0, 2))
            for widget in (slot, label, number):
                widget.bind("<ButtonPress-1>", lambda e, idx=i: self._on_thumb_press(idx, e))
                widget.bind("<B1-Motion>", self._on_thumb_motion)
                widget.bind("<ButtonRelease-1>", self._on_thumb_release)
                for sequence in self._context_sequences():
                    widget.bind(sequence, lambda e, idx=i: self._on_thumb_context(idx, e))
            self._thumbnail_labels[i] = label
            self._thumbnail_slots[i] = slot

        self._canvas.update_idletasks()
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        self._canvas.yview_moveto(0.0)
        # yview()の分数はscrollregion変更の即時反映を保証しないため、
        # 表示範囲を計算する前にidle処理を強制する。
        self._canvas.update_idletasks()
        self._building = False
        self._status_var.set(f"{self._mode_label()}：全{self._page_count}ページ")
        self._request_visible_range()
        keep = {i for i in keep if 0 <= i < self._page_count}
        if keep:
            if active not in keep:
                active = min(keep)
            self._apply_selection(keep, active, anchor=active)
        elif self._page_count:
            self.select_page(0, take_focus=False)

    def _mode_label(self) -> str:
        """ステータス欄の表示モードラベル。

        シミュレーション表示（入力PDF＋選択中の処理内容から生成した見込み）と、
        変換完了後の実物表示（出力PDFそのもの）のどちらを見ているかを、
        ステータス欄で常時区別できるようにする。
        """
        if self._raw_mode:
            return "保存した結果"
        if self._current_options is not None and self._current_options.mode == "none":
            return "そのままの表示（画像にしません）"
        return "変換イメージ"

    def clear(self) -> None:
        """入力が無効になった場合等、プレビューを空にする。"""
        self._pdf_bytes = None
        self._sim_pdf_bytes = None
        self._raw_mode = False
        self._profile = None
        self._current_options = None
        self._generation += 1
        self._page_count = 0
        self._cancel_drag()
        self._selected_index = None
        self._selection = set()
        self._anchor = None
        self._indicator = None
        self._thumbnail_images.clear()
        self._pending_thumbnails.clear()
        for widget in self._inner.winfo_children():
            widget.destroy()
        self._thumbnail_labels.clear()
        self._thumbnail_slots.clear()
        self._large_label.configure(image="", text="")
        self._large_image = None
        self._status_var.set(EMPTY_STATUS_TEXT)
        if self.on_selection_changed is not None:
            self.on_selection_changed()

    def update_options(self, options: ConvertOptions) -> None:
        """処理内容（圧縮方式・ディザ方式・用紙正規化）の変更を反映し、表示中の内容を再生成する。

        変換結果（実物）の表示中に処理内容が変更された場合は、変更後の内容での
        シミュレーション表示（入力PDFベース）に戻す。
        """
        if self._sim_pdf_bytes is None:
            return
        if self._raw_mode:
            self._current_options = options
            self._raw_mode = False
            self._load_document(self._sim_pdf_bytes, self._selection, self._selected_index)
            return
        if options == self._current_options:
            return
        self._current_options = options
        self._generation += 1
        self._thumbnail_images.clear()
        self._pending_thumbnails.clear()
        for label in self._thumbnail_labels.values():
            label.configure(image="", text="…")
        self._request_visible_range()
        if self._selected_index is not None:
            self._request_render(self._selected_index, is_large=True)

    def select_page(self, page_index: int, take_focus: bool = True) -> None:
        """指定ページを拡大表示欄に表示する。

        take_focus=Falseは文書読込時の自動選択（_load_document）用：診断完了の
        たびにキーボードフォーカスを奪うと、入力ファイル欄での操作中
        （Delete・Ctrl+Z等）が中断されてしまうため、利用者のプレビュー操作
        （サムネイルクリック・キー・ホイール）による選択の場合のみフォーカスを取る。
        """
        if page_index not in self._thumbnail_slots:
            return
        self._apply_selection({page_index}, page_index, anchor=page_index)
        if take_focus:
            # PageUp/PageDown/Home/Endによるページ切替を、以降このキャンバスで受け付ける。
            self._canvas.focus_set()

    def _apply_selection(self, selection: Iterable[int], active: Optional[int],
                         anchor: Optional[int] = None, refresh_large: bool = True) -> None:
        """選択（枠で示す）と、拡大表示するページ（active）を切り替える。"""
        new = {i for i in selection if i in self._thumbnail_slots}
        for i in self._selection - new:
            if i in self._thumbnail_slots:
                self._thumbnail_slots[i].configure(style=theme.THUMBNAIL_FRAME_STYLE)
        for i in new - self._selection:
            self._thumbnail_slots[i].configure(style=theme.THUMBNAIL_FRAME_SELECTED_STYLE)
        self._selection = new
        if anchor is not None:
            self._anchor = anchor
        if active is not None and active in self._thumbnail_slots:
            changed = active != self._selected_index
            self._selected_index = active
            if len(new) > 1:
                self._status_var.set(
                    f"{self._mode_label()}：{active + 1} / {self._page_count} ページ目（{len(new)}ページを選択中）")
            else:
                self._status_var.set(f"{self._mode_label()}：{active + 1} / {self._page_count} ページ目")
            if changed or refresh_large:
                self._large_label.configure(image="", text="読み込み中…")
                self._request_render(active, is_large=True)
            self._scroll_thumbnail_into_view(active)
        if self.on_selection_changed is not None:
            self.on_selection_changed()

    # ------------------------------------------------------------ ページの選択・並べ替え・取り除き（F-33）

    def _extend_selection(self, step: int) -> str:
        """Shift+PageUp／PageDown：選択の範囲を、起点から1ページずつ広げる・縮める。"""
        if self._selected_index is None:
            return "break"
        target = max(0, min(self._page_count - 1, self._selected_index + step))
        selection, anchor = click_selection(self._selection, self._anchor, target, True, False)
        self._apply_selection(selection, target, anchor=anchor)
        return "break"

    def _on_select_all(self, _event=None) -> str:
        self.select_all()
        return "break"

    def _on_delete_key(self, _event=None) -> str:
        self._request_delete()
        return "break"

    def _request_delete(self) -> None:
        if self._editable and self.on_delete is not None and self._selection:
            self.on_delete(sorted(self._selection))

    def _on_escape(self, _event=None) -> str:
        self._cancel_drag()
        return "break"

    def _context_sequences(self) -> tuple[str, ...]:
        """右クリックにあたる操作。Macでは、2本指のクリック（Button-2）とControl+クリックも同じ。"""
        return ("<Button-3>", "<Button-2>", "<Control-Button-1>") if self._aqua else ("<Button-3>",)

    def _on_thumb_context(self, index: Optional[int], event) -> str:
        """右クリック：選択していないページの上なら、そのページだけを選んでから、メニューを出す
        （選択中のページの上なら、選択を保つ。エクスプローラー等と同じ）。"""
        self._cancel_drag()
        if index is not None and index not in self._selection:
            self._apply_selection({index}, index, anchor=index, refresh_large=False)
        self._canvas.focus_set()
        if self.on_context_menu is not None:
            self.on_context_menu(event.x_root, event.y_root)
        return "break"

    def _on_thumb_press(self, index: int, event) -> str:
        shift = bool(event.state & _SHIFT_MASK)
        ctrl = bool(event.state & _CONTROL_MASK)
        self._press = (index, event.x_root, event.y_root)
        self._press_widget = event.widget
        self._collapse_on_release = False
        if not shift and not ctrl and index in self._selection and len(self._selection) > 1:
            # 選択中の複数ページの中を押した：そのまま動かすかもしれないので、離すまで選択を保つ
            self._collapse_on_release = True
            self._apply_selection(self._selection, index, refresh_large=False)
        else:
            selection, anchor = click_selection(self._selection, self._anchor, index, shift, ctrl)
            self._apply_selection(selection, index, anchor=anchor, refresh_large=False)
        self._canvas.focus_set()
        return "break"

    def _on_thumb_motion(self, event) -> str:
        if self._press is None or not self._editable or not self._selection:
            return "break"
        if not self._dragging:
            _index, x0, y0 = self._press
            if abs(event.x_root - x0) < DRAG_START_PX and abs(event.y_root - y0) < DRAG_START_PX:
                return "break"
            self._dragging = True
        self._drag_pointer = (event.x_root, event.y_root)
        self._update_drag_feedback()
        return "break"

    def _on_thumb_release(self, _event=None) -> str:
        press, self._press = self._press, None
        if self._dragging:
            drop_index = self._drag_drop_index
            pages = sorted(self._selection)
            self._cancel_drag()
            if drop_index is None:
                self._request_delete()
            elif self.on_move is not None:
                self.on_move(pages, drop_index)
        elif self._collapse_on_release and press is not None:
            self._apply_selection({press[0]}, press[0], anchor=press[0])
        self._collapse_on_release = False
        return "break"

    def _slot_bounds(self) -> list[tuple[float, float]]:
        bounds = []
        for i in range(self._page_count):
            slot = self._thumbnail_slots[i]
            bounds.append((slot.winfo_y(), slot.winfo_y() + slot.winfo_height()))
        return bounds

    def _update_drag_feedback(self) -> None:
        """ドラッグ中の表示：欄の中なら挿入位置の線、欄の外なら取り除きのカーソル。"""
        x = self._drag_pointer[0] - self._canvas.winfo_rootx()
        y = self._drag_pointer[1] - self._canvas.winfo_rooty()
        width, height = self._canvas.winfo_width(), self._canvas.winfo_height()
        margin = DRAG_OUTSIDE_MARGIN_PX
        outside = x < -margin or x > width + margin or y < -margin or y > height + margin
        if outside:
            self._drag_drop_index = None
            self._hide_indicator()
            self._set_drag_cursor(self.remove_cursor)
            self._stop_drag_scroll()
            return
        self._set_drag_cursor("")
        bounds = self._slot_bounds()
        self._drag_drop_index = drop_index_for_y(bounds, self._canvas.canvasy(y))
        self._show_indicator(bounds, self._drag_drop_index)
        if y < DRAG_SCROLL_ZONE_PX or y > height - DRAG_SCROLL_ZONE_PX:
            if self._drag_scroll_job is None:
                self._drag_scroll_job = self.after(DRAG_SCROLL_INTERVAL_MS, self._on_drag_scroll_tick)
        else:
            self._stop_drag_scroll()

    def _on_drag_scroll_tick(self) -> None:
        self._drag_scroll_job = None
        if not self._dragging:
            return
        y = self._drag_pointer[1] - self._canvas.winfo_rooty()
        self._canvas.yview_scroll(-1 if y < DRAG_SCROLL_ZONE_PX else 1, "units")
        self._request_visible_range()
        self._update_drag_feedback()

    def _stop_drag_scroll(self) -> None:
        if self._drag_scroll_job is not None:
            self.after_cancel(self._drag_scroll_job)
            self._drag_scroll_job = None

    def _show_indicator(self, bounds: Sequence[tuple[float, float]], drop_index: int) -> None:
        """挿入位置を、サムネイルの間の線（アクセント色）で示す。"""
        if not bounds:
            return
        if drop_index < len(bounds):
            y = bounds[drop_index][0] - 7
        else:
            y = bounds[-1][1] + 5
        if self._indicator is None or not self._indicator.winfo_exists():
            self._indicator = tk.Frame(self._inner, bg=theme.COLOR_ACCENT, height=2)
        self._indicator.place(x=6, y=max(0, int(y)), relwidth=1.0, width=-12, height=2)
        self._indicator.lift()

    def _hide_indicator(self) -> None:
        if self._indicator is not None and self._indicator.winfo_exists():
            self._indicator.place_forget()

    def _set_drag_cursor(self, cursor: str) -> None:
        # ボタンを押している間は、押したウィジェットのカーソルが表示される
        widget = self._press_widget
        if widget is not None:
            try:
                widget.configure(cursor=cursor)
            except tk.TclError:
                pass

    def _cancel_drag(self) -> None:
        """ドラッグを取りやめる（Escape・文書の切り替え）。選択は変えない。"""
        self._stop_drag_scroll()
        self._hide_indicator()
        self._set_drag_cursor("")
        self._dragging = False
        self._drag_drop_index = None
        self._press = None
        self._press_widget = None

    def _on_page_up(self, _event=None) -> str:
        if self._selected_index is not None and self._selected_index > 0:
            self.select_page(self._selected_index - 1)
        return "break"

    def _on_page_down(self, _event=None) -> str:
        if self._selected_index is not None and self._selected_index < self._page_count - 1:
            self.select_page(self._selected_index + 1)
        return "break"

    def _on_home(self, _event=None) -> str:
        if self._page_count:
            self.select_page(0)
        return "break"

    def _on_end(self, _event=None) -> str:
        if self._page_count:
            self.select_page(self._page_count - 1)
        return "break"

    def _scroll_thumbnail_into_view(self, page_index: int) -> None:
        """選択中のサムネイルがスクロール範囲外の場合、見える位置までスクロールする。"""
        slot = self._thumbnail_slots.get(page_index)
        if slot is None:
            return
        bbox = self._canvas.bbox("all")
        if not bbox:
            return
        total_height = bbox[3] - bbox[1]
        if total_height <= 0:
            return
        slot_top = slot.winfo_y()
        slot_bottom = slot_top + slot.winfo_height()
        view_height = self._canvas.winfo_height()
        view_top = self._canvas.yview()[0] * total_height
        view_bottom = view_top + view_height

        if slot_top < view_top:
            self._canvas.yview_moveto(slot_top / total_height)
            self._request_visible_range()
        elif slot_bottom > view_bottom:
            self._canvas.yview_moveto(max(0.0, (slot_bottom - view_height)) / total_height)
            self._request_visible_range()

    def _on_any_mousewheel(self, event) -> Optional[str]:
        """全ウィジェット到達分のホイールイベントから、プレビュー欄上の操作を拾う。

        イベントの配送先ウィジェットではなく、ポインタの実際の位置（x_root/y_root）で
        判定する。拡大表示欄内ならページ送り、サムネイル一覧内（サムネイル画像の
        ラベル上を含む。ホイール処理はキャンバス自体にしかバインドしていないため、
        ラベルに配送された場合はここで拾う必要がある）なら一覧のスクロールとする。
        いずれの領域でもない場合（入力欄等）は何もせず、各ウィジェット既存の動作に
        任せる。
        """
        if self._page_count == 0:
            return None
        try:
            under = self.winfo_containing(event.x_root, event.y_root)
        except (KeyError, tk.TclError):
            return None
        if under is None:
            return None
        if _is_self_or_descendant(under, self._large_label.master):
            return self._on_large_mousewheel(event)
        if _is_self_or_descendant(under, self._canvas):
            return self._on_mousewheel(event)
        return None

    def _on_large_mousewheel(self, event) -> str:
        """拡大表示欄の上でのマウスホイールでページを送る（上＝前ページ、下＝次ページ）。"""
        if getattr(event, "num", None) == 4:
            self._on_page_up()
        elif getattr(event, "num", None) == 5:
            self._on_page_down()
        elif self._aqua and event.delta == 0:
            # Macのトラックパッドは向きを持たない（delta=0の）イベントも送ることがあり、
            # 次ページへの送りと誤らないよう無視する（Windowsの動きは変えないためMacに限る）。
            pass
        elif event.delta > 0:
            self._on_page_up()
        else:
            self._on_page_down()
        return "break"

    def destroy(self) -> None:  # noqa: D102 - tkinter標準メソッドのオーバーライド
        self._render_queue.put(None)
        # デーモンスレッドがpdfium（ネイティブコード）実行中のままプロセス終了すると
        # アクセス違反になるため、終了指示後、描画中の1件が終わるのを短時間待つ。
        self._worker.join(timeout=3.0)
        super().destroy()

    # ------------------------------------------------------------ スクロール・遅延読込

    def _on_inner_configure(self, _event=None) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        self._request_visible_range()

    def _on_canvas_configure(self, event) -> None:
        self._canvas.itemconfigure(self._canvas_window, width=event.width)
        self._request_visible_range()

    def _on_mousewheel(self, event) -> str:
        """サムネイル一覧をスクロールする。

        "break"を返してバインドタグの後続処理を止める（イベントがキャンバスへ直接
        配送された場合に、bind_allの_on_any_mousewheelが重ねて発火して二重スクロール
        になることを防ぐ）。
        """
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            delta = wheel_scroll_units(event.delta, self._aqua)
        self._canvas.yview_scroll(delta, "units")
        self._request_visible_range()
        return "break"

    def _request_visible_range(self) -> None:
        if self._building or self._page_count == 0 or self._pdf_bytes is None:
            return
        top_frac, bottom_frac = self._canvas.yview()
        first = max(0, int(top_frac * self._page_count) - VISIBLE_BUFFER)
        last = min(self._page_count - 1, int(bottom_frac * self._page_count) + VISIBLE_BUFFER)
        for i in range(first, last + 1):
            if i not in self._thumbnail_images and i not in self._pending_thumbnails:
                self._request_render(i, is_large=False)

    # ------------------------------------------------------------ レンダリング

    def _request_render(self, page_index: int, is_large: bool) -> None:
        if self._pdf_bytes is None or self._profile is None or self._current_options is None:
            return
        if not is_large:
            self._pending_thumbnails.add(page_index)
        self._render_queue.put(_RenderRequest(
            generation=self._generation, page_index=page_index, is_large=is_large,
            raw=self._raw_mode, pdf_bytes=self._pdf_bytes, profile=self._profile,
            options=self._current_options,
        ))

    def _render_worker(self) -> None:
        while True:
            request = self._render_queue.get()
            if request is None:
                return
            if request.generation != self._generation:
                # 世代が古い要求は、結果側でも破棄されるため描画自体を省略する。
                self._result_queue.put({
                    "generation": request.generation, "page_index": request.page_index,
                    "is_large": request.is_large, "image": None,
                })
                continue
            # 拡大表示は本変換と同じ解像度（プロファイルの既定dpi）で処理し、
            # 表示時にのみ縮小する（低解像度で処理すると大津法・自動ディザ判定の
            # 結果が本変換と食い違うため）。サムネイルはページ探し用の簡易表示の
            # ため低解像度のままとする。
            dpi = request.profile.resolution_dpi_default if request.is_large else THUMBNAIL_DPI
            try:
                # 圧縮方式「何もしない」（F-11）のときは、材料のページをそのまま表示する
                if request.raw or request.options.mode == "none":
                    image = render_page_raw(request.pdf_bytes, request.page_index, dpi)
                else:
                    image = render_page_preview(
                        request.pdf_bytes, request.page_index, request.profile, request.options, dpi)
            except Exception:
                image = None
            self._result_queue.put({
                "generation": request.generation, "page_index": request.page_index,
                "is_large": request.is_large, "image": image,
            })

    def _poll_results(self) -> None:
        if not self.winfo_exists():
            return
        try:
            while True:
                message = self._result_queue.get_nowait()
                self._handle_result(message)
        except queue.Empty:
            pass
        self.after(POLL_INTERVAL_MS, self._poll_results)

    def _fit_to_large_frame(self, image: Image.Image) -> Image.Image:
        """拡大表示欄の実際の表示領域に収まるよう画像を縮小する（等比縮小・中央表示前提）。

        本変換と同じdpiでの実ピクセルサイズをそのまま表示すると欄の外に大きくはみ出し、
        グリッドレイアウトの最小サイズ要求としてサムネイル一覧の領域を圧迫するため、
        表示直前に欄のサイズへ縮小する（欄自体のサイズはgrid_propagate(False)により
        画像サイズの影響を受けない。この縮小は完成した結果画像の表示上のズームであり、
        二値化等の判定には影響しない）。
        """
        frame = self._large_label.master
        width = max(50, frame.winfo_width() - 8)
        height = max(50, frame.winfo_height() - 8)
        fitted = image.copy()
        fitted.thumbnail((width, height), Image.LANCZOS)
        return fitted

    def _handle_result(self, message: dict) -> None:
        if message["generation"] != self._generation:
            return
        page_index = message["page_index"]
        image = message["image"]
        if message["is_large"]:
            if page_index != self._selected_index:
                return
            if image is not None:
                fitted = self._fit_to_large_frame(image)
                photo = ImageTk.PhotoImage(fitted)
                self._large_image = photo
                self._large_label.configure(image=photo, text="", width=0, height=0)
            else:
                self._large_label.configure(image="", text="プレビューを生成できませんでした")
            return

        self._pending_thumbnails.discard(page_index)
        label = self._thumbnail_labels.get(page_index)
        if label is None:
            return
        if image is not None:
            photo = ImageTk.PhotoImage(image)
            label.configure(width=0, height=0)
            self._thumbnail_images[page_index] = photo
            label.configure(image=photo, text="")
        else:
            label.configure(image="", text="エラー")
