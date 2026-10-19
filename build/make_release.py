# -*- coding: utf-8 -*-
"""VA FAXの配布用zipを組み立てる（ビルド用資材）。

前提: dist/VA_FAX.exe がビルド済みであること（build/motfax.spec参照）。
実行方法（リポジトリルートで）: python build/make_release.py
出力: dist/VA_FAX_v111.zip

zipの構成:
    VA_FAX_v111/
        VA_FAX.exe                          … 本体（これだけで動作する）
        README.md                           … 利用者向け説明（VBAアセット掲載文と同一）
        VA_FAX_操作マニュアル_Ver111.pdf     … 操作マニュアル
        LICENSE.txt                         … MITライセンス（オープンソース）
        source/                             … ソースコード一式（gitで管理している全ファイル。
                                              ただしCLAUDE.md等の開発プロセス用ファイルは除く）

ソースはgitの管理対象ファイル（git ls-files）から取るため、ビルド生成物や
キャッシュが紛れ込まない。オープンソースとしての透明性（守秘設計の実装を
第三者が検証できること）のため、テスト・サンプル生成スクリプト・ビルド定義を
含む一式を同梱する。
"""
from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

VERSION = "v111"
ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
EXE = DIST / "VA_FAX.exe"
OUTPUT = DIST / f"VA_FAX_{VERSION}.zip"
TOP = f"VA_FAX_{VERSION}"

# 開発プロセス用のファイル（ツールの利用・改変に不要）はソース同梱から除く
SOURCE_EXCLUDES = {"CLAUDE.md"}


def tracked_files() -> list[str]:
    # -z（NUL区切り）で取得する。既定の出力では日本語等の非ASCIIファイル名が
    # クォート付きの8進エスケープ（例: "docs/VA_FAX_\346..."）になり、
    # そのままではパスとして使えないため。
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", check=True)
    return [p for p in out.stdout.split("\0") if p and p not in SOURCE_EXCLUDES]


def main() -> None:
    if not EXE.is_file():
        raise SystemExit("dist/VA_FAX.exe がありません。先にPyInstallerでビルドしてください。")

    DIST.mkdir(exist_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as zf:
        manual_name = "VA_FAX_操作マニュアル_Ver111.pdf"
        zf.write(EXE, f"{TOP}/VA_FAX.exe")
        zf.write(ROOT / "README.md", f"{TOP}/README.md")
        zf.write(ROOT / "docs" / manual_name, f"{TOP}/{manual_name}")
        zf.write(ROOT / "LICENSE", f"{TOP}/LICENSE.txt")
        for rel in tracked_files():
            zf.write(ROOT / rel, f"{TOP}/source/{rel}")

    size_mb = OUTPUT.stat().st_size / 1024 / 1024
    print(f"generated: {OUTPUT}（{size_mb:.1f} MB）")


if __name__ == "__main__":
    main()
