# -*- coding: utf-8 -*-
"""ツールの名前と版（標準 02「版」「メッセージとエラー」）。

版はここの1か所に持つ。spec・scripts/build_release.py・scripts/generate_manual.py は、ここから読むか、
引数で受ける（版を上げるときは、ここと、マニュアルの原稿のフロントマターの version・更新履歴を直す）。
画面に出す版は VERSION_LABEL（Ver211。点を入れない。標準 01「版」）を使う。9 の次は A（"2.A.0"）。

VA FAX は、ほかのツールと違い、src の下をパッケージにしていない（src/config・src/core・src/gui）ので、
雛形の __init__.py に当たるものを、このファイルに置く（2026-11-19 班長決定。CLAUDE.md の「標準と違う所」）。
"""

__version__ = "2.1.2"
VERSION_LABEL = "Ver" + __version__.replace(".", "")

APP_NAME = "VA FAX"                          # ツールの名前（題名・メッセージ・「について」はここから作る）
FILE_STEM = "VA_FAX"                         # exe・zip・マニュアルの名前に使う形（空白を使わない。N-07）
# 「について」の1行の説明（統合版・記事の説明と同じにする。標準 01「について」）
ABOUT_LINE = "FAX送信用PDFの準備（ページの編集・送付書・変換・容量調整）"
APP_USER_MODEL_ID = "VBAAssets.VAFAX.Source"   # ソースから起動したときの、タスクバーのアイコンの組
