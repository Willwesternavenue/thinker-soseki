"""対談・インタビュー形式(聞き手=ダッシュ、本人=無標識段落)のテスト。"""

from src.steps.chunk import chunk_source
from src.steps.clean import clean_pages

RAW = """第一回 夢 と 現 実

『夢十夜』を書く

—漱石さんが『夢十夜』を書かれたのはいつですか?

私が『夢十夜』を書いたのは明治四十一年です。""" + ("夢の話に魅かれた。" * 40) + """

—「夢」のどういった面に魅かれたのですか。

説明を拒むこと、つまり不可解さです。""" + ("不可解が生を支えている。" * 40) + """

第二回 則 天 去 私

—次の話に移りましょう。

そうですね。""" + ("則天去私について語ろう。" * 40)


def test_interview_normalizes_interviewer_and_chapters():
    result = clean_pages([RAW], source_type="interview")
    # 章見出し(第◯回)が正規化され、分かち書き空白が詰まっている
    assert "# 第一回 夢と現実" in result.text
    assert "# 第二回 則天去私" in result.text
    # 聞き手行が「質問者:」に正規化される
    assert "質問者: 漱石さんが『夢十夜』を書かれたのはいつですか?" in result.text
    # ダッシュ行がそのまま残らない
    assert "—漱石さん" not in result.text


def test_interview_chunks_are_self_verbatim_with_question_context():
    result = clean_pages([RAW], source_type="interview")
    chunks = chunk_source("INTV_001", result.text, "interview", result.page_offsets)
    assert chunks
    # 本人の答えのみが本文 → 全チャンク verbatim=true, 本人発言
    assert all(c.verbatim for c in chunks)
    assert all(c.speaker == "本人発言" for c in chunks)
    # 聞き手の質問は本文に混ざらず question(文脈)に入る
    assert all("質問者" not in c.text for c in chunks)
    first = chunks[0]
    assert first.question and "夢十夜" in first.question
    assert "夢十夜" in first.text or "夢の話" in first.text


def test_interview_chapters_detected():
    result = clean_pages([RAW], source_type="interview")
    chunks = chunk_source("INTV_001", result.text, "interview", result.page_offsets)
    chapters = {c.chapter_title for c in chunks}
    assert "第一回 夢と現実" in chapters
    assert "第二回 則天去私" in chapters
    assert chunks[0].chunk_id.startswith("INTV_001_R01_")


def test_interview_deterministic():
    a = clean_pages([RAW], source_type="interview")
    b = clean_pages([RAW], source_type="interview")
    ca = chunk_source("INTV_001", a.text, "interview", a.page_offsets)
    cb = chunk_source("INTV_001", b.text, "interview", b.page_offsets)
    assert [c.chunk_id for c in ca] == [c.chunk_id for c in cb]
    assert [c.chunk_hash for c in ca] == [c.chunk_hash for c in cb]


def test_book_mode_unaffected_by_interview_changes():
    """書籍(第◯章)は従来どおり。回チャプター対応が章を壊さないこと。"""
    book = "第一章 自意識\n" + ("近代の孤独。" * 100)
    cleaned = clean_pages([book], source_type="book")
    chunks = chunk_source("BOOK_009", cleaned.text, "book", cleaned.page_offsets)
    assert chunks
    assert chunks[0].chapter_title == "第一章 自意識"
    assert all(c.verbatim for c in chunks)
