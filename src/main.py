# -*- coding: utf-8 -*-
"""VA FAX エントリポイント。"""
from __future__ import annotations

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
    変換エンジン一式（pypdfium2・Pillow・img2pdf）・tkinter／tkinterdnd2の
    ネイティブ部品がexeに正しく同梱されていることを終了コードで報告する
    （windowed exeでは標準出力が使えないため、成否は終了コードと出力ファイルで確認する）。
    """
    input_str, output_str = spec.split(";", 1)

    from core import convert as convert_module

    profiles = load_all_profiles()
    profile = profiles["motfax"]
    data = Path(input_str).read_bytes()
    result = convert_module.convert(data, profile, convert_module.ConvertOptions(mode="binarize"))
    Path(output_str).write_bytes(result.build_pdf())

    from tkinterdnd2 import TkinterDnD

    root = TkinterDnD.Tk()
    root.withdraw()
    theme.apply_flat_theme(root)
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
