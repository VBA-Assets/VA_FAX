# -*- coding: utf-8 -*-
"""操作マニュアルのPDFを生成する（ビルド用資材）。

docs/manual.md（本文とフロントマターのみ）から、VA_MINTSのマニュアルと同じ
体裁のPDFを生成する：
- 表紙（中央にタイトル・版数・作成者/配布元・日付）
- 目次（「第N章」見出しとその節を自動抽出。章は太字・節は字下げ）
- 全ページ下部中央の頁番号

表紙・目次はフロントマターと本文見出しから自動生成するため、manual.md自体には
書かない（VA_MINTSのマニュアル原稿と同じ流儀。本文は「第1章」から始める）。

実行方法（リポジトリルートで）: python build/make_manual.py
前提: md-to-pdf（npm）がインストール済みであること
出力: docs/VA_FAX_操作マニュアル_VerNNN.pdf（フロントマターのdest指定に従う）
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "manual.md"
WORK_DIR = ROOT / "build" / "work"
COMBINED = WORK_DIR / "manual_build.md"

# 頁番号（全ページ下部中央）。md-to-pdf（Puppeteer）のフッターテンプレート。
PDF_OPTIONS = """\
pdf_options:
  format: A4
  margin: 18mm 16mm 22mm 16mm
  printBackground: true
  displayHeaderFooter: true
  headerTemplate: '<div></div>'
  footerTemplate: '<div style="width:100%; text-align:center; font-size:9px; color:#888;"><span class="pageNumber"></span></div>'
"""


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not match:
        raise SystemExit("manual.mdにフロントマターがありません")
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, match.group(2)


def build_cover(meta: dict[str, str]) -> str:
    return f"""\
<div style="page-break-after: always; text-align: center; padding-top: 86mm;">
  <div style="font-size: 30px; font-weight: bold;">{meta["title"]}</div>
  <div style="margin-top: 34px; font-size: 15px; color: #666;">{meta["version"]}</div>
  <div style="margin-top: 34px; font-size: 12px; color: #666;">作成者：{meta["author"]} 配布元：{meta["distributor"]}</div>
  <div style="margin-top: 16px; font-size: 12px; color: #666;">{meta["date"]}</div>
</div>
"""


def build_toc(body: str) -> str:
    """本文の「第N章」見出し（#）とその節（##）から目次を組み立てる。

    「更新履歴」等、章番号を持たない見出しは目次に載せない（VA_MINTSの体裁に合わせる）。
    """
    lines = ["# 目次", ""]
    for raw in body.splitlines():
        if raw.startswith("# 第"):
            lines.append(f'<p style="font-weight: bold; margin: 7px 0 3px;">{raw[2:].strip()}</p>')
        elif raw.startswith("## ") and re.match(r"## \d+\.\d+", raw):
            lines.append(f'<p style="margin: 3px 0 3px 2em;">{raw[3:].strip()}</p>')
    lines.append('\n<div style="page-break-after: always;"></div>\n')
    return "\n".join(lines)


def main() -> None:
    meta, body = split_frontmatter(SOURCE.read_text(encoding="utf-8"))

    # 出力先はmanual.mdのdest指定を引き継ぐ。md-to-pdfのdestはカレントディレクトリ
    # 基準で解決されるため、build/workをカレントにして相対パスを付け替える。
    dest = meta.get("dest", "./manual.pdf").lstrip("./")
    frontmatter = f"---\ndest: ../../docs/{dest}\n{PDF_OPTIONS}---\n"

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    COMBINED.write_text(frontmatter + build_cover(meta) + "\n" + build_toc(body) + "\n" + body,
                        encoding="utf-8")

    subprocess.run(["md-to-pdf", COMBINED.name], check=True, shell=True, cwd=WORK_DIR)
    print(f"generated: {ROOT / 'docs' / dest}")


if __name__ == "__main__":
    main()
