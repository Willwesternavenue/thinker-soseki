"""Pass2 の適用と Pass4 レビューキュー(C-T4b)。実DBで検証する。

Pass2 は取り込みとは別のステップにしてある(embed と同じ)。取り込みは LLM 無しで
再実行できるようにしておきたいのと、分類だけをやり直せるようにするため。
"""

from src.aozora import retag, tag


def _llm(items):
    def call(**_kwargs):
        return {"chunks": items}
    return call


def _seed(client, *, genre="lecture", corpus_role="core_thought", chunks=2):
    client.table("personas").upsert(
        {"person_id": "natsume_soseki", "display_name": "X漱石"}
    ).execute()
    client.table("canonical_works").upsert({
        "canonical_work_id": "cw_T", "person_id": "natsume_soseki",
        "canonical_title": "テスト作品"}).execute()
    client.table("work_editions").upsert({
        "edition_id": "ed_T", "canonical_work_id": "cw_T",
        "aozora_work_id": "000000", "orthography": "新字新仮名"}).execute()
    client.table("sources").upsert({
        "source_id": "SRC_T", "person_id": "natsume_soseki", "title": "テスト作品",
        "source_type": "essay", "edition_id": "ed_T", "corpus_role": corpus_role,
        "document_genre": genre, "source_provider": "aozora"}).execute()
    for i in range(chunks):
        pass1 = tag.deterministic_chunk_tags(
            {"text": f"本文{i}", "chunk_type": "narration"}, document_genre=genre
        )
        client.table("source_chunks").upsert({
            "chunk_id": f"SRC_T_{i:03d}", "source_id": "SRC_T",
            "person_id": "natsume_soseki", "text": f"本文{i}",
            "chunker_version": "aozora_v1", "chunk_hash": f"h{i}",
            "speaker_role": pass1["speaker_role"],
            "thought_eligibility": pass1["thought_eligibility"],
            "creative_eligibility": pass1["creative_eligibility"],
            # 旧版のタグが付いた状態(Pass2 未適用)を作る
            "tagger_version": "aozora_tag_v1",
            "tag_review_status": "auto_ok",
        }).execute()


def _chunk(client, chunk_id):
    return (
        client.table("source_chunks").select("*").eq("chunk_id", chunk_id)
        .single().execute().data
    )


def test_applies_pass2_and_records_tagger_version(clean_corpus, client):
    _seed(client)

    result = retag.retag_pending(
        client=client,
        call_json=_llm([
            {"chunk_id": "SRC_T_000", "claim_type": "normative_claim",
             "assertion_status": "asserted", "confidence": 0.9,
             "reason": "主張が明確"},
            {"chunk_id": "SRC_T_001", "claim_type": "descriptive_observation",
             "confidence": 0.9, "reason": "観察の記述"},
        ]),
    )

    assert result["updated"] == 2
    row = _chunk(client, "SRC_T_000")
    assert row["claim_type"] == "normative_claim"
    assert row["tagger_version"] == tag.TAGGER_VERSION
    assert row["tag_confidence"] == 0.9
    assert row["tag_review_status"] == "auto_ok"


def test_skips_chunks_already_tagged_by_this_version(clean_corpus, client):
    _seed(client)
    retag.retag_pending(client=client, call_json=_llm([
        {"chunk_id": "SRC_T_000", "confidence": 0.9},
        {"chunk_id": "SRC_T_001", "confidence": 0.9},
    ]))

    again = retag.retag_pending(client=client, call_json=_llm([]))

    assert again["updated"] == 0


def test_low_confidence_goes_to_review_queue(clean_corpus, client):
    _seed(client, chunks=1)

    retag.retag_pending(client=client, call_json=_llm([
        {"chunk_id": "SRC_T_000", "confidence": 0.3, "reason": "判断がつかない"},
    ]))

    row = _chunk(client, "SRC_T_000")
    assert row["tag_review_status"] == "needs_review"
    assert retag.review_queue(client=client)[0]["chunk_id"] == "SRC_T_000"


def test_consistency_violation_goes_to_review_queue(clean_corpus, client):
    """引用なのに主たる根拠のまま → 整合性違反として拾う(Pass3)。"""
    _seed(client, chunks=1)

    retag.retag_pending(client=client, call_json=_llm([
        {"chunk_id": "SRC_T_000", "is_quotation": True, "confidence": 0.95},
    ]))

    row = _chunk(client, "SRC_T_000")
    assert row["tag_review_status"] == "needs_review"
    assert "引用" in row["classification_reason"]


def test_llm_calling_a_lecture_speaker_a_character_is_corrected_not_queued(
    clean_corpus, client
):
    """カテゴリ誤りは直して通す。直した事実は残す。

    実データで LLM が講演者を character と分類する傾向が出た。レビューへ回すと
    キューが埋まって本当に見るべきものが埋もれる（実測 40件中26件）。
    """
    _seed(client, chunks=1)

    retag.retag_pending(client=client, call_json=_llm([
        {"chunk_id": "SRC_T_000", "speaker_role": "character", "confidence": 0.95},
    ]))

    row = _chunk(client, "SRC_T_000")
    assert row["speaker_role"] == "author_direct"
    assert row["tag_review_status"] == "auto_ok"
    assert "character" in row["classification_reason"]


def test_fiction_is_never_promoted_by_retag(clean_corpus, client):
    """実DB経由でも小説が思想の根拠にならない。"""
    _seed(client, genre="short_story", corpus_role="narrative_reference", chunks=1)

    retag.retag_pending(client=client, call_json=_llm([
        {"chunk_id": "SRC_T_000", "speaker_role": "author_direct",
         "thought_eligibility": "candidate", "confidence": 0.99},
    ]))

    row = _chunk(client, "SRC_T_000")
    assert row["thought_eligibility"] == "excluded"
    assert row["speaker_role"] != "author_direct"


def test_review_queue_only_lists_chunks_needing_review(clean_corpus, client):
    _seed(client, chunks=2)
    retag.retag_pending(client=client, call_json=_llm([
        {"chunk_id": "SRC_T_000", "confidence": 0.95},
        {"chunk_id": "SRC_T_001", "confidence": 0.2},
    ]))

    queue = retag.review_queue(client=client)

    assert [q["chunk_id"] for q in queue] == ["SRC_T_001"]


def test_resolve_review_records_who_decided(clean_corpus, client):
    _seed(client, chunks=1)
    retag.retag_pending(client=client, call_json=_llm([
        {"chunk_id": "SRC_T_000", "confidence": 0.2},
    ]))

    retag.resolve_review("SRC_T_000", reviewed_by="tester", client=client)

    row = _chunk(client, "SRC_T_000")
    assert row["tag_review_status"] == "reviewed"
    assert row["tag_reviewed_by"] == "tester"
    assert row["tag_reviewed_at"] is not None
    assert retag.review_queue(client=client) == []


def test_correcting_a_tag_marks_it_corrected(clean_corpus, client):
    """人が値を直した場合は reviewed ではなく corrected として残す。"""
    _seed(client, chunks=1)
    retag.retag_pending(client=client, call_json=_llm([
        {"chunk_id": "SRC_T_000", "speaker_role": "character", "confidence": 0.95},
    ]))

    retag.resolve_review(
        "SRC_T_000", reviewed_by="tester",
        corrections={"speaker_role": "author_direct"}, client=client,
    )

    row = _chunk(client, "SRC_T_000")
    assert row["tag_review_status"] == "corrected"
    assert row["speaker_role"] == "author_direct"


def test_correction_cannot_promote_fiction(clean_corpus, client):
    """人手の修正でも小説を作者の直接発言にはできない。"""
    _seed(client, genre="short_story", corpus_role="narrative_reference", chunks=1)
    retag.retag_pending(client=client, call_json=_llm([
        {"chunk_id": "SRC_T_000", "confidence": 0.2},
    ]))

    result = retag.resolve_review(
        "SRC_T_000", reviewed_by="tester",
        corrections={"speaker_role": "author_direct"}, client=client,
    )

    assert result["error"]
    assert _chunk(client, "SRC_T_000")["speaker_role"] != "author_direct"
