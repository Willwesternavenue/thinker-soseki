"""青空文庫 Parser / Normalizer のテスト(C-T3 / 仕様 docs/CORPUS_T1_SPEC.md §7)。

実データ(夢十夜 799_ruby_6024.zip)の構造に基づく。
"""

import pytest

from src.aozora import parse

# 実ファイルの構造を写したfixture。ヘッダ(記号説明)・本文・フッタ(底本情報)の3部構成。
SAMPLE = """夢十夜
夏目漱石

-------------------------------------------------------
【テキスト中に現れる記号について】

《》：ルビ
（例）坐《すわ》って

｜：ルビの付く文字列の始まりを特定する記号
（例）右｜堀田原《ほったはら》とある

［＃］：入力者注　主に外字の説明や、傍点の位置の指定
-------------------------------------------------------

［＃５字下げ］第一夜［＃「第一夜」は中見出し］

　こんな夢を見た。
　腕組をして枕元に坐《すわ》っていると、仰向《あおむき》に寝た女が、\
静かな声でもう死にますと云う。

［＃５字下げ］第二夜［＃「第二夜」は中見出し］

　こんな夢を見た。
　和尚《おしょう》の室《へや》を退《さが》ると、廊下伝いに自分の部屋へ帰った。



底本：「夏目漱石全集10巻」ちくま文庫、筑摩書房
　　　1988（昭和63）年7月26日第1刷発行
　　　1996（平成8）年7月15日第5刷発行
底本の親本：「筑摩全集類聚版夏目漱石全集」筑摩書房
入力：野口英司
1997年12月16日公開
2013年7月17日修正
青空文庫作成ファイル：
このファイルは、インターネットの図書館、青空文庫で作られました。
"""


# ── 文字コード(§7 / 指示書§8.2) ──


def test_decode_uses_cp932_not_shift_jis():
    """機種依存文字を含むためCP932で変換する(shift_jisだと落ちる)。"""
    # 「①」はCP932にはあるがshift_jisには無い
    raw = "第①夜".encode("cp932")
    assert parse.decode_aozora_bytes(raw) == "第①夜"


def test_decode_reports_replacement_ratio():
    """変換できないバイトの割合を記録する(閾値超過はIndex登録しないため)。"""
    raw = "正常な本文".encode("cp932") + b"\x80\x81"
    text, ratio = parse.decode_aozora_bytes(raw, with_ratio=True)
    assert ratio > 0
    assert "正常な本文" in text


# ── ヘッダ・フッタ分離(指示書§8.5) ──


def test_split_document_separates_header_body_footer():
    doc = parse.split_document(SAMPLE)

    assert doc["title"] == "夢十夜"
    assert doc["author"] == "夏目漱石"
    # 記号説明は本文へ混ぜない
    assert "【テキスト中に現れる記号について】" not in doc["body"]
    assert "こんな夢を見た。" in doc["body"]
    # 底本情報も本文へ混ぜない
    assert "底本：" not in doc["body"]
    assert "青空文庫作成ファイル" not in doc["body"]


def test_footer_metadata_is_kept_not_discarded():
    """底本・入力者は破棄せずmetadataとして保存する(指示書§2.4)。"""
    doc = parse.split_document(SAMPLE)

    assert doc["colophon"]["底本"].startswith("「夏目漱石全集10巻」ちくま文庫")
    assert doc["colophon"]["底本の親本"].startswith("「筑摩全集類聚版夏目漱石全集」")
    assert doc["colophon"]["入力"] == "野口英司"


# ── ルビ(指示書§8.3) ──


def test_strip_ruby_keeps_kanji_and_extracts_reading():
    """embedding用本文には漢字を残し、読みは重複挿入しない。"""
    text, rubies = parse.extract_ruby("腕組をして枕元に坐《すわ》っていると")

    assert text == "腕組をして枕元に坐っていると"
    assert rubies == [{"surface": "坐", "reading": "すわ"}]


def test_strip_ruby_handles_explicit_start_marker():
    """｜でルビの付く範囲が明示される場合、｜自体は本文へ残さない。"""
    text, rubies = parse.extract_ruby("右｜堀田原《ほったはら》とある")

    assert text == "右堀田原とある"
    assert rubies == [{"surface": "堀田原", "reading": "ほったはら"}]


def test_strip_ruby_handles_multiple_occurrences():
    text, rubies = parse.extract_ruby("和尚《おしょう》の室《へや》を退《さが》ると")
    assert text == "和尚の室を退ると"
    assert [r["reading"] for r in rubies] == ["おしょう", "へや", "さが"]


# ── 注記(指示書§8.4) ──


@pytest.mark.parametrize(
    "note,expected_kind",
    [
        ("［＃５字下げ］", "formatting_note"),
        ("［＃「第一夜」は中見出し］", "formatting_note"),
        ("［＃「目＋爭」、第3水準1-88-85］", "gaiji_note"),
        ("［＃ここから２字下げ］", "formatting_note"),
        ("［＃「自分」に傍点］", "emphasis_note"),
    ],
)
def test_classify_note_kinds(note, expected_kind):
    """注記は単純削除せず種別に分類して保存する。"""
    assert parse.classify_note(note) == expected_kind


def test_extract_notes_removes_from_body_but_keeps_record():
    text, notes = parse.extract_notes("［＃５字下げ］第一夜［＃「第一夜」は中見出し］")

    assert text == "第一夜"
    assert len(notes) == 2
    assert all(n["kind"] == "formatting_note" for n in notes)
    assert notes[0]["raw"] == "［＃５字下げ］"


# ── 章分割(指示書§8.6) ──


def test_split_chapters_uses_heading_notes():
    """固定文字数で切らず、中見出しの注記を章の境界として使う。"""
    doc = parse.split_document(SAMPLE)
    chapters = parse.split_chapters(doc["body"])

    assert [c["chapter_title"] for c in chapters] == ["第一夜", "第二夜"]
    assert "こんな夢を見た。" in chapters[0]["text"]
    assert "和尚" in chapters[1]["text"]


# ── 3形式の本文(指示書§8.1) ──


def test_normalize_document_produces_three_text_forms():
    doc = parse.normalize_document(SAMPLE)

    # raw_text: 注記を残した本文
    assert "《すわ》" in doc["raw_text"]
    # normalized_text: 検索・embedding用(ルビ・注記を除去)
    assert "《" not in doc["normalized_text"]
    assert "［＃" not in doc["normalized_text"]
    assert "坐っていると" in doc["normalized_text"]
    # display_text: 表示・引用用(ルビは読みを括弧で保持)
    assert "坐（すわ）っていると" in doc["display_text"]


def test_normalize_document_records_provenance():
    """再現性のためhashとparser versionを持たせる(指示書§15-19)。"""
    doc = parse.normalize_document(SAMPLE)

    assert len(doc["content_sha256"]) == 64
    assert doc["parser_version"] == parse.PARSER_VERSION
    assert doc["colophon"]["入力"] == "野口英司"
