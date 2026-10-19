# -*- coding: utf-8 -*-
"""診断ログの設定（F-24）。

ログにはファイル名・パスを一切記録しない（切替は設けず常時この方針とする）。
記録してよいのは処理段階・エラー種別・事前診断結果（用紙サイズ・色判定・
テキスト有無等）・解像度・再試行内容等の技術情報に限る
（本モジュール自体はログメッセージの内容を検閲しない。呼出側が責務を負う）。
ログの保存件数・期間には上限を設け、古いものから自動的に削除する。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from config.settings import app_data_dir

LOG_FILENAME_PREFIX = "va_fax_"
MAX_LOG_AGE_DAYS = 30
MAX_LOG_FILE_COUNT = 30

LOGGER_NAME = "va_fax"


def logs_dir() -> Path:
    return app_data_dir() / "logs"


def _log_file_path(day: date) -> Path:
    return logs_dir() / f"{LOG_FILENAME_PREFIX}{day.isoformat()}.log"


def _parse_log_date(path: Path) -> Optional[date]:
    if not path.stem.startswith(LOG_FILENAME_PREFIX):
        return None
    try:
        return date.fromisoformat(path.stem[len(LOG_FILENAME_PREFIX):])
    except ValueError:
        return None


def cleanup_old_logs(today: date, max_age_days: int = MAX_LOG_AGE_DAYS,
                      max_count: int = MAX_LOG_FILE_COUNT) -> None:
    """保存期間・保存件数の上限を超えたログファイルを古いものから削除する。"""
    directory = logs_dir()
    if not directory.is_dir():
        return

    entries = sorted(
        (d, p) for p in directory.glob(f"{LOG_FILENAME_PREFIX}*.log")
        if (d := _parse_log_date(p)) is not None
    )

    cutoff = today - timedelta(days=max_age_days)
    kept = [(d, p) for d, p in entries if d >= cutoff]
    expired = [(d, p) for d, p in entries if d < cutoff]
    for _, path in expired:
        path.unlink(missing_ok=True)

    if len(kept) > max_count:
        for _, path in kept[: len(kept) - max_count]:
            path.unlink(missing_ok=True)


def setup_logger(today: Optional[date] = None) -> logging.Logger:
    """アプリケーションロガーを構成し、日付別のログファイルへ出力する。"""
    day = today or date.today()
    directory = logs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    cleanup_old_logs(today=day)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    file_handler = logging.FileHandler(_log_file_path(day), encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(file_handler)
    return logger
