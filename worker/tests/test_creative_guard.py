"""Creative Output Guard のテスト(T1設計書 §5 / §11 T4c)。

閾値がコード直書きでなく profile の設定から読まれることも検証する(仕様§8.1)。
"""

import pytest

from src.creative import guard


# ── 正規化(生成文・原典で同一のものを使う。仕様§5.1-1) ──


def test_normalize_removes_whitespace_and_punctuation():
    """空白・約物を除いてから比較する(改行や句読点の差で検出漏れしないため)。"""
    assert guard.normalize_text("こんな 夢を、見た。\n") == guard.normalize_text("こんな夢を見た")


def test_normalize_applies_nfkc():
    """全角英数や半角カナの表記差を吸収する。"""
    assert guard.normalize_text("ＡＢＣ") == guard.normalize_text("ABC")


# ── 最長共通部分文字列(仕様§5.1-3) ──


def test_longest_common_substring_finds_longest_shared_run():
    assert guard.longest_common_substring("こんな夢を見た", "私はこんな夢を見たのだ") == "こんな夢を見た"


def test_longest_common_substring_returns_empty_when_nothing_shared():
    assert guard.longest_common_substring("あいうえお", "かきくけこ") == ""


# ── n-gram 重複比率(仕様§5.1-3) ──


def test_ngram_overlap_ratio_is_one_for_identical_text():
    assert guard.ngram_overlap_ratio("こんな夢を見た", ["こんな夢を見た"], n=3) == 1.0


def test_ngram_overlap_ratio_is_zero_for_disjoint_text():
    assert guard.ngram_overlap_ratio("あいうえおかきくけこ", ["さしすせそたちつてと"], n=3) == 0.0


def test_ngram_overlap_ratio_handles_text_shorter_than_n():
    """n文字未満の生成文でゼロ除算しない。"""
    assert guard.ngram_overlap_ratio("あ", ["あいうえお"], n=10) == 0.0


# ── 原文類似検査(閾値は設定から読む。仕様§8.1) ──


def test_similarity_check_flags_long_verbatim_copy():
    """原典と長く一致したら違反として該当箇所とchunk_idを記録する。"""
    sources = [{"chunk_id": "c1", "text": "こんな夢を見た。腕組をして枕元に坐っていると、仰向に寝た女が静かな声でもう死にますと云う。"}]
    draft = "冒頭。こんな夢を見た。腕組をして枕元に坐っていると、仰向に寝た女が静かな声でもう死にますと云う。以上。"

    result = guard.check_similarity(draft, sources, {"lcs_threshold": 20, "ngram_n": 10, "ngram_overlap_ratio_max": 0.05})

    assert result["passed"] is False
    assert result["lcs_len"] >= 20
    assert "c1" in result["matched_chunk_ids"]


def test_similarity_check_passes_for_original_text():
    sources = [{"chunk_id": "c1", "text": "こんな夢を見た。腕組をして枕元に坐っていると、仰向に寝た女が静かな声でもう死にますと云う。"}]
    draft = "鏡の前に立つと、映った顔だけが老いていた。私は驚かず、いつもの通り髪を整えて部屋を出た。"

    result = guard.check_similarity(draft, sources, {"lcs_threshold": 20, "ngram_n": 10, "ngram_overlap_ratio_max": 0.05})

    assert result["passed"] is True


def test_similarity_check_reads_threshold_from_settings_not_hardcoded():
    """閾値を厳しくすると、同じ生成文でも違反になること(=設定が効いている)。"""
    sources = [{"chunk_id": "c1", "text": "こんな夢を見た。腕組をして枕元に坐っていると"}]
    draft = "冒頭。こんな夢を見た。以降は独自の文章が続く。"

    loose = guard.check_similarity(draft, sources, {"lcs_threshold": 20, "ngram_n": 10, "ngram_overlap_ratio_max": 1.0})
    strict = guard.check_similarity(draft, sources, {"lcs_threshold": 5, "ngram_n": 10, "ngram_overlap_ratio_max": 1.0})

    assert loose["passed"] is True
    assert strict["passed"] is False


# ── 誤認防止(機械検査。仕様§5.2) ──


@pytest.mark.parametrize(
    "text",
    [
        "これは夏目漱石の未発表作である。",
        "発見された第十一夜をお届けする。",
        "本人が書いた真作として伝わる。",
    ],
)
def test_misattribution_check_flags_forbidden_phrases(text):
    """真作と誤認させる定型句を検出する(仕様§8.2)。"""
    result = guard.check_misattribution(text)
    assert result["passed"] is False
    assert result["matched"]


def test_misattribution_check_passes_clean_text():
    result = guard.check_misattribution("鏡の前に立つと、映った顔だけが老いていた。")
    assert result["passed"] is True
    assert result["matched"] == []


# ── prohibition カード検査(LLM judge。仕様§5.2) ──


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0) if self.responses else {}


def test_prohibition_check_uses_light_model_and_reports_violations():
    """approvedなprohibitionカードへの違反を項目別に判定する。"""
    llm = FakeLLM({"violations": [{"card_id": "cc_1", "reason": "象徴の意味を解説している"}]})
    cards = [
        {"card_id": "cc_1", "card_type": "prohibition", "title": "象徴の意味を解説しない"},
        {"card_id": "cc_2", "card_type": "narrative", "title": "異常を自然に扱う"},
    ]

    result = guard.check_prohibitions("本文。鏡は時間の象徴である。", cards, call_json=llm)

    assert result["passed"] is False
    assert result["violations"][0]["card_id"] == "cc_1"
    # prohibitionカードだけをjudgeに渡す(narrativeカードは対象外)
    assert "象徴の意味を解説しない" in llm.calls[0]["prompt"]
    assert "異常を自然に扱う" not in llm.calls[0]["prompt"]


def test_prohibition_check_skips_llm_when_no_prohibition_cards():
    """prohibitionカードが無ければLLMを呼ばない(無駄な課金を避ける)。"""
    llm = FakeLLM()
    cards = [{"card_id": "cc_2", "card_type": "narrative", "title": "異常を自然に扱う"}]

    result = guard.check_prohibitions("本文。", cards, call_json=llm)

    assert result["passed"] is True
    assert llm.calls == []


# ── Guard統合(仕様§5) ──


def test_run_guard_combines_checks_and_separates_safety_from_creative():
    """作家固有の創作制約とシステム安全規則をtrace上で別キーに保存する(仕様§8.4)。"""
    llm = FakeLLM({"violations": []})
    sources = [{"chunk_id": "c1", "text": "こんな夢を見た。"}]
    cards = [{"card_id": "cc_1", "card_type": "prohibition", "title": "象徴を解説しない"}]

    result = guard.run_guard(
        "鏡の前に立つと、映った顔だけが老いていた。",
        sources=sources, cards=cards,
        settings={"lcs_threshold": 20, "ngram_n": 10, "ngram_overlap_ratio_max": 0.5},
        call_json=llm,
    )

    assert result["passed"] is True
    # trace用の構造(仕様§6)
    assert set(result) >= {"passed", "similarity", "misattribution", "prohibitions", "violations"}
    assert result["similarity"]["passed"] is True
    assert result["misattribution"]["passed"] is True


def test_run_guard_fails_and_lists_reasons_for_regeneration():
    """違反理由は再生成プロンプトへ渡せる形で返す(仕様§5.3)。"""
    llm = FakeLLM({"violations": []})
    sources = [{"chunk_id": "c1", "text": "こんな夢を見た。腕組をして枕元に坐っていると、仰向に寝た女が"}]
    draft = "こんな夢を見た。腕組をして枕元に坐っていると、仰向に寝た女が。これは夏目漱石の未発表作である。"

    result = guard.run_guard(
        draft, sources=sources, cards=[],
        settings={"lcs_threshold": 20, "ngram_n": 10, "ngram_overlap_ratio_max": 0.05},
        call_json=llm,
    )

    assert result["passed"] is False
    assert result["violations"], "再生成プロンプトに渡す違反理由が必要"
    joined = " ".join(result["violations"])
    assert "原文" in joined or "類似" in joined
    assert "誤認" in joined or "未発表" in joined
