"""青空文庫チャンカーのテスト(C-T3c / 仕様 docs/CORPUS_T1_SPEC.md §7)。

固定文字数で切らない。優先順位は 章 → 節 → 段落 → 話者交代 → 意味段落 → token上限。
既存の CHUNKER_VERSION='v1' とは別系統(`aozora_v1`)。
"""

from src.aozora import chunk


def test_chunker_version_is_separate_from_existing():
    """既存の思想モード用チャンカーと別系統にする(既存チャンクを再生成しないため)。"""
    assert chunk.CHUNKER_VERSION == "aozora_v1"


# ── 段落単位(§8.6) ──


def test_splits_on_paragraphs_not_fixed_length():
    """固定文字数ではなく段落で切る。"""
    text = "　最初の段落である。\n　二つ目の段落である。\n　三つ目の段落である。"

    chunks = chunk.chunk_chapter(text, max_chars=1000)

    assert len(chunks) == 1, "上限内なら段落をまとめて1チャンクにする"
    assert "最初の段落" in chunks[0]["text"]


def test_keeps_paragraph_boundaries_when_splitting():
    """上限を超えるときも段落の途中では切らない。"""
    paras = ["　" + "あ" * 200 for _ in range(5)]
    chunks = chunk.chunk_chapter("\n".join(paras), max_chars=500)

    assert len(chunks) > 1
    for c in chunks:
        # 段落の途中で切れていないこと(各チャンクは段落の連結でできている)
        for para in c["text"].split("\n"):
            assert para.strip() in [p.strip() for p in paras]


# ── 話者交代(§8.6 小説の要件) ──


def test_dialogue_paragraph_is_marked_as_speech():
    """会話文(「で始まる段落)を識別できるようにする。"""
    text = "　女がこう云った。\n「死んだら、埋めて下さい」\n　自分は黙っていた。"

    chunks = chunk.chunk_chapter(text, max_chars=1000, source_type="novel")

    kinds = [c["chunk_type"] for c in chunks]
    assert "dialogue" in kinds, "会話文を地の文と区別できること"
    assert "narration" in kinds


def test_dialogue_is_separated_from_narration():
    """話者交代で切る。地の文と会話文が同じチャンクに混ざらない。"""
    text = "　女がこう云った。\n「死んだら、埋めて下さい」\n　自分は黙っていた。"

    chunks = chunk.chunk_chapter(text, max_chars=1000, source_type="novel")

    speech = [c for c in chunks if c["chunk_type"] == "dialogue"]
    assert len(speech) == 1
    assert "死んだら" in speech[0]["text"]
    assert "女がこう云った" not in speech[0]["text"]


def test_lecture_does_not_split_on_dialogue():
    """講演・評論では会話文で切らない(主張の連続性を保つため)。"""
    text = "　私はこう考える。\n「引用された文」\n　だから結論はこうなる。"

    chunks = chunk.chunk_chapter(text, max_chars=1000, source_type="lecture")

    assert len(chunks) == 1
    assert all(c["chunk_type"] == "body" for c in chunks)


# ── 位置情報(§7.4) ──


def test_chunks_carry_char_offsets():
    """引用位置を特定できるよう文字位置を持たせる。"""
    text = "　最初の段落。\n　二つ目の段落。"
    chunks = chunk.chunk_chapter(text, max_chars=10)

    assert chunks[0]["char_start"] == 0
    assert chunks[0]["char_end"] > 0
    # 位置が本文と整合していること(evidence span 整合性検査の前提)
    assert text[chunks[1]["char_start"]:chunks[1]["char_end"]].strip() == chunks[1]["text"].strip()


def test_chunks_are_numbered_in_order():
    text = "\n".join(f"　段落{i}である。" for i in range(5))
    chunks = chunk.chunk_chapter(text, max_chars=12)
    assert [c["paragraph_start"] for c in chunks] == sorted(
        c["paragraph_start"] for c in chunks
    )


# ── 決定性(既存チャンカーと同じ規律) ──


def test_chunking_is_deterministic():
    """同じ入力なら常に同じ chunk_id / hash になる。"""
    text = "　最初の段落。\n「会話文」\n　最後の段落。"

    a = chunk.chunk_document("SRC_X", [{"chapter_title": "第一夜", "text": text}],
                             source_type="novel")
    b = chunk.chunk_document("SRC_X", [{"chapter_title": "第一夜", "text": text}],
                             source_type="novel")

    assert [c["chunk_id"] for c in a] == [c["chunk_id"] for c in b]
    assert [c["chunk_hash"] for c in a] == [c["chunk_hash"] for c in b]


def test_chunk_ids_include_source_and_chapter():
    chunks = chunk.chunk_document(
        "SRC_YUME", [{"chapter_title": "第一夜", "text": "　本文である。"}],
        source_type="novel",
    )
    assert chunks[0]["chunk_id"].startswith("SRC_YUME_")
    assert chunks[0]["chapter_title"] == "第一夜"


def test_chunk_document_covers_all_chapters():
    chapters = [
        {"chapter_title": "第一夜", "text": "　一つ目。"},
        {"chapter_title": "第二夜", "text": "　二つ目。"},
    ]
    chunks = chunk.chunk_document("SRC_X", chapters, source_type="novel")

    assert {c["chapter_title"] for c in chunks} == {"第一夜", "第二夜"}


# ── 意味段落での分割(§8.6 の5段目) ──


def test_splits_overlong_paragraph_at_sentence_boundary():
    """1段落が上限を大きく超える場合は文末で分ける(講演・評論は長大な段落が多い)。"""
    para = "　" + "".join(f"これは第{i}の文である。" for i in range(60))
    chunks = chunk.chunk_chapter(para, max_chars=300, source_type="lecture")

    assert len(chunks) > 1
    for c in chunks:
        assert len(c["text"]) <= 600, "上限の2倍を超えるチャンクを作らない"
        # 文の途中で切っていないこと
        assert c["text"].rstrip().endswith("。")


def test_does_not_split_paragraph_that_fits():
    """上限に収まる段落は文単位に刻まない(文脈を保つ)。"""
    para = "　短い段落である。二文目もある。"
    chunks = chunk.chunk_chapter(para, max_chars=300, source_type="lecture")
    assert len(chunks) == 1


def test_sentence_split_keeps_char_offsets_consistent():
    """文で分けても位置情報は本文と整合していること(evidence span の前提)。"""
    para = "　" + "".join(f"これは第{i}の文である。" for i in range(40))
    chunks = chunk.chunk_chapter(para, max_chars=200, source_type="lecture")

    for c in chunks:
        assert para[c["char_start"]:c["char_end"]] == c["text"]
