from pathlib import Path

from src.ingest_source import _resolve_title, parse_header


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_parse_header_fullwidth_colon_and_brackets(tmp_path):
    p = _write(
        tmp_path,
        "174_x.txt",
        "動画名：【ドイツ空軍の撃墜王 リヒトホーフェン】\n"
        "https://www.youtube.com/watch?v=DRsGNhLtUXU\n\n\n社長: そういうのが男の戦いですよ。\n",
    )
    title, url = parse_header(p)
    assert title == "ドイツ空軍の撃墜王 リヒトホーフェン"  # 外側【】は除去
    assert url == "https://www.youtube.com/watch?v=DRsGNhLtUXU"


def test_parse_header_halfwidth_colon_and_kagi(tmp_path):
    p = _write(
        tmp_path,
        "02_x.txt",
        "動画名: 「抗菌と免疫力について」\nhttps://youtu.be/_qDQR3GHsuU?si=abc\n社長: 本文。\n",
    )
    title, url = parse_header(p)
    assert title == "抗菌と免疫力について"
    assert url == "https://youtu.be/_qDQR3GHsuU?si=abc"


def test_parse_header_absent(tmp_path):
    """ヘッダーが無いファイルは (None, None)。"""
    p = _write(tmp_path, "plain.txt", "社長: いきなり本文。\n司会者: はい。\n")
    assert parse_header(p) == (None, None)


def test_parse_header_bom_stripped(tmp_path):
    p = _write(tmp_path, "bom.txt", "﻿動画名：テスト回\nhttps://youtu.be/x\n")
    title, url = parse_header(p)
    assert title == "テスト回"
    assert url == "https://youtu.be/x"


def test_resolve_title_priority(tmp_path):
    p = Path("174_リヒトホーフェン.txt")
    # 明示 > ヘッダー > ファイル名
    assert _resolve_title("明示タイトル", "ヘッダー名", p) == "明示タイトル"
    assert _resolve_title(None, "ヘッダー名", p) == "ヘッダー名"
    # ファイル名フォールバックは先頭連番プレフィックスを除く
    assert _resolve_title(None, None, p) == "リヒトホーフェン"
