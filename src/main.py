# -*- coding: utf-8 -*-
"""VA FAX エントリポイント。"""
from __future__ import annotations

import gc
import os
import sys
import tkinter as tk
from pathlib import Path
from typing import Optional

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from config.profiles import ProfileError, load_all_profiles  # noqa: E402
from config.settings import Settings, load_settings, save_settings  # noqa: E402
from gui import theme  # noqa: E402
from gui.app import ProfileChoiceDialog, VaFaxApp  # noqa: E402


def _run_smoke_test(spec: str) -> int:
    """ビルド成果物の自己診断（CLAUDE.md第4章のexe検証用。利用者向け機能ではない）。

    環境変数 VA_FAX_SMOKE_TEST="入力PDFパス;出力PDFパス" が設定されている場合のみ
    実行される。ウィンドウは表示せず（withdraw）、同梱プロファイルの読込・
    文書・変換・保存のエンジン一式（pypdfium2・Pillow・pypdf）・tkinter／tkinterdnd2の
    ネイティブ部品・リボンのアイコン（F-15）がexeに正しく同梱されていることを
    終了コードで報告する
    （windowed exeでは標準出力が使えないため、成否は終了コードと出力ファイルで確認する）。
    """
    input_str, output_str = spec.split(";", 1)

    import io

    from PIL import Image

    from core import convert as convert_module
    from core import document as document_module
    from core import saving as saving_module

    profiles = load_all_profiles()
    profile = profiles["motfax"]
    paper = (profile.normalize_target.width_mm, profile.normalize_target.height_mm)
    # 文書（ページの表）・画像の取り込み・ページの編集・保存（F-01・F-02・F-33・F-34）を通す
    document = document_module.Document()
    document.add_source(document_module.load_source(Path(input_str).read_bytes(), paper))
    image = io.BytesIO()
    Image.new("RGB", (600, 400), "white").save(image, format="PNG")
    document.add_source(document_module.load_source(image.getvalue(), paper), at=0)
    document.rotate([0], 90)
    # 送付書（F-36。Ver210）：reportlab と、明朝体の埋め込みが exe で動くことを確かめる
    from core import cover_sheet as cover_module

    cover = cover_module.CoverInput(court="架空地方裁判所", other="架空", sender_title="原告訴訟代理人弁護士",
                                    documents=[cover_module.DocItem("甲第1号証", "1")])
    data, _pages, _total = cover_module.build_cover(
        cover, cover_module.Sender(office="架空法律事務所", lawyer="架空"), document.page_count, paper)
    if os.name == "nt" and cover_module._registered_font != cover_module._FONT_NAME:
        raise SystemExit("送付書の明朝体を埋め込めません")
    document.put_front(document_module.load_source(data, paper))
    from core import diagnose as diagnose_module

    diagnose_module.diagnose(document.assemble(), profile)
    saving_module.save_document(
        document.assemble(), Path(output_str), profile,
        convert_module.ConvertOptions(mode="binarize", page_numbers=True), "reduce_resolution",
        "culprit_only", [])
    # ページ番号（F-37。Ver210）：「何もしない」のときに PDF のまま重ねる番号（reportlab・Arial の埋め込み）
    from core import as_is

    numbered = as_is.stamp_numbers(document.assemble())
    if os.name == "nt" and as_is._font() != as_is._FONT_NAME:
        raise SystemExit("ページ番号の字を埋め込めません")
    if b"/FontFile2" not in numbered and os.name == "nt":
        raise SystemExit("ページ番号の字が埋め込まれていません")

    from tkinterdnd2 import TkinterDnD

    root = TkinterDnD.Tk()
    root.withdraw()
    theme.apply_flat_theme(root)

    # リボンのアイコン（同梱PNG）を実際に読み、exeに入っていることを確かめる（F-15）。
    from gui.icons import BUTTON_ICONS, IconSet
    from gui.ribbon import Ribbon

    icons = IconSet(root)
    ribbon = Ribbon(root, icons)
    group = ribbon.group("ファイル")
    group.large("開く", lambda: None)
    for name in sorted(set(BUTTON_ICONS.values())):
        for large in (False, True):
            if icons.image(name, large=large) is None or icons.image(name, disabled=True) is None:
                raise SystemExit(f"アイコンが同梱されていません：{name}")

    root.update()
    root.destroy()
    return 0


def _select_initial_profile(settings: Settings) -> Optional[str]:
    """初回起動時のプロファイル選択ダイアログを表示する（F-28）。"""
    root = tk.Tk()
    # root.withdraw()はTcl/Tk環境によってはtransient()の相手先ウィンドウを
    # withdrawn状態のまま固着させ、ダイアログが表示されなくなることがあるため、
    # withdrawはせず、目立たない位置に小さく表示するに留める。
    root.geometry("1x1+0+0")
    # VaFaxApp自体のインタプリタとは別（tk.Tk()を独立して生成するため）、
    # ここでも同じフラットデザイン・アイコンを適用する必要がある。
    theme.apply_flat_theme(root)
    theme.apply_app_icon(root)
    dialog = ProfileChoiceDialog(root, current_profile_id=settings.profile_id)
    root.wait_window(dialog)
    profile_id = dialog.result_profile_id
    root.destroy()
    # 閉じたウィンドウの部品（Tkの変数等）を、ここ（画面のスレッド）で片づけておく。残したままに
    # すると、この後のメイン画面の裏のスレッド（事前診断・保存）でガベージコレクションにかかり、
    # Tclが「別のスレッドからの解放」として異常終了することがある。
    del dialog, root
    gc.collect()
    return profile_id


def main() -> None:
    smoke_spec = os.environ.get("VA_FAX_SMOKE_TEST")
    if smoke_spec:
        sys.exit(_run_smoke_test(smoke_spec))

    settings = load_settings()
    profiles = load_all_profiles()

    if not settings.profile_id or settings.profile_id not in profiles or not settings.first_run_completed:
        profile_id = _select_initial_profile(settings)
        if not profile_id:
            return
        settings.profile_id = profile_id
        settings.first_run_completed = True
        save_settings(settings)
        profiles = load_all_profiles()

    try:
        profile = profiles[settings.profile_id]
    except (KeyError, ProfileError):
        return

    app = VaFaxApp(settings, profile)
    app.mainloop()


if __name__ == "__main__":
    main()
