# -*- coding: utf-8 -*-
"""VA FAXのOffice風フラットデザイン共通スタイル定義。

Word等Microsoft製品のダイアログに近い配色・質感（ニュートラルな灰白色の地に、
ホバー・フォーカス時のみ青系を差し色として使う）を採用する。色・書体等の
見た目に関する定数と、ttkウィジェットへのスタイル適用をここに集約する。

GUIの部品はttk（テーマ付きウィジェット）を基本とする。classic tkウィジェットは
枠線色等を独立指定する手段がなく、-highlightbackground等による代替表現は
Windows実機では表示されないことがある一方、ttkは'clam'テーマのbordercolor等で
見た目を直接・確実に指定できる。ただしttkに対応品がないウィジェット
（Text・Canvas・Menu・Toplevel・PanedWindow）、画像表示のため高さをピクセル指定
する必要があるLabel（プレビューのサムネイル・拡大表示）、および'clam'テーマの
チェック時の見た目が「✕」形状になるCheckbuttonはclassic tkのまま残し、
option_add（Tkのオプションデータベース）で最小限の既定色を与える。
"""
from __future__ import annotations

import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk

from version import FILE_STEM

# ---- パレット（Office風）。名前は雛形（標準\雛形\python の theme.py）と同じにする -----------------
WINDOW_BG = "#f0f0f0"      # ウィンドウ・フレームの背景
FIELD_BG = "#ffffff"       # 入力欄・テキスト欄・サムネイル等の背景
CONTROL_BG = "#fdfdfd"     # ボタンの通常時の地色（白寄り）
HOVER_BG = "#e0eef9"       # ボタンのホバー時の地色（淡い青）
PRESSED_BG = "#cce4f7"     # ボタンの押下時の地色（濃い青）
TEXT = "#1a1a1a"           # 通常テキスト
NOTE = "#666666"           # 補足・注記テキスト
BORDER = "#c8c8c8"         # 枠線・区切り線
ACCENT = "#0078d4"         # アクセント（フォーカス枠線・主要ボタンの強調枠）
DISABLED_TEXT = "#a0a0a0"  # 押せないときの字（標準 02「テーマとウィジェット」）
RIBBON_HOVER_BORDER = "#a9cdee"     # リボンのボタンにマウスを重ねたときの枠線
SCROLL_THUMB, SCROLL_THUMB_ACTIVE = "#c1c1c1", "#a8a8a8"

# ---- VA FAX だけの色（雛形にないもの）
PLACEHOLDER = "#999999"    # プレースホルダー文字色
WARNING = "#a05a00"        # 注意喚起テキスト（参考プロファイル注記等）
# リボン（ribbon.py）のアイコンは、VA Case・VA mints・VA Documentと同じ2色の形とする。
ICON_TINT = "#9cc9f0"           # アイコンの塗りつぶし版を敷く色（薄い青）
ICON_TINT_DISABLED = "#e2e2e2"  # 押せないときの、塗りつぶし版の色
DISABLED_BG = "#f0f0f0"    # 押せない部品の地（ウィンドウ地色に同化）
SCROLL_TROUGH = "#f0f0f0"  # スクロールバーの溝

# ---- 画面の書体（標準 02「テーマとウィジェット」。Windows の画面と同じ Yu Gothic UI 9pt）
UI_FONT_FAMILY = "Yu Gothic UI"
UI_FONT_SIZE = 9

_ACCENT_BUTTON_STYLE = "Accent.TButton"
_NORMAL_BUTTON_STYLE = "TButton"

# リボン（ribbon.py）のスタイル名
RIBBON_FRAME_STYLE = "Ribbon.TFrame"
RIBBON_LABEL_STYLE = "Ribbon.TLabel"
RIBBON_GROUP_LABEL_STYLE = "RibbonGroup.TLabel"
RIBBON_NOTE_LABEL_STYLE = "RibbonNote.TLabel"
RIBBON_BUTTON_STYLE = "Ribbon.TButton"
RIBBON_LARGE_BUTTON_STYLE = "RibbonLarge.TButton"
RIBBON_MENUBUTTON_STYLE = "Ribbon.TMenubutton"
RIBBON_ACCENT_BUTTON_STYLE = "RibbonAccent.TButton"

# 同梱カーソル（trash.cur）を読み込めない環境で使う代替カーソル
REMOVE_CURSOR_FALLBACK = "X_cursor"

THUMBNAIL_FRAME_STYLE = "Thumbnail.TFrame"
THUMBNAIL_FRAME_SELECTED_STYLE = "ThumbnailSelected.TFrame"


def apply_ui_font(root: tk.Misc) -> None:
    """画面の書体を Yu Gothic UI 9pt にする（Tk の名前付きの書体を変えるので、画面のすべての部品に及ぶ）。

    その書体がない環境（Mac など）では、Tk の既定の書体のままにする。
    """
    if UI_FONT_FAMILY not in set(tkfont.families(root)):
        return
    for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont", "TkCaptionFont",
                 "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont"):
        try:
            tkfont.nametofont(name, root=root).configure(family=UI_FONT_FAMILY, size=UI_FONT_SIZE)
        except tk.TclError:
            continue


def icon_path() -> Path:
    """アプリのアイコン（assets/VA_FAX.ico）の場所。exe では展開先（sys._MEIPASS）の assets、
    ソースでは作業フォルダの assets から読む（雛形と同じ置き場。標準 02「フォルダの構成」）。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / "assets" / f"{FILE_STEM}.ico"


def apply_app_icon(window: tk.Tk) -> None:
    """アプリケーションアイコン（VA／FAXの2行テキスト）をウィンドウに適用する。

    default指定により、このウィンドウから開かれる各Toplevelダイアログにも
    自動的に同じアイコンが適用される。アイコンはbuild/make_icon.pyで生成し、
    assets/VA_FAX.icoとしてexeにも同梱される（build/va_fax.spec）。
    見つからない場合（開発中の未生成等）は既定アイコンのまま起動を続ける。

    Windows以外（Macでソースから動かす場合。N-03）のTkは.icoをiconbitmapで
    受け付けず、TclErrorで起動できなくなるため、Pillowで読み込んだ画像を
    iconphotoで与える（Macではアプリのアイコン〔Dock〕になる）。読み込めない場合は
    既定アイコンのまま起動を続ける。
    """
    path = icon_path()
    if not path.is_file():
        return
    if windowing_system(window) == "win32":
        window.iconbitmap(default=str(path))
        return
    try:
        from PIL import Image, ImageTk

        with Image.open(path) as image:
            photo = ImageTk.PhotoImage(image.convert("RGBA"), master=window)
        window.iconphoto(True, photo)
    except (ImportError, OSError, tk.TclError):
        return
    # PhotoImageは参照が無くなると破棄され、アイコンが消えるため、ウィンドウに持たせる。
    window._va_fax_icon_photo = photo  # type: ignore[attr-defined]


def windowing_system(widget: tk.Misc) -> str:
    """Tkのウィンドウシステム名を返す（Windows="win32"、Mac="aqua"、Linux等="x11"）。

    Mac固有のキー（Command）・ホイールの値などの切替に使う。
    """
    return str(widget.tk.call("tk", "windowingsystem"))


def is_aqua(widget: tk.Misc) -> bool:
    """MacのTk（aqua）で動いているかを返す。"""
    return windowing_system(widget) == "aqua"


def scale(widget: tk.Misc) -> float:
    """画面の拡大率（96 dpi を 1.0 とする）。ピクセルで決めた幅・高さに掛けて使う。"""
    try:
        return max(1.0, widget.winfo_fpixels("1i") / 96.0)
    except tk.TclError:
        return 1.0


def px(widget: tk.Misc, n: int) -> int:
    """96 dpi のときの n ピクセルを、いまの画面のピクセル数にする。"""
    return int(round(n * scale(widget)))


# 親のウィンドウがこれより小さいとき（初回のサービス選択の、1x1の仮のウィンドウ等）は、画面の中央に出す
_PLACE_MIN_PARENT_PX = 200


def place_over(window: tk.Toplevel, master: "tk.Misc | None") -> None:
    """自作の画面（ダイアログ）を、親のウィンドウの中央（縦は上から1/3）に出す（2026-11-18 班長指摘：
    位置を指定しないと、Windows の既定どおり画面の左上に出る）。

    出す前に一度隠して大きさを決め、位置を決めてから出す（左上に一瞬出てから動くことがないように）。
    親が隠れているとき（自動テストの、画面に出さないメイン画面など）は、隠したままにする（親が
    出たときに、Tk が一緒に出す）。親が小さい・ないときは、画面の中央に出す。
    """
    window.withdraw()
    window.update_idletasks()
    width, height = window.winfo_reqwidth(), window.winfo_reqheight()
    parent = master.winfo_toplevel() if master is not None else None
    parent_shown = parent is not None and bool(parent.winfo_viewable())
    if parent_shown and min(parent.winfo_width(), parent.winfo_height()) >= _PLACE_MIN_PARENT_PX:
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 3
        x, y = max(parent.winfo_rootx(), x), max(parent.winfo_rooty(), y)
    else:
        x = max(0, (window.winfo_screenwidth() - width) // 2)
        y = max(0, (window.winfo_screenheight() - height) // 3)
    window.geometry(f"+{x}+{y}")
    if parent is None or parent_shown:
        window.deiconify()


def resolve_remove_cursor(widget: tk.Misc) -> str:
    """「一覧から取り除く」操作中に使うマウスカーソルの指定を返す（F-15）。

    同梱のtrash.cur（矢印＋ゴミ箱。build/make_cursor.pyで生成し、exeにも
    resources/trash.curとして同梱する）を読み込み、そのカーソル指定を返す。
    読み込めない環境では標準のX_cursor（✕）に切り替える（表示が変わるだけで
    取り除く操作自体は同じように行えるため、起動はそのまま続ける）。

    Tkの「@ファイル名」形式のカーソル指定はWindowsのANSI系API経由で読み込まれるため、
    日本語を含むパスを絶対パスで渡すと読み込みに失敗する（利用者のユーザー名が
    日本語の場合、exeの展開先も日本語を含みうる）。そこで読み込みの瞬間だけ
    カレントディレクトリをリソースのフォルダへ移し、ASCIIのファイル名だけで
    読み込ませる。読み込んだカーソルは、参照を持つウィジェットが1つも無くなると
    Tkが解放してしまい、次に指定したときに相対パスで読み直そうとして失敗するため、
    配置しない小さなウィジェットに常時持たせておく。

    Windows以外（Mac等）のTkは.curを読めないことがあり、代替のX_cursorも
    MacのTkには無い場合があるため、代替も使えなければTkの既定のカーソル（""）とする。
    """
    path = Path(__file__).resolve().parent.parent / "resources" / "trash.cur"
    if not path.is_file():
        return _fallback_remove_cursor(widget)

    spec = f"@{path.name}"
    holder = tk.Frame(widget.winfo_toplevel(), width=1, height=1)  # 参照保持用（配置しない）
    previous_cwd = os.getcwd()
    try:
        os.chdir(path.parent)
        holder.configure(cursor=spec)
    except (tk.TclError, OSError):
        holder.destroy()
        return _fallback_remove_cursor(widget)
    finally:
        os.chdir(previous_cwd)
    return spec


def _fallback_remove_cursor(widget: tk.Misc) -> str:
    """同梱カーソルを使えない場合の代替カーソル（使えなければTkの既定のカーソル）を返す。"""
    probe = tk.Frame(widget.winfo_toplevel(), width=1, height=1)  # 試し用（配置しない）
    try:
        probe.configure(cursor=REMOVE_CURSOR_FALLBACK)
    except tk.TclError:
        return ""
    finally:
        probe.destroy()
    return REMOVE_CURSOR_FALLBACK


def create_button(parent: tk.Misc, accent: bool = False, **kwargs) -> ttk.Button:
    """ボタンを作成する（ttk.Button）。

    accent=Trueの場合（決定・保存等の主要な操作ボタン）は、
    枠線をアクセント色にしたスタイルを適用し、他のボタンと区別する
    （地色を塗りつぶす方式にはしない）。地色・ホバー・押下時の色変化は
    apply_theme()で設定したttk.Styleにより自動的に反映される。
    """
    style_name = _ACCENT_BUTTON_STYLE if accent else _NORMAL_BUTTON_STYLE
    kwargs.setdefault("cursor", "hand2")
    return ttk.Button(parent, style=style_name, **kwargs)


def apply_theme(root: tk.Misc) -> None:
    """Office風フラットデザインの既定色を適用する。

    ttkウィジェットはttk.Styleで指定する（本関数の主眼）。ttkに対応品がなく
    classic tkのまま残るウィジェット（Text・Canvas・Menu・Toplevel・PanedWindow、
    および画像表示のため高さをピクセル指定する必要があるLabel）は、
    option_add（Tkのオプションデータベース）で最小限の既定色を与える。
    個々のウィジェット生成箇所での明示的な色指定（プレースホルダー・警告文言等）は
    そのまま優先される（option_addは「指定がない場合の既定値」のため）。

    root（TkまたはToplevel）と同じTclインタプリタ内の全ウィジェットに適用されるため、
    メインウィンドウで1度呼び出せば、そこから開く各ダイアログにも自動的に反映される。
    ただし独立したtk.Tk()（別インタプリタ）を使う画面（初回起動時のプロファイル選択等）
    では、その都度呼び出す必要がある。
    """
    apply_ui_font(root)
    root.option_add("*Background", WINDOW_BG)
    root.option_add("*Foreground", TEXT)

    root.option_add("*Text.background", FIELD_BG)
    root.option_add("*Text.foreground", TEXT)
    root.option_add("*Text.relief", "flat")
    root.option_add("*Text.highlightThickness", 1)
    root.option_add("*Text.highlightBackground", BORDER)
    root.option_add("*Text.highlightColor", ACCENT)

    root.option_add("*Menu.background", FIELD_BG)
    root.option_add("*Menu.foreground", TEXT)
    root.option_add("*Menu.activeBackground", HOVER_BG)
    root.option_add("*Menu.activeForeground", TEXT)
    root.option_add("*Menu.relief", "flat")

    # チェックボックスはclassic tk.Checkbuttonのまま残す（'clam'テーマのttk版は
    # チェック時の見た目が「✕」形状になり、利用不可のように誤解されるため）。
    root.option_add("*Checkbutton.selectColor", FIELD_BG)
    root.option_add("*Checkbutton.activeBackground", WINDOW_BG)
    root.option_add("*Checkbutton.activeForeground", TEXT)
    root.option_add("*Checkbutton.disabledForeground", DISABLED_TEXT)

    style = ttk.Style(root)
    # 'clam'はOSネイティブ装飾（立体的な縁取り・グラデーション等）を持たない、
    # 最もフラットに設定しやすい組込みテーマ。
    style.theme_use("clam")

    # 既定の背景・文字色（"."は全ttkスタイルの基底であり、個別指定がない限り継承される）。
    style.configure(".", background=WINDOW_BG, foreground=TEXT)
    style.configure("TFrame", background=WINDOW_BG)
    style.configure("TLabel", background=WINDOW_BG, foreground=TEXT)
    style.map("TLabel", foreground=[("disabled", DISABLED_TEXT)])

    # ボタン：白系の面＋細いグレー枠線。ホバーで淡い青の面＋青枠、押下で
    # 一段濃い青（Word・Office系ダイアログのボタン挙動に倣う）。
    # clamの立体感はlightcolor/darkcolorを面と同色にして消す。
    style.configure(
        _NORMAL_BUTTON_STYLE,
        background=CONTROL_BG, foreground=TEXT,
        bordercolor=BORDER, lightcolor=CONTROL_BG, darkcolor=CONTROL_BG,
        padding=(10, 4),
    )
    style.map(
        _NORMAL_BUTTON_STYLE,
        background=[("disabled", DISABLED_BG), ("pressed", PRESSED_BG),
                    ("active", HOVER_BG)],
        foreground=[("disabled", DISABLED_TEXT)],
        bordercolor=[("pressed", ACCENT), ("active", ACCENT)],
        lightcolor=[("pressed", PRESSED_BG), ("active", HOVER_BG)],
        darkcolor=[("pressed", PRESSED_BG), ("active", HOVER_BG)],
    )

    # 主要な操作ボタン（決定・保存）：地色は他のボタンと同じ
    # ニュートラルな色のまま、枠線だけを常時アクセント色にして強調する
    # （常時アクセント色で塗りつぶす方式にはしない）。
    style.configure(
        _ACCENT_BUTTON_STYLE,
        background=CONTROL_BG, foreground=TEXT,
        bordercolor=ACCENT, lightcolor=CONTROL_BG, darkcolor=CONTROL_BG,
        padding=(10, 4),
    )
    style.map(
        _ACCENT_BUTTON_STYLE,
        background=[("disabled", DISABLED_BG), ("pressed", PRESSED_BG),
                    ("active", HOVER_BG)],
        foreground=[("disabled", DISABLED_TEXT)],
        bordercolor=[("disabled", BORDER)],
        lightcolor=[("pressed", PRESSED_BG), ("active", HOVER_BG)],
        darkcolor=[("pressed", PRESSED_BG), ("active", HOVER_BG)],
    )

    # リボンのボタン（VA mints・VA Case・VA Documentと同じ）：Wordのリボンと同じく、
    # ふだんは枠も地もなく、マウスを重ねたときだけ淡い青の地と枠を出す（「各コマンド
    # ボタンには常時、細い枠線を表示する」という方針の、リボンだけの例外）。
    # 大きいボタン（RibbonLarge）はアイコンの下に文字、小さいボタン（Ribbon）は
    # アイコンの右に文字を置く。
    _ribbon_states = dict(
        background=[("disabled", WINDOW_BG), ("pressed", PRESSED_BG), ("active", HOVER_BG)],
        lightcolor=[("disabled", WINDOW_BG), ("pressed", PRESSED_BG), ("active", HOVER_BG)],
        darkcolor=[("disabled", WINDOW_BG), ("pressed", PRESSED_BG), ("active", HOVER_BG)],
        bordercolor=[("disabled", WINDOW_BG), ("pressed", RIBBON_HOVER_BORDER),
                     ("active", RIBBON_HOVER_BORDER)],
        foreground=[("disabled", DISABLED_TEXT)],
    )
    for _name, _padding, _anchor in (
        (RIBBON_BUTTON_STYLE, (3, 1), "w"),
        (RIBBON_LARGE_BUTTON_STYLE, (4, 2), "center"),
    ):
        style.configure(
            _name,
            background=WINDOW_BG, foreground=TEXT, bordercolor=WINDOW_BG,
            lightcolor=WINDOW_BG, darkcolor=WINDOW_BG,
            padding=_padding, width=0, anchor=_anchor, justify="center", relief="raised",
        )
        style.map(_name, **_ribbon_states)
    # 押すとメニューが開くボタン（リボンの［回転］。Wordの［回転］と同じ）：小さいボタンと
    # 同じ見た目に、右に小さな下向きの三角を付ける。
    style.configure(
        RIBBON_MENUBUTTON_STYLE,
        background=WINDOW_BG, foreground=TEXT, bordercolor=WINDOW_BG,
        lightcolor=WINDOW_BG, darkcolor=WINDOW_BG,
        padding=(3, 1), width=0, anchor="w", relief="raised",
        arrowcolor=TEXT, arrowsize=6,
    )
    style.map(RIBBON_MENUBUTTON_STYLE, arrowcolor=[("disabled", DISABLED_TEXT)],
              **_ribbon_states)

    # リボンの主要な操作（開く・保存の大きいボタン）：見落とされないよう、
    # ほかのリボンのボタンと違って、いつも地色と青い枠を出す（主要な操作ボタンは
    # 枠線の色をアクセント色にする、という方針に合わせる）。
    style.configure(
        RIBBON_ACCENT_BUTTON_STYLE,
        background=CONTROL_BG, foreground=TEXT, bordercolor=ACCENT,
        lightcolor=CONTROL_BG, darkcolor=CONTROL_BG,
        padding=(4, 2), width=0, anchor="center", justify="center", relief="raised",
    )
    style.map(
        RIBBON_ACCENT_BUTTON_STYLE,
        background=[("disabled", DISABLED_BG), ("pressed", PRESSED_BG),
                    ("active", HOVER_BG)],
        foreground=[("disabled", DISABLED_TEXT)],
        bordercolor=[("disabled", BORDER), ("!disabled", ACCENT)],
        lightcolor=[("pressed", PRESSED_BG), ("active", HOVER_BG)],
        darkcolor=[("pressed", PRESSED_BG), ("active", HOVER_BG)],
    )

    style.configure(RIBBON_FRAME_STYLE, background=WINDOW_BG)
    style.configure(RIBBON_LABEL_STYLE, background=WINDOW_BG, foreground=TEXT)
    # 組の名前（リボンの下端に小さく出す）と、組の中の注記
    style.configure(RIBBON_GROUP_LABEL_STYLE, background=WINDOW_BG, foreground=NOTE,
                    padding=(0, 0, 0, 2))
    style.configure(RIBBON_NOTE_LABEL_STYLE, background=WINDOW_BG, foreground=NOTE)

    # ラジオボタン：判定枠（インジケータ）は入力欄と同じ白地、枠線は共通の枠線色。
    # チェックボックスはclassic tk.Checkbutton（後述のoption_add）のまま残す
    # （'clam'テーマのCheckbutton.indicatorはチェック時に「✕」形状で描画され、
    # 「利用不可」のように誤解されるため）。
    style.configure(
        "TRadiobutton",
        background=WINDOW_BG, foreground=TEXT,
        indicatorbackground=FIELD_BG, indicatorforeground=TEXT,
        upperbordercolor=BORDER, lowerbordercolor=BORDER,
    )
    style.map(
        "TRadiobutton",
        foreground=[("disabled", DISABLED_TEXT)],
        indicatorbackground=[("disabled", DISABLED_BG)],
    )

    # LabelFrame：立体的な溝状の縁取りにせず、薄い一色の枠線のみで囲む。
    style.configure(
        "TLabelframe",
        background=WINDOW_BG, bordercolor=BORDER,
        lightcolor=WINDOW_BG, darkcolor=WINDOW_BG,
    )
    style.configure("TLabelframe.Label", background=WINDOW_BG, foreground=TEXT)

    style.configure(
        "TEntry",
        fieldbackground=FIELD_BG, foreground=TEXT,
        bordercolor=BORDER, lightcolor=FIELD_BG, darkcolor=FIELD_BG,
        insertcolor=TEXT, padding=4,
    )
    style.map(
        "TEntry",
        fieldbackground=[("readonly", FIELD_BG), ("disabled", DISABLED_BG)],
        foreground=[("disabled", DISABLED_TEXT)],
        bordercolor=[("focus", ACCENT)],
    )

    style.configure(
        "TCombobox",
        fieldbackground=FIELD_BG, background=FIELD_BG, foreground=TEXT,
        bordercolor=BORDER, lightcolor=FIELD_BG, darkcolor=FIELD_BG,
        arrowcolor=NOTE, relief="flat", padding=3,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", FIELD_BG), ("disabled", DISABLED_BG)],
        foreground=[("disabled", DISABLED_TEXT)],
        bordercolor=[("focus", ACCENT)],
    )

    style.configure(
        "TProgressbar",
        background=ACCENT, troughcolor=DISABLED_BG,
        bordercolor=DISABLED_BG, lightcolor=ACCENT, darkcolor=ACCENT,
        thickness=12,
    )

    style.configure(
        "TScrollbar",
        troughcolor=SCROLL_TROUGH, background=SCROLL_THUMB,
        bordercolor=SCROLL_TROUGH, lightcolor=SCROLL_THUMB, darkcolor=SCROLL_THUMB,
        arrowcolor=NOTE,
    )
    style.map(
        "TScrollbar",
        background=[("pressed", SCROLL_THUMB_ACTIVE), ("active", SCROLL_THUMB_ACTIVE)],
        lightcolor=[("pressed", SCROLL_THUMB_ACTIVE), ("active", SCROLL_THUMB_ACTIVE)],
        darkcolor=[("pressed", SCROLL_THUMB_ACTIVE), ("active", SCROLL_THUMB_ACTIVE)],
    )

    # プレビューのサムネイル枠（F-30）：選択中はアクセント色の枠、非選択は背景色に
    # 同化させて見えなくする（classic tkの頃はhighlightbackgroundの切替で表現していた）。
    # 'clam'ではreliefがflatのままだとbordercolorの枠線が描画されないため、
    # raisedを明示する（lightcolor/darkcolorを面と同色にしているため立体感は出ない。
    # TButton・TLabelframeで枠線が表示されているのも同じくreliefがraisedのため）。
    style.configure(
        THUMBNAIL_FRAME_STYLE,
        background=FIELD_BG, bordercolor=WINDOW_BG,
        lightcolor=FIELD_BG, darkcolor=FIELD_BG, borderwidth=2, relief="raised",
    )
    style.configure(
        THUMBNAIL_FRAME_SELECTED_STYLE,
        background=FIELD_BG, bordercolor=ACCENT,
        lightcolor=FIELD_BG, darkcolor=FIELD_BG, borderwidth=2, relief="raised",
    )
