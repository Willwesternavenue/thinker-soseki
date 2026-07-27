"""Pass2 LLM分類 と Pass4 レビューキュー(C-T4b / 仕様 docs/CORPUS_T1_SPEC.md §4)。

Pass2 は**候補値を出すだけ**で、安全側の決定を覆せない。
最重要は「LLM が小説を作者の直接発言へ昇格させられない」こと。
LLM は注入して差し替える(実呼び出しはしない)。
"""

import pytest

from src.aozora import tag


def _llm(payload):
    """call_json の差し替え。渡した dict をそのまま返す。"""
    def call(**_kwargs):
        return payload
    return call


# ── Pass2 が Pass1 の安全側判定を覆せないこと(最重要) ──


def test_llm_cannot_promote_fiction_to_author_direct():
    """小説のチャンクを作者の直接発言にはできない。"""
    pass1 = tag.deterministic_chunk_tags(
        {"text": "こんな夢を見た。", "chunk_type": "narration"},
        document_genre="short_story",
    )

    merged = tag.merge_pass2(
        pass1,
        {"speaker_role": "author_direct", "thought_eligibility": "candidate",
         "confidence": 0.99, "reason": "作者の思想だと判断した"},
        document_genre="short_story",
    )

    assert merged["speaker_role"] == "narrator"
    assert merged["thought_eligibility"] == "excluded"


def test_llm_cannot_raise_thought_eligibility():
    """適格性は下げられるが上げられない(Pass1 が上限)。"""
    # 段落の大半を占める長いブロック引用 → Pass1 が support まで落とす
    pass1 = tag.deterministic_chunk_tags(
        {
            "text": "「西洋の開化は内発的であって、日本の現代の開化は外発的なものである」",
            "chunk_type": "narration",
        },
        document_genre="lecture",
    )
    assert pass1["thought_eligibility"] == "support"

    merged = tag.merge_pass2(
        pass1, {"thought_eligibility": "candidate", "confidence": 0.9},
        document_genre="lecture",
    )

    assert merged["thought_eligibility"] == "support"


def test_llm_can_lower_thought_eligibility():
    pass1 = tag.deterministic_chunk_tags(
        {"text": "開化は内発的である。", "chunk_type": "narration"},
        document_genre="lecture",
    )
    assert pass1["thought_eligibility"] == "candidate"

    merged = tag.merge_pass2(
        pass1, {"thought_eligibility": "excluded", "confidence": 0.9},
        document_genre="lecture",
    )

    assert merged["thought_eligibility"] == "excluded"


def test_non_fiction_cannot_be_narrator_or_character():
    """講演・評論に「登場人物」「語り手」はいない。

    実データで LLM が講演の話者を character と分類する傾向が出た（講演者を
    語りの中の人物と見なす）。これは本文についての情報ではなくカテゴリの誤りなので、
    Pass3 でレビューへ回すのではなくここで閉じる（回さないとキューが埋まって使えなくなる）。
    """
    pass1 = tag.deterministic_chunk_tags(
        {"text": "私はここに立っております。", "chunk_type": "narration"},
        document_genre="lecture",
    )

    merged = tag.merge_pass2(
        pass1, {"speaker_role": "character", "confidence": 0.85},
        document_genre="lecture",
    )

    assert merged["speaker_role"] == "author_direct"
    # 何が起きたかは残す(黙って直すと後から追えない)
    assert "character" in merged["classification_reason"]


def test_non_fiction_character_with_quotation_becomes_quoted_person():
    """引用だと言っているなら quoted_person として受け取る。"""
    pass1 = tag.deterministic_chunk_tags(
        {"text": "彼はこう述べた。", "chunk_type": "narration"}, document_genre="lecture"
    )

    merged = tag.merge_pass2(
        pass1,
        {"speaker_role": "character", "is_quotation": True, "confidence": 0.9},
        document_genre="lecture",
    )

    assert merged["speaker_role"] == "quoted_person"


def test_coerced_speaker_role_does_not_flag_review_by_itself():
    """カテゴリ誤りを直しただけでレビュー行きにしない(確信度が足りていれば)。"""
    pass1 = tag.deterministic_chunk_tags(
        {"text": "本文", "chunk_type": "narration"}, document_genre="lecture"
    )

    merged = tag.merge_pass2(
        pass1, {"speaker_role": "narrator", "confidence": 0.9},
        document_genre="lecture",
    )

    assert tag.check_consistency(merged, document_genre="lecture") == []
    assert tag.needs_review(merged, []) is False


def test_fiction_speaker_role_is_limited_to_narrator_and_character():
    pass1 = tag.deterministic_chunk_tags(
        {"text": "「もう死にます」と云った。", "chunk_type": "dialogue"},
        document_genre="short_story",
    )

    merged = tag.merge_pass2(
        pass1, {"speaker_role": "editor", "confidence": 0.9},
        document_genre="short_story",
    )

    assert merged["speaker_role"] in ("narrator", "character")


# ── Pass2 が付けてよいもの ──


def test_llm_can_mark_embedded_quotation_in_lecture():
    """地の文に埋め込まれた引用は Pass2 の担当(§4.1)。"""
    pass1 = tag.deterministic_chunk_tags(
        {"text": "彼は開化は外発的だと述べた。私はそうは思わない。", "chunk_type": "narration"},
        document_genre="lecture",
    )
    assert pass1["is_quotation"] is False

    merged = tag.merge_pass2(
        pass1,
        {"speaker_role": "quoted_person", "is_quotation": True,
         "thought_eligibility": "support", "claim_type": "quotation",
         "assertion_status": "attributed", "confidence": 0.85},
        document_genre="lecture",
    )

    assert merged["is_quotation"] is True
    assert merged["speaker_role"] == "quoted_person"
    assert merged["thought_eligibility"] == "support"


def test_llm_fills_claim_type_left_open_by_pass1():
    pass1 = tag.deterministic_chunk_tags(
        {"text": "自己本位でなければならない。", "chunk_type": "narration"},
        document_genre="lecture",
    )
    assert pass1["claim_type"] is None

    merged = tag.merge_pass2(
        pass1, {"claim_type": "normative_claim", "confidence": 0.9},
        document_genre="lecture",
    )

    assert merged["claim_type"] == "normative_claim"


def test_records_confidence_and_reason():
    pass1 = tag.deterministic_chunk_tags({"text": "本文", "chunk_type": "narration"},
                                         document_genre="lecture")

    merged = tag.merge_pass2(
        pass1, {"confidence": 0.82, "reason": "講演の地の文で主張が明確"},
        document_genre="lecture",
    )

    assert merged["tag_confidence"] == 0.82
    assert "講演" in merged["classification_reason"]


# ── 不正な出力を握りつぶさない ──


def test_unknown_enum_value_falls_back_and_flags_review():
    pass1 = tag.deterministic_chunk_tags({"text": "本文", "chunk_type": "narration"},
                                         document_genre="lecture")

    merged = tag.merge_pass2(
        pass1, {"speaker_role": "なにか", "claim_type": "存在しない種別", "confidence": 0.9},
        document_genre="lecture",
    )

    assert merged["speaker_role"] == "author_direct"  # Pass1 の値へ戻す
    assert merged["claim_type"] is None
    assert tag.needs_review(merged, []) is True


def test_missing_confidence_is_treated_as_uncertain():
    pass1 = tag.deterministic_chunk_tags({"text": "本文", "chunk_type": "narration"},
                                         document_genre="lecture")

    merged = tag.merge_pass2(pass1, {}, document_genre="lecture")

    assert merged["tag_confidence"] == 0.0
    assert tag.needs_review(merged, []) is True


def test_ironic_or_hypothetical_goes_to_review():
    pass1 = tag.deterministic_chunk_tags({"text": "本文", "chunk_type": "narration"},
                                         document_genre="lecture")

    merged = tag.merge_pass2(
        pass1,
        {"assertion_status": "ironic", "is_ironic": True, "confidence": 0.95},
        document_genre="lecture",
    )

    assert tag.needs_review(merged, []) is True


# ── バッチ分類 ──


def test_classify_chunks_maps_results_back_by_chunk_id():
    chunks = [
        {"chunk_id": "A_001", "text": "開化は内発的である。", "chunk_type": "narration"},
        {"chunk_id": "A_002", "text": "彼はそう述べた。", "chunk_type": "narration"},
    ]

    result = tag.classify_chunks(
        chunks,
        document_genre="lecture",
        corpus_role="core_thought",
        call_json=_llm({"chunks": [
            {"chunk_id": "A_002", "speaker_role": "quoted_person",
             "is_quotation": True, "confidence": 0.9},
            {"chunk_id": "A_001", "claim_type": "descriptive_observation",
             "confidence": 0.9},
        ]}),
    )

    assert result["A_001"]["claim_type"] == "descriptive_observation"
    assert result["A_002"]["speaker_role"] == "quoted_person"


def test_classify_chunks_flags_chunks_the_llm_skipped():
    """LLMが返さなかったチャンクを「分類済み」にしない。"""
    chunks = [
        {"chunk_id": "A_001", "text": "本文1", "chunk_type": "narration"},
        {"chunk_id": "A_002", "text": "本文2", "chunk_type": "narration"},
    ]

    result = tag.classify_chunks(
        chunks,
        document_genre="lecture",
        corpus_role="core_thought",
        call_json=_llm({"chunks": [{"chunk_id": "A_001", "confidence": 0.9}]}),
    )

    assert tag.needs_review(result["A_002"], []) is True
    assert result["A_002"]["tag_confidence"] == 0.0


def test_classify_chunks_ignores_unknown_chunk_ids():
    """存在しない chunk_id を返されても他のチャンクへ混ぜない。"""
    chunks = [{"chunk_id": "A_001", "text": "本文", "chunk_type": "narration"}]

    result = tag.classify_chunks(
        chunks,
        document_genre="lecture",
        corpus_role="core_thought",
        call_json=_llm({"chunks": [
            {"chunk_id": "SOMEWHERE_ELSE", "speaker_role": "character", "confidence": 0.9},
            {"chunk_id": "A_001", "confidence": 0.9},
        ]}),
    )

    assert set(result) == {"A_001"}
    assert result["A_001"]["speaker_role"] == "author_direct"


def test_classify_chunks_survives_llm_failure():
    """LLMが落ちても取り込み済みのタグを壊さない(Pass1 の結果を残す)。"""
    chunks = [{"chunk_id": "A_001", "text": "本文", "chunk_type": "narration"}]

    def boom(**_kwargs):
        raise RuntimeError("LLM error")

    result = tag.classify_chunks(
        chunks, document_genre="lecture", corpus_role="core_thought", call_json=boom
    )

    assert result["A_001"]["speaker_role"] == "author_direct"
    assert tag.needs_review(result["A_001"], []) is True


def test_classify_chunks_batches_long_documents():
    """1回のプロンプトに詰め込みすぎない(打ち切りで全滅させない)。"""
    chunks = [
        {"chunk_id": f"A_{i:03d}", "text": "本文", "chunk_type": "narration"}
        for i in range(tag.PASS2_BATCH_SIZE * 2 + 1)
    ]
    calls = []

    def counting(**kwargs):
        calls.append(kwargs)
        return {"chunks": []}

    tag.classify_chunks(
        chunks, document_genre="lecture", corpus_role="core_thought", call_json=counting
    )

    assert len(calls) == 3


@pytest.mark.parametrize("genre", ["novel", "short_story", "sketch"])
def test_fiction_stays_excluded_across_batch(genre):
    """小説はバッチ経路でも思想の根拠にならない。"""
    chunks = [{"chunk_id": "A_001", "text": "こんな夢を見た。", "chunk_type": "narration"}]

    result = tag.classify_chunks(
        chunks,
        document_genre=genre,
        corpus_role="narrative_reference",
        call_json=_llm({"chunks": [
            {"chunk_id": "A_001", "speaker_role": "author_direct",
             "thought_eligibility": "candidate", "confidence": 0.99},
        ]}),
    )

    assert result["A_001"]["thought_eligibility"] == "excluded"
    assert result["A_001"]["speaker_role"] != "author_direct"
