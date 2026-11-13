# -*- coding: utf-8 -*-
"""診断情報（［ヘルプ］→［診断情報をコピー］）の試験。

いちばん大事なのは「中身が入らないこと」：例外のメッセージの本文（OSError等が持つ
ファイルの場所・名前）を、記録にも診断情報の文にも入れないこと（F-24と同じ方針）。
"""
from __future__ import annotations

import json

import diagnostics


def _raise_oserror(path: str) -> OSError:
    """ファイルの場所を含むメッセージを持つ例外を作る（実際の失敗と同じ形）。"""
    try:
        raise FileNotFoundError(2, "No such file or directory", path)
    except OSError as exc:
        return exc


def test_record_has_no_message_or_path():
    """記録に入るのは型と場所（ファイル名:行:関数）だけで、メッセージ本文は入らない。"""
    secret = r"C:\事件\依頼者名\契約書.pdf"
    record = diagnostics.exception_record(_raise_oserror(secret), "変換")

    dumped = json.dumps(record, ensure_ascii=False)
    assert "依頼者名" not in dumped
    assert "契約書" not in dumped
    assert "No such file" not in dumped
    assert record["type"] == "FileNotFoundError"
    assert record["stage"] == "変換"
    assert record["errno"] == 2
    # 場所は、このテストファイルの名前・行・関数名だけ（絶対パスは入れない）
    assert record["where"]
    assert all(w.startswith("test_diagnostics.py:") for w in record["where"][-1:])


def test_format_record_lines():
    record = diagnostics.exception_record(_raise_oserror("x.pdf"), "事前診断")
    lines = diagnostics.format_record(record)
    assert "FileNotFoundError" in lines[0]
    assert "errno=2" in lines[0]
    assert lines[1] == "    処理：事前診断"


def test_error_log_keeps_recent_and_persists(tmp_path):
    """誤りは新しいものだけを覚え、ファイルにも残す（閉じた後でも渡せるように）。"""
    path = tmp_path / "errors.json"
    log = diagnostics.ErrorLog(path)
    for i in range(diagnostics.KEEP_ERRORS + 5):
        log.record(_raise_oserror(f"{i}.pdf"), "変換")

    assert len(log.records) == diagnostics.KEEP_ERRORS
    assert path.is_file()
    # 作り直しても、前回までの記録を読み戻せる
    assert len(diagnostics.ErrorLog(path).records) == diagnostics.KEEP_ERRORS

    log.clear()
    assert log.records == []
    assert not path.exists()


def test_error_log_never_raises(tmp_path):
    """書けない場所でも、画面の動きを止めない（記録は諦める）。"""
    path = tmp_path / "errors.json"
    path.mkdir()  # 同名のフォルダを置いて、書き込みを失敗させる
    log = diagnostics.ErrorLog(path)
    log.record(_raise_oserror("x.pdf"), "変換")  # 例外を投げないこと
    assert len(log.records) == 1


def test_errors_path_is_in_app_data():
    """誤りの記録は、ログと同じ管理領域に置く（出力先フォルダ等には書かない。N-06）。"""
    from config.settings import app_data_dir

    assert diagnostics.errors_path().parent == app_data_dir()
