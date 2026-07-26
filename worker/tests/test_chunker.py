"""チャンカーのテスト(仕様6.3)。

- 決定性: 同じテキスト・同じchunker_versionなら同じchunk_id・同じchunk_hash
- 書籍: 章検出・600〜1500字パッキング・verbatim=true
- QA: 質問+回答ペア・話者・verbatim導出
"""

from src.steps.chunk import TARGET_MAX, chunk_source

BOOK_TEXT = (
    "# 第一章 武士道と死\n"
    + ("死の覚悟について述べる。" * 60)  # 720字
    + "\n\n"
    + ("義とは何かを論じる。" * 70)  # 700字
    + "\n# 第二章 憧れ\n"
    + ("憧れが生を支える。" * 80)  # 720字
)

QA_TEXT = """[00:03:12]
質問者: 絶対負とは何ですか?
本人発言: 絶対負とは、正に敗れた負ではない。根源的なエネルギーのことだ。
[00:05:20]
質問者: それはネガティブ思考とは違うのですか?
本人発言: 全く違う。宇宙と生命を支える力の話をしている。
"""


def test_book_chunking_is_deterministic():
    """同じ入力から常に同じchunk_id・chunk_hashが得られる(仕様6.3要件)。"""
    a = chunk_source("BOOK_001", BOOK_TEXT, "book")
    b = chunk_source("BOOK_001", BOOK_TEXT, "book")
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]
    assert [c.chunk_hash for c in a] == [c.chunk_hash for c in b]


def test_book_chapters_detected():
    chunks = chunk_source("BOOK_001", BOOK_TEXT, "book")
    chapters = {c.chapter_title for c in chunks}
    assert "第一章 武士道と死" in chapters
    assert "第二章 憧れ" in chapters
    ch1 = [c for c in chunks if c.chapter_title == "第一章 武士道と死"]
    assert ch1[0].chunk_id == "BOOK_001_CH01_001"


def test_book_chunk_sizes_bounded():
    chunks = chunk_source("BOOK_001", BOOK_TEXT, "book")
    for c in chunks:
        assert len(c.text) <= TARGET_MAX + 100


def test_book_chunks_are_verbatim():
    """書籍本文は原則 verbatim=true(仕様5.3)。"""
    chunks = chunk_source("BOOK_001", BOOK_TEXT, "book")
    assert all(c.verbatim for c in chunks)


def test_qa_pairs_extracted():
    chunks = chunk_source("VIDEO_001", QA_TEXT, "video_transcript")
    qa = [c for c in chunks if c.chunk_type == "qa_pair"]
    assert len(qa) == 2
    first = qa[0]
    assert first.chunk_id == "VIDEO_001_QA_001"
    assert first.question == "絶対負とは何ですか?"
    assert "根源的なエネルギー" in (first.answer or "")
    assert first.speaker == "本人発言"
    assert first.verbatim is True
    assert first.timestamp_start == "00:03:12"


def test_qa_chunk_id_deterministic():
    a = chunk_source("VIDEO_001", QA_TEXT, "video_transcript")
    b = chunk_source("VIDEO_001", QA_TEXT, "video_transcript")
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]


def test_hash_changes_when_text_changes():
    """chunk_hashは再処理時の差分検出に使える(仕様6.3)。"""
    a = chunk_source("VIDEO_001", QA_TEXT, "video_transcript")
    b = chunk_source(
        "VIDEO_001", QA_TEXT.replace("根源的なエネルギー", "根源の力"), "video_transcript"
    )
    assert a[0].chunk_id == b[0].chunk_id
    assert a[0].chunk_hash != b[0].chunk_hash


def test_monologue_transcript_without_labels():
    """話者ラベルの無い書き起こしは本人のモノローグとして扱う。"""
    text = "今日は絶対負について話す。" * 60
    chunks = chunk_source("VIDEO_002", text, "video_transcript")
    assert chunks
    assert all(c.verbatim for c in chunks)
    assert all(c.speaker == "本人発言" for c in chunks)
