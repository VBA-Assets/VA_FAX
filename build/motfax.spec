# -*- mode: python ; coding: utf-8 -*-
# VA FAX のPyInstaller定義（onefile方式。CLAUDE.md第4章「後方支援」）。
#
# ビルド方法（リポジトリルートで実行）:
#   python -m PyInstaller build/motfax.spec --distpath dist --workpath build/work -y
#
# 同梱が必要なもの:
# - resources/profiles/*.json … 同梱サービスプロファイル（F-27。config/profiles.pyが
#   __file__基準の相対パス resources/profiles で参照するため、同じ相対位置に配置する）
# - tkinterdnd2 … ドラッグ＆ドロップ用のtkdnd Tclパッケージ（ネイティブDLL含む。F-15）
# - pypdfium2_raw の pdfium.dll … PDFラスタライズエンジン（F-07）
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

datas = [("../src/resources/profiles", "resources/profiles")]
# アプリケーションアイコン（タイトルバー用。build/make_icon.pyで生成）
datas += [("../src/resources/icon.ico", "resources")]
# 「一覧から取り除く」操作中のマウスカーソル（F-15。build/make_cursor.pyで生成）
datas += [("../src/resources/trash.cur", "resources")]
datas += collect_data_files("tkinterdnd2")

binaries = collect_dynamic_libs("pypdfium2_raw")
binaries += collect_dynamic_libs("tkinterdnd2")

a = Analysis(
    ["../src/main.py"],
    pathex=["../src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=["tkinterdnd2"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    # 実行ファイル名は空白を用いず VA_FAX とする（N-07）
    name="VA_FAX",
    # エクスプローラー・タスクバーに表示されるexe自体のアイコン
    icon="../src/resources/icon.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # GUIアプリのためコンソールウィンドウは表示しない
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
