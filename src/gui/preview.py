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
from typing import Optional

import pypdf
from PIL import Image, ImageTk

from config.profiles import Profile
from core import convert
from core.convert import ConvertOptions
from gui import theme

THUMBNAIL_DPI = 10
VISIBLE_BUFFER = 3  # 表示範囲の前後、先読みするページ数
POLL_INTERVAL_MS = 80


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
        self._selected_index: Optional[int] = None

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

        self._status_var = tk.StringVar(value="ファイルを選択するとプレビューを表示します。")
        ttk.Label(self, textvariable=self._status_var, foreground=theme.COLOR_MUTED, anchor="w").grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))

    # ------------------------------------------------------------ 外部API

    def set_document(self, pdf_bytes: bytes, profile: Profile, options: ConvertOptions) -> None:
        """新しい入力ファイルに切り替える。サムネイル一覧を作り直す。"""
        self._sim_pdf_bytes = pdf_bytes
        self._profile = profile
        self._current_options = options
        self._raw_mode = False
        self._load_document(pdf_bytes)

    def show_converted_result(self, pdf_bytes: bytes) -> None:
        """変換完了後、実際の出力PDFをそのまま表示する（シミュレーションではなく実物）。

        容量超過時の処理方式による解像度・品質の低下も含め、出力された内容が
        そのまま見える。処理内容の選択が変更されたり、別の入力ファイルが選択されると
        入力PDFのシミュレーション表示に戻る。
        """
        if self._profile is None:
            return
        self._raw_mode = True
        self._load_document(pdf_bytes)

    def _load_document(self, pdf_bytes: bytes) -> None:
        """表示対象のPDFを読み込み、サムネイル一覧を作り直す。"""
        self._pdf_bytes = pdf_bytes
        self._generation += 1
        self._selected_index = None
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
            label = tk.Label(slot, text=f"{i + 1}", bg=theme.COLOR_SURFACE, width=7, height=5)
            label.pack(padx=3, pady=3)
            label.bind("<Button-1>", lambda _e, idx=i: self.select_page(idx))
            slot.bind("<Button-1>", lambda _e, idx=i: self.select_page(idx))
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
        if self._page_count:
            self.select_page(0, take_focus=False)

    def _mode_label(self) -> str:
        """ステータス欄の表示モードラベル。

        シミュレーション表示（入力PDF＋選択中の処理内容から生成した見込み）と、
        変換完了後の実物表示（出力PDFそのもの）のどちらを見ているかを、
        ステータス欄で常時区別できるようにする。
        """
        return "変換結果" if self._raw_mode else "変換イメージ"

    def clear(self) -> None:
        """入力が無効になった場合等、プレビューを空にする。"""
        self._pdf_bytes = None
        self._sim_pdf_bytes = None
        self._raw_mode = False
        self._profile = None
        self._current_options = None
        self._generation += 1
        self._page_count = 0
        self._selected_index = None
        self._thumbnail_images.clear()
        self._pending_thumbnails.clear()
        for widget in self._inner.winfo_children():
            widget.destroy()
        self._thumbnail_labels.clear()
        self._thumbnail_slots.clear()
        self._large_label.configure(image="", text="")
        self._large_image = None
        self._status_var.set("ファイルを選択するとプレビューを表示します。")

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
            self._load_document(self._sim_pdf_bytes)
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
        previous = self._selected_index
        self._selected_index = page_index
        if previous is not None and previous in self._thumbnail_slots:
            self._thumbnail_slots[previous].configure(style=theme.THUMBNAIL_FRAME_STYLE)
        self._thumbnail_slots[page_index].configure(style=theme.THUMBNAIL_FRAME_SELECTED_STYLE)
        self._status_var.set(f"{self._mode_label()}：{page_index + 1} / {self._page_count} ページ目")
        self._large_label.configure(image="", text="読み込み中…")
        self._request_render(page_index, is_large=True)
        self._scroll_thumbnail_into_view(page_index)
        if take_focus:
            # PageUp/PageDown/Home/Endによるページ切替を、以降このキャンバスで受け付ける。
            self._canvas.focus_set()

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
                if request.raw:
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
