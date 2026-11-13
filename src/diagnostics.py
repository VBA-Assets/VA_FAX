# -*- coding: utf-8 -*-
"""診断情報（［ヘルプ］→［診断情報をコピー］。2026-11-10 班長決定。VA mints・VA Caseと同じ形）。

不具合をAIや作成者に相談するときに渡す文を作る。中身（入力・出力ファイルの名前、ファイルや
フォルダの場所、利用者・PCの名前、例外のメッセージの本文）は一切入れない。入れてよいのは、
版・環境・設定の数値・件数・誤りの型と起きた場所（ファイル名・行・関数名）だけとする
（F-24のログの方針と同じ。例外のメッセージの本文を入れないのは、ファイルの読み書きの誤り
〔OSError等〕のメッセージに、文書の名前や場所が入るため）。
"""
from __future__ import annotations

import json
import os
import platform
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.settings import app_data_dir

KEEP_ERRORS = 30  # 覚えておく誤りの数（古いものから消す）
SHOW_ERRORS = 10  # 診断情報に載せる誤りの数（新しいものから）
MAX_FRAMES = 12   # 1つの誤りに載せる場所の数（深い方から）


def exception_record(exc: BaseException, stage: str = "") -> dict:
    """例外を、型の名前と起きた場所だけの記録にする（メッセージの本文は入れない）。"""
    frames = traceback.extract_tb(exc.__traceback__)[-MAX_FRAMES:]
    record = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stage": stage,
        "type": type(exc).__name__,
        "where": [f"{Path(f.filename).name}:{f.lineno}:{f.name}" for f in frames],
    }
    # OSErrorの番号（何の誤りかの手がかりになり、場所や名前は含まない）
    for name in ("errno", "winerror"):
        value = getattr(exc, name, None)
        if isinstance(value, int):
            record[name] = value
    cause = exc.__cause__ or exc.__context__
    if cause is not None:
        record["cause"] = type(cause).__name__
    return record


def errors_path() -> Path:
    """誤りの記録の置き場所（%APPDATA%\\VA_FAX\\errors.json）。

    exeの隣に書くとフォルダによっては書けないため、ログ（F-24）と同じ管理領域に置く。
    記録するのは型と場所だけで、文書に関する情報は含まない（N-06）。
    """
    return app_data_dir() / "errors.json"


class ErrorLog:
    """起きた誤りを覚える（型と場所だけ）。

    画面を閉じた後や落ちた後でも渡せるよう、ファイルにも書く。書けなくても画面の動きは
    止めない（診断のための記録のため）。
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else errors_path()
        try:
            records = json.loads(self.path.read_text(encoding="utf-8"))
            self.records = [r for r in records if isinstance(r, dict)][-KEEP_ERRORS:]
        except (OSError, ValueError, TypeError):
            self.records = []

    def record(self, exc: BaseException, stage: str = "") -> None:
        try:
            self.records.append(exception_record(exc, stage))
            self.records = self.records[-KEEP_ERRORS:]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.records, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception:  # noqa: BLE001 記録できなくても処理は続ける
            pass

    def clear(self) -> None:
        self.records = []
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


def format_record(record: dict) -> list[str]:
    """1件の誤りを、診断情報に載せる行にする。"""
    head = f"{record.get('at', '')}　{record.get('type', '')}"
    extra = [f"{key}={record[key]}" for key in ("errno", "winerror") if key in record]
    if record.get("cause"):
        extra.append(f"原因 {record['cause']}")
    if extra:
        head += "（" + "・".join(extra) + "）"
    lines = [head]
    if record.get("stage"):
        lines.append(f"    処理：{record['stage']}")
    for where in record.get("where", []):
        lines.append(f"    {where}")
    return lines


def environment_lines(root, title: str, version: str) -> list[str]:
    """環境（版・OS・Python/Tk・画面）の行。利用者名・PC名・フォルダの場所は入れない。"""
    frozen = getattr(sys, "frozen", False)
    lines = [
        f"ツール：{title}  {version}（{'exe' if frozen else 'ソースから起動'}）",
        f"OS：{platform.platform()}",
        f"Python：{platform.python_version()}　Tk：{_tk_patchlevel(root)}",
    ]
    try:
        scale = root.winfo_fpixels("1i") / 96.0
        lines.append(
            f"画面：{root.winfo_screenwidth()}×{root.winfo_screenheight()}・拡大率 {scale:.0%}・"
            f"ウィンドウ {root.winfo_width()}×{root.winfo_height()}"
        )
    except Exception:  # noqa: BLE001 画面の情報が取れなくても診断情報は作る
        pass
    return lines


def _tk_patchlevel(root) -> str:
    try:
        return root.tk.call("info", "patchlevel")
    except Exception:  # noqa: BLE001
        return "不明"
