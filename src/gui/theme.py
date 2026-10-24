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
import tkinter as tk
from pathlib import Path
from tkinter import ttk

# ---- パレット（Office風） -------------------------------------------------------
COLOR_BG = "#f0f0f0"             # ウィンドウ・フレームの背景
COLOR_SURFACE = "#ffffff"        # 入力欄・テキスト欄・サムネイル等の背景
COLOR_CONTROL_BG = "#fdfdfd"     # ボタンの通常時の地色（白寄り）
COLOR_HOVER_BG = "#e0eef9"       # ボタンのホバー時の地色（淡い青）
COLOR_PRESSED_BG = "#cce4f7"     # ボタンの押下時の地色（濃い青）
COLOR_TEXT = "#1a1a1a"           # 通常テキスト
COLOR_MUTED = "#666666"          # 補足・注記テキスト
COLOR_PLACEHOLDER = "#999999"    # プレースホルダー文字色
COLOR_BORDER = "#c8c8c8"         # 枠線・区切り線
COLOR_WARNING = "#a05a00"        # 注意喚起テキスト（参考プロファイル注記等。既存踏襲）

COLOR_ACCENT = "#0078d4"         # アクセント（フォーカス枠線・主要ボタンの強調枠）

COLOR_DISABLED_BG = "#f0f0f0"    # 無効化ウィジェットの背景（ウィンドウ地色に同化）
COLOR_DISABLED_TEXT = "#a3a3a3"  # 無効化ウィジェットの文字色

COLOR_SCROLL_TROUGH = "#f0f0f0"
COLOR_SCROLL_THUMB = "#c1c1c1"
COLOR_SCROLL_THUMB_ACTIVE = "#a8a8a8"

_ACCENT_BUTTON_STYLE = "Accent.TButton"
_NORMAL_BUTTON_STYLE = "TButton"

# 同梱カーソル（trash.cur）を読み込めない環境で使う代替カーソル
REMOVE_CURSOR_FALLBACK = "X_cursor"

THUMBNAIL_FRAME_STYLE = "Thumbnail.TFrame"
THUMBNAIL_FRAME_SELECTED_STYLE = "ThumbnailSelected.TFrame"


def apply_app_icon(window: tk.Tk) -> None:
    """アプリケーションアイコン（VA／FAXの2行テキスト）をウィンドウに適用する。

    default指定により、このウィンドウから開かれる各Toplevelダイアログにも
    自動的に同じアイコンが適用される。アイコンはbuild/make_icon.pyで生成し、
    resources/icon.icoとしてexeにも同梱される（build/motfax.spec）。
    見つからない場合（開発中の未生成等）は既定アイコンのまま起動を続ける。

    Windows以外（Macでソースから動かす場合。N-03）のTkは.icoをiconbitmapで
    受け付けず、TclErrorで起動できなくなるため、Pillowで読み込んだ画像を
    iconphotoで与える（Macではアプリのアイコン〔Dock〕になる）。読み込めない場合は
    既定アイコンのまま起動を続ける。
    """
    icon_path = Path(__file__).resolve().parent.parent / "resources" / "icon.ico"
    if not icon_path.is_file():
        return
    if windowing_system(window) == "win32":
        window.iconbitmap(default=str(icon_path))
        return
    try:
        from PIL import Image, ImageTk

        with Image.open(icon_path) as image:
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

    accent=Trueの場合（変換して出力・決定・保存等の主要な操作ボタン）は、
    枠線をアクセント色にしたスタイルを適用し、他のボタンと区別する
    （地色を塗りつぶす方式にはしない）。地色・ホバー・押下時の色変化は
    apply_flat_theme()で設定したttk.Styleにより自動的に反映される。
    """
    style_name = _ACCENT_BUTTON_STYLE if accent else _NORMAL_BUTTON_STYLE
    kwargs.setdefault("cursor", "hand2")
    return ttk.Button(parent, style=style_name, **kwargs)


def apply_flat_theme(root: tk.Misc) -> None:
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
    root.option_add("*Background", COLOR_BG)
    root.option_add("*Foreground", COLOR_TEXT)

    root.option_add("*Text.background", COLOR_SURFACE)
    root.option_add("*Text.foreground", COLOR_TEXT)
    root.option_add("*Text.relief", "flat")
    root.option_add("*Text.highlightThickness", 1)
    root.option_add("*Text.highlightBackground", COLOR_BORDER)
    root.option_add("*Text.highlightColor", COLOR_ACCENT)

    root.option_add("*Menu.background", COLOR_SURFACE)
    root.option_add("*Menu.foreground", COLOR_TEXT)
    root.option_add("*Menu.activeBackground", COLOR_HOVER_BG)
    root.option_add("*Menu.activeForeground", COLOR_TEXT)
    root.option_add("*Menu.relief", "flat")

    # チェックボックスはclassic tk.Checkbuttonのまま残す（'clam'テーマのttk版は
    # チェック時の見た目が「✕」形状になり、利用不可のように誤解されるため）。
    root.option_add("*Checkbutton.selectColor", COLOR_SURFACE)
    root.option_add("*Checkbutton.activeBackground", COLOR_BG)
    root.option_add("*Checkbutton.activeForeground", COLOR_TEXT)
    root.option_add("*Checkbutton.disabledForeground", COLOR_DISABLED_TEXT)

    style = ttk.Style(root)
    # 'clam'はOSネイティブ装飾（立体的な縁取り・グラデーション等）を持たない、
    # 最もフラットに設定しやすい組込みテーマ。
    style.theme_use("clam")

    # 既定の背景・文字色（"."は全ttkスタイルの基底であり、個別指定がない限り継承される）。
    style.configure(".", background=COLOR_BG, foreground=COLOR_TEXT)
    style.configure("TFrame", background=COLOR_BG)
    style.configure("TLabel", background=COLOR_BG, foreground=COLOR_TEXT)
    style.map("TLabel", foreground=[("disabled", COLOR_DISABLED_TEXT)])

    # ボタン：白系の面＋細いグレー枠線。ホバーで淡い青の面＋青枠、押下で
    # 一段濃い青（Word・Office系ダイアログのボタン挙動に倣う）。
    # clamの立体感はlightcolor/darkcolorを面と同色にして消す。
    style.configure(
        _NORMAL_BUTTON_STYLE,
        background=COLOR_CONTROL_BG, foreground=COLOR_TEXT,
        bordercolor=COLOR_BORDER, lightcolor=COLOR_CONTROL_BG, darkcolor=COLOR_CONTROL_BG,
        padding=(10, 4),
    )
    style.map(
        _NORMAL_BUTTON_STYLE,
        background=[("disabled", COLOR_DISABLED_BG), ("pressed", COLOR_PRESSED_BG),
                    ("active", COLOR_HOVER_BG)],
        foreground=[("disabled", COLOR_DISABLED_TEXT)],
        bordercolor=[("pressed", COLOR_ACCENT), ("active", COLOR_ACCENT)],
        lightcolor=[("pressed", COLOR_PRESSED_BG), ("active", COLOR_HOVER_BG)],
        darkcolor=[("pressed", COLOR_PRESSED_BG), ("active", COLOR_HOVER_BG)],
    )

    # 主要な操作ボタン（変換して出力・決定・保存）：地色は他のボタンと同じ
    # ニュートラルな色のまま、枠線だけを常時アクセント色にして強調する
    # （常時アクセント色で塗りつぶす方式にはしない）。
    style.configure(
        _ACCENT_BUTTON_STYLE,
        background=COLOR_CONTROL_BG, foreground=COLOR_TEXT,
        bordercolor=COLOR_ACCENT, lightcolor=COLOR_CONTROL_BG, darkcolor=COLOR_CONTROL_BG,
        padding=(10, 4),
    )
    style.map(
        _ACCENT_BUTTON_STYLE,
        background=[("disabled", COLOR_DISABLED_BG), ("pressed", COLOR_PRESSED_BG),
                    ("active", COLOR_HOVER_BG)],
        foreground=[("disabled", COLOR_DISABLED_TEXT)],
        bordercolor=[("disabled", COLOR_BORDER)],
        lightcolor=[("pressed", COLOR_PRESSED_BG), ("active", COLOR_HOVER_BG)],
        darkcolor=[("pressed", COLOR_PRESSED_BG), ("active", COLOR_HOVER_BG)],
    )

    # ラジオボタン：判定枠（インジケータ）は入力欄と同じ白地、枠線は共通の枠線色。
    # チェックボックスはclassic tk.Checkbutton（後述のoption_add）のまま残す
    # （'clam'テーマのCheckbutton.indicatorはチェック時に「✕」形状で描画され、
    # 「利用不可」のように誤解されるため）。
    style.configure(
        "TRadiobutton",
        background=COLOR_BG, foreground=COLOR_TEXT,
        indicatorbackground=COLOR_SURFACE, indicatorforeground=COLOR_TEXT,
        upperbordercolor=COLOR_BORDER, lowerbordercolor=COLOR_BORDER,
    )
    style.map(
        "TRadiobutton",
        foreground=[("disabled", COLOR_DISABLED_TEXT)],
        indicatorbackground=[("disabled", COLOR_DISABLED_BG)],
    )

    # LabelFrame：立体的な溝状の縁取りにせず、薄い一色の枠線のみで囲む。
    style.configure(
        "TLabelframe",
        background=COLOR_BG, bordercolor=COLOR_BORDER,
        lightcolor=COLOR_BG, darkcolor=COLOR_BG,
    )
    style.configure("TLabelframe.Label", background=COLOR_BG, foreground=COLOR_TEXT)

    style.configure(
        "TEntry",
        fieldbackground=COLOR_SURFACE, foreground=COLOR_TEXT,
        bordercolor=COLOR_BORDER, lightcolor=COLOR_SURFACE, darkcolor=COLOR_SURFACE,
        insertcolor=COLOR_TEXT, padding=4,
    )
    style.map(
        "TEntry",
        fieldbackground=[("readonly", COLOR_SURFACE), ("disabled", COLOR_DISABLED_BG)],
        foreground=[("disabled", COLOR_DISABLED_TEXT)],
        bordercolor=[("focus", COLOR_ACCENT)],
    )

    style.configure(
        "TCombobox",
        fieldbackground=COLOR_SURFACE, background=COLOR_SURFACE, foreground=COLOR_TEXT,
        bordercolor=COLOR_BORDER, lightcolor=COLOR_SURFACE, darkcolor=COLOR_SURFACE,
        arrowcolor=COLOR_MUTED, relief="flat", padding=3,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", COLOR_SURFACE), ("disabled", COLOR_DISABLED_BG)],
        foreground=[("disabled", COLOR_DISABLED_TEXT)],
        bordercolor=[("focus", COLOR_ACCENT)],
    )

    style.configure(
        "TProgressbar",
        background=COLOR_ACCENT, troughcolor=COLOR_DISABLED_BG,
        bordercolor=COLOR_DISABLED_BG, lightcolor=COLOR_ACCENT, darkcolor=COLOR_ACCENT,
        thickness=12,
    )

    style.configure(
        "TScrollbar",
        troughcolor=COLOR_SCROLL_TROUGH, background=COLOR_SCROLL_THUMB,
        bordercolor=COLOR_SCROLL_TROUGH, lightcolor=COLOR_SCROLL_THUMB, darkcolor=COLOR_SCROLL_THUMB,
        arrowcolor=COLOR_MUTED,
    )
    style.map(
        "TScrollbar",
        background=[("pressed", COLOR_SCROLL_THUMB_ACTIVE), ("active", COLOR_SCROLL_THUMB_ACTIVE)],
        lightcolor=[("pressed", COLOR_SCROLL_THUMB_ACTIVE), ("active", COLOR_SCROLL_THUMB_ACTIVE)],
        darkcolor=[("pressed", COLOR_SCROLL_THUMB_ACTIVE), ("active", COLOR_SCROLL_THUMB_ACTIVE)],
    )

    # プレビューのサムネイル枠（F-30）：選択中はアクセント色の枠、非選択は背景色に
    # 同化させて見えなくする（classic tkの頃はhighlightbackgroundの切替で表現していた）。
    # 'clam'ではreliefがflatのままだとbordercolorの枠線が描画されないため、
    # raisedを明示する（lightcolor/darkcolorを面と同色にしているため立体感は出ない。
    # TButton・TLabelframeで枠線が表示されているのも同じくreliefがraisedのため）。
    style.configure(
        THUMBNAIL_FRAME_STYLE,
        background=COLOR_SURFACE, bordercolor=COLOR_BG,
        lightcolor=COLOR_SURFACE, darkcolor=COLOR_SURFACE, borderwidth=2, relief="raised",
    )
    style.configure(
        THUMBNAIL_FRAME_SELECTED_STYLE,
        background=COLOR_SURFACE, bordercolor=COLOR_ACCENT,
        lightcolor=COLOR_SURFACE, darkcolor=COLOR_SURFACE, borderwidth=2, relief="raised",
    )
