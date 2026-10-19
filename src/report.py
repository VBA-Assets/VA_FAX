# -*- coding: utf-8 -*-
"""変換レポートの生成・保存（F-25）。

変換レポートには元ファイル名等の文書情報を含むため、VA FAXの管理領域
（%APPDATA%等）には一切保存せず、出力PDFと同じフォルダにのみ保存する（N-06）。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.convert import CompressionMode, PageConversionLog

_DITHER_LABELS = {"threshold": "単純閾値", "diffusion": "誤差拡散"}
_MODE_LABELS = {"binarize": "二値化（白黒）", "grayscale": "グレースケール"}
_CAPACITY_METHOD_LABELS = {"reduce_resolution": "解像度・品質を下げて再実行", "split": "分割して再実行"}


@dataclass(frozen=True)
class ReportData:
    """変換レポートの元となる情報。"""

    input_filename: str
    input_size_bytes: int
    output_filename: str
    profile_display_name: str
    mode: CompressionMode
    normalize_enabled: bool
    capacity_exceeded_method: str
    page_logs: tuple[PageConversionLog, ...]
    final_output_size_bytes: int
    jpeg_quality_requested: Optional[int] = None


def _format_size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} バイト"


def _format_page_line(log: PageConversionLog) -> str:
    parts = [f"元サイズ {log.original_width_mm:.1f}×{log.original_height_mm:.1f}mm"]
    if log.rotated:
        parts.append("90度回転")
    if log.normalized:
        if log.scale_ratio < 0.999:
            parts.append(f"縮小 {log.scale_ratio * 100:.0f}%")
        else:
            # 正規化先サイズより小さいページは拡大せず、原寸のまま中央配置する。
            parts.append("中央配置（原寸）")
    if log.dither_method:
        parts.append(f"二値化方式: {_DITHER_LABELS.get(log.dither_method, log.dither_method)}")
    parts.append(f"解像度: {log.resolution_dpi}dpi")
    if log.jpeg_quality is not None:
        parts.append(f"JPEG品質: {log.jpeg_quality}")
    return f"{log.page_index + 1}ページ目： " + " / ".join(parts)


def build_report_text(data: ReportData) -> str:
    """変換レポートのテキストを生成する。"""
    lines = [
        "VA FAX 変換レポート",
        "=" * 40,
        f"元ファイル名: {data.input_filename}",
        f"元ファイルサイズ: {_format_size(data.input_size_bytes)}",
        f"出力ファイル名: {data.output_filename}",
        f"出力ファイルサイズ: {_format_size(data.final_output_size_bytes)}",
        f"対象サービス: {data.profile_display_name}",
        f"ページ数: {len(data.page_logs)}",
        f"圧縮方式: {_MODE_LABELS.get(data.mode, data.mode)}",
        f"用紙正規化: {'有効' if data.normalize_enabled else '無効'}",
        f"容量超過時の処理方式: "
        f"{_CAPACITY_METHOD_LABELS.get(data.capacity_exceeded_method, data.capacity_exceeded_method)}",
    ]
    if data.mode == "grayscale" and data.jpeg_quality_requested is not None:
        lines.append(f"JPEG品質（初期設定）: {data.jpeg_quality_requested}")

    lines.append("")
    lines.append("ページ別処理内容")
    lines.append("-" * 40)
    lines.extend(_format_page_line(log) for log in data.page_logs)

    lines.append("")
    lines.append("本レポートはVA FAXの管理領域には保存されません（出力PDFと同じフォルダにのみ保存されます）。")
    return "\n".join(lines)


def report_path_for(output_pdf_path: Path) -> Path:
    """出力PDFと同じフォルダに置くレポートファイルのパスを返す。"""
    return output_pdf_path.with_name(f"{output_pdf_path.stem}_変換レポート.txt")


def save_report(output_pdf_path: Path, data: ReportData) -> Path:
    """変換レポートを出力PDFと同じフォルダに保存する。"""
    path = report_path_for(output_pdf_path)
    path.write_text(build_report_text(data), encoding="utf-8")
    return path
