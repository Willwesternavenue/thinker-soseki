"""L3 判断規則 と Bridge Rule の候補生成(C-T6 / 受入#13)。

判断規則は**承認済み思想カードから導く**。原典から直接作らないのは、
カードの承認という人手の関門を規則が迂回できてしまうため。

Bridge Rule は思想と創作を繋ぐ唯一の経路（仕様§6）。これが無い限り、
思想チャンクは創作依頼へ渡らない。
"""

import pytest

from src.aozora import gen_rules, tag


def _llm(payload):
    def call(**_kwargs):
        return payload
    return call


def _seed_corpus(client):
    client.table("personas").upsert(
        {"person_id": "natsume_soseki", "display_name": "X漱石"}
    ).execute()
    client.table("canonical_works").upsert({
        "canonical_work_id": "cw_R", "person_id": "natsume_soseki",
        "canonical_title": "R"}).execute()
    client.table("work_editions").upsert({
        "edition_id": "ed_R", "canonical_work_id": "cw_R",
        "aozora_work_id": "000000", "orthography": "新字新仮名"}).execute()
    client.table("sources").upsert({
        "source_id": "SRC_R", "person_id": "natsume_soseki", "title": "R",
        "source_type": "essay", "edition_id": "ed_R", "corpus_role": "core_thought",
        "document_genre": "lecture", "source_provider": "aozora"}).execute()
    for i in range(2):
        client.table("source_chunks").upsert({
            "chunk_id": f"SRC_R_{i:03d}", "source_id": "SRC_R",
            "person_id": "natsume_soseki", "text": f"開化は内発的である({i})。",
            "chunker_version": "aozora_v1", "chunk_hash": f"hR{i}",
            "speaker_role": "author_direct", "thought_eligibility": "candidate",
            "tagger_version": tag.TAGGER_VERSION,
        }).execute()


def _seed_thought_card(client, *, status="approved", thought_id="naihatsu_kaika"):
    client.table("thought_cards").upsert({
        "card_id": f"tc_{thought_id}", "person_id": "natsume_soseki",
        "thought_id": thought_id, "title": "開化は内発的でなければならない",
        "core_claim": "外から与えられた開化は本人のものにならない",
        "distinctions": [{"not": "外発的な模倣", "but": "内発的な展開"}],
        "representative_chunk_ids": ["SRC_R_000", "SRC_R_001"],
        "status": status,
    }).execute()
    return f"tc_{thought_id}"


def _seed_creative_card(client, *, status="approved"):
    client.table("creative_profiles").upsert({
        "profile_id": "cp_R", "person_id": "natsume_soseki", "name": "R",
        "slug": "cp-r", "orthography_policy": "新字新仮名",
        "disclosure_text": "AI創作です", "display_title_format": "{title}（AI創作）",
        "status": "active"}).execute()
    client.table("creative_cards").upsert({
        "card_id": "cc_R", "profile_id": "cp_R", "card_type": "narrative",
        "title": "説明せずに現象だけを示す",
        "evidence_chunk_ids": ["SRC_R_000"], "status": status}).execute()
    return "cc_R"


_ONE_RULE = {"rules": [{
    "rule_family_id": "kaika_naihatsu",
    "title": "外圧で始まった変化を成熟と呼ばない",
    "rule_type": "distinction",
    "action": {"between": ["内発的な展開", "外発的な模倣"], "criterion": "動機の出所"},
    "derived_claims": ["形の模倣は開化の完成を意味しない"],
    "required_distinctions": ["内発と外発"],
    "forbidden_inferences": ["西洋の様式を採った事実だけで近代化を評価する"],
    "source_thought_id": "naihatsu_kaika",
}]}


# ── 判断規則は承認済み思想カードからのみ導く ──


def test_does_not_derive_rules_from_unapproved_cards(clean_corpus, client):
    """draft のカードから規則を作らない(承認の関門を迂回させない)。"""
    _seed_corpus(client)
    _seed_thought_card(client, status="draft")

    result = gen_rules.generate_judgment_rules(client=client, call_json=_llm(_ONE_RULE))

    assert result["created"] == 0
    assert client.table("judgment_rules").select("rule_id").execute().data == []


def test_creates_draft_rule_from_approved_card(clean_corpus, client):
    _seed_corpus(client)
    _seed_thought_card(client)

    result = gen_rules.generate_judgment_rules(client=client, call_json=_llm(_ONE_RULE))

    assert result["created"] == 1
    rule = client.table("judgment_rules").select("*").execute().data[0]
    assert rule["rule_scope"] == "judgment"
    assert rule["rule_type"] == "distinction"
    assert rule["creation_method"] == "corpus_extraction"
    version = client.table("judgment_rule_versions").select("*").execute().data[0]
    # ⚠️ 必ず draft。承認は人間が行う
    assert version["status"] == "draft"
    assert version["content"]["derived_claims"]


def test_records_evidence_pointing_at_the_source_card(clean_corpus, client):
    """受入#13: 規則にも evidence と provenance を持たせる。"""
    _seed_corpus(client)
    card_id = _seed_thought_card(client)

    gen_rules.generate_judgment_rules(client=client, call_json=_llm(_ONE_RULE))

    evidence = client.table("judgment_rule_evidence").select("*").execute().data
    assert {e["card_id"] for e in evidence} == {card_id}
    # 原典チャンクまで辿れること
    assert {e["chunk_id"] for e in evidence if e["chunk_id"]} == {
        "SRC_R_000", "SRC_R_001"
    }
    assert all(e["origin_type"] == "corpus_inferred" for e in evidence)


def test_unknown_rule_type_is_skipped(clean_corpus, client):
    """スキーマに無い rule_type は作らない(DBエラーにせず候補を捨てる)。"""
    _seed_corpus(client)
    _seed_thought_card(client)
    payload = {"rules": [{**_ONE_RULE["rules"][0], "rule_type": "なにか"}]}

    result = gen_rules.generate_judgment_rules(client=client, call_json=_llm(payload))

    assert result["created"] == 0
    assert result["skipped_invalid"] == 1


def test_rule_referencing_unknown_thought_is_skipped(clean_corpus, client):
    _seed_corpus(client)
    _seed_thought_card(client)
    payload = {"rules": [{**_ONE_RULE["rules"][0], "source_thought_id": "存在しない"}]}

    assert gen_rules.generate_judgment_rules(
        client=client, call_json=_llm(payload))["created"] == 0


def test_is_idempotent(clean_corpus, client):
    _seed_corpus(client)
    _seed_thought_card(client)
    gen_rules.generate_judgment_rules(client=client, call_json=_llm(_ONE_RULE))

    again = gen_rules.generate_judgment_rules(client=client, call_json=_llm(_ONE_RULE))

    assert again["created"] == 0
    assert len(client.table("judgment_rules").select("rule_id").execute().data) == 1


# ── Bridge Rule ──


_ONE_BRIDGE = {"bridges": [{
    "title": "内発性の主張は、説明を避ける語り方として現れる",
    "source_thought_id": "naihatsu_kaika",
    "target_creative_card_id": "cc_R",
    "rationale": "内から起きたものは説明を要さない、という主張が語りの型に対応する",
    "forbidden_inferences": ["思想の文言を登場人物の台詞にそのまま言わせる"],
}]}


def test_bridge_requires_both_sides_approved(clean_corpus, client):
    """片側でも未承認なら橋を架けない。"""
    _seed_corpus(client)
    _seed_thought_card(client)
    _seed_creative_card(client, status="draft")

    result = gen_rules.generate_bridge_rules(client=client, call_json=_llm(_ONE_BRIDGE))

    assert result["created"] == 0


def test_creates_bridge_rule_with_both_evidences(clean_corpus, client):
    _seed_corpus(client)
    thought_card = _seed_thought_card(client)
    creative_card = _seed_creative_card(client)

    result = gen_rules.generate_bridge_rules(client=client, call_json=_llm(_ONE_BRIDGE))

    assert result["created"] == 1
    rule = client.table("judgment_rules").select("*").execute().data[0]
    assert rule["rule_scope"] == "bridge_rule"
    version = client.table("judgment_rule_versions").select("*").execute().data[0]
    assert version["status"] == "draft"
    assert version["content"]["target_creative_card_id"] == creative_card
    # 思想側・創作側の両方を evidence に残す(受入#13)
    evidence = client.table("judgment_rule_evidence").select("*").execute().data
    assert thought_card in {e["card_id"] for e in evidence}
    assert any(e["note"] and creative_card in e["note"] for e in evidence)


def test_bridge_rule_forbids_direct_quotation_of_thought(clean_corpus, client):
    """橋は「思想をそのまま台詞にする」ことを禁じる文言を必ず持つ(仕様§6)。"""
    _seed_corpus(client)
    _seed_thought_card(client)
    _seed_creative_card(client)
    payload = {"bridges": [{**_ONE_BRIDGE["bridges"][0], "forbidden_inferences": []}]}

    gen_rules.generate_bridge_rules(client=client, call_json=_llm(payload))

    version = client.table("judgment_rule_versions").select("*").execute().data[0]
    assert any(
        "台詞" in f for f in version["content"]["forbidden_inferences"]
    ), "禁止事項が空でも既定の禁止を必ず入れる"


def test_bridge_to_unknown_creative_card_is_skipped(clean_corpus, client):
    _seed_corpus(client)
    _seed_thought_card(client)
    _seed_creative_card(client)
    payload = {"bridges": [
        {**_ONE_BRIDGE["bridges"][0], "target_creative_card_id": "cc_なし"}
    ]}

    assert gen_rules.generate_bridge_rules(
        client=client, call_json=_llm(payload))["created"] == 0


# ── 承認 ──


def test_approve_rule_activates_the_latest_version(clean_corpus, client):
    _seed_corpus(client)
    _seed_thought_card(client)
    gen_rules.generate_judgment_rules(client=client, call_json=_llm(_ONE_RULE))
    rule_id = client.table("judgment_rules").select("rule_id").execute().data[0]["rule_id"]

    gen_rules.approve_rule(rule_id, reviewed_by="tester", client=client)

    version = client.table("judgment_rule_versions").select("*").eq(
        "rule_id", rule_id).single().execute().data
    assert version["status"] == "approved"


def test_approve_rule_refuses_when_source_card_is_no_longer_approved(
    clean_corpus, client
):
    """元の思想カードが取り消されていたら規則を承認できない。"""
    _seed_corpus(client)
    card_id = _seed_thought_card(client)
    gen_rules.generate_judgment_rules(client=client, call_json=_llm(_ONE_RULE))
    rule_id = client.table("judgment_rules").select("rule_id").execute().data[0]["rule_id"]
    client.table("thought_cards").update({"status": "draft"}).eq(
        "card_id", card_id).execute()

    with pytest.raises(ValueError, match="思想カード"):
        gen_rules.approve_rule(rule_id, reviewed_by="tester", client=client)
