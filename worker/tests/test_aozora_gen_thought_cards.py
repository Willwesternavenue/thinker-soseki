"""思想カード候補の生成(C-T6 / 仕様 docs/CORPUS_T1_SPEC.md §11・受入#13)。

最重要は「**小説から思想カードを作らない**」こと。作れてしまうと、
作中人物の言葉が本人の思想として回答に出る経路ができる。

受入#13(L2/L3 の全項目に evidence と provenance がある)のため、
カードには根拠チャンクと `thought_evidence_links` を必ず伴わせる。
"""

import pytest

from src.aozora import gen_thought_cards, tag


def _llm(payload):
    def call(**_kwargs):
        return payload
    return call


def _seed(
    client, *, source_id="SRC_L", corpus_role="core_thought", genre="lecture",
    speaker_role="author_direct", eligibility="candidate", chunks=3,
):
    client.table("personas").upsert(
        {"person_id": "natsume_soseki", "display_name": "X漱石"}
    ).execute()
    client.table("canonical_works").upsert({
        "canonical_work_id": f"cw_{source_id}", "person_id": "natsume_soseki",
        "canonical_title": source_id}).execute()
    client.table("work_editions").upsert({
        "edition_id": f"ed_{source_id}", "canonical_work_id": f"cw_{source_id}",
        "aozora_work_id": "000000", "orthography": "新字新仮名"}).execute()
    client.table("sources").upsert({
        "source_id": source_id, "person_id": "natsume_soseki", "title": source_id,
        "source_type": "essay", "edition_id": f"ed_{source_id}",
        "corpus_role": corpus_role, "document_genre": genre,
        "source_provider": "aozora"}).execute()
    ids = []
    for i in range(chunks):
        cid = f"{source_id}_{i:03d}"
        client.table("source_chunks").upsert({
            "chunk_id": cid, "source_id": source_id, "person_id": "natsume_soseki",
            "text": f"開化は内発的でなければならない({i})。",
            "chunker_version": "aozora_v1", "chunk_hash": f"h{cid}",
            "speaker_role": speaker_role, "thought_eligibility": eligibility,
            "tagger_version": tag.TAGGER_VERSION,
        }).execute()
        ids.append(cid)
    return ids


_ONE_CARD = {"cards": [{
    "thought_id": "naihatsu_kaika",
    "title": "開化は内発的でなければならない",
    "core_claim": "外から与えられた開化は本人のものにならない",
    "distinctions": [{"not": "外発的な模倣", "but": "内発的な展開"}],
    "answer_policy": ["外圧で始まった変化を成熟と混同しない"],
    "prohibitions": ["外来の様式を採り入れた事実だけで近代化を評価しない"],
    "evidence_chunk_ids": ["SRC_L_000", "SRC_L_001"],
}]}


# ── 最重要: 小説から思想カードを作らない ──


def test_does_not_use_fiction_chunks(clean_corpus, client):
    """小説しか無ければカードを1枚も作らない。"""
    _seed(client, source_id="SRC_N", corpus_role="narrative_reference",
          genre="short_story", speaker_role="narrator", eligibility="excluded")

    result = gen_thought_cards.generate(client=client, call_json=_llm(_ONE_CARD))

    assert result["created"] == 0
    assert client.table("thought_cards").select("card_id").execute().data == []


def test_does_not_use_character_speech_even_in_non_fiction(clean_corpus, client):
    """登場人物・語り手のチャンクは、文書種別に関わらず渡さない。"""
    _seed(client, speaker_role="character")

    result = gen_thought_cards.generate(client=client, call_json=_llm(_ONE_CARD))

    assert result["created"] == 0


def test_does_not_use_excluded_chunks(clean_corpus, client):
    _seed(client, eligibility="excluded")

    assert gen_thought_cards.generate(
        client=client, call_json=_llm(_ONE_CARD))["created"] == 0


def test_llm_cannot_cite_a_chunk_outside_the_thought_index(clean_corpus, client):
    """思想Index外のチャンクIDを根拠に挙げてきたら捨てる。"""
    _seed(client)
    _seed(client, source_id="SRC_N", corpus_role="narrative_reference",
          genre="short_story", speaker_role="narrator", eligibility="excluded")

    payload = {"cards": [{
        **_ONE_CARD["cards"][0],
        "evidence_chunk_ids": ["SRC_L_000", "SRC_N_000", "SRC_N_001"],
    }]}
    gen_thought_cards.generate(client=client, call_json=_llm(payload))

    cards = client.table("thought_cards").select("*").execute().data
    # 有効な根拠が1件しか残らない → 最低件数に満たずカードを作らない
    assert cards == []


# ── 生成されるカードの形 ──


def test_creates_draft_card_with_evidence(clean_corpus, client):
    _seed(client)

    result = gen_thought_cards.generate(client=client, call_json=_llm(_ONE_CARD))

    assert result["created"] == 1
    card = client.table("thought_cards").select("*").execute().data[0]
    # ⚠️ 必ず draft。LLMの出力を自動でapprovedにしない
    assert card["status"] == "draft"
    assert card["thought_id"] == "naihatsu_kaika"
    assert card["core_claim"]
    assert set(card["representative_chunk_ids"]) == {"SRC_L_000", "SRC_L_001"}


def test_creates_evidence_links_for_provenance(clean_corpus, client):
    """受入#13: 根拠を links にも残す(どの原典のどこか を辿れるように)。"""
    _seed(client)

    gen_thought_cards.generate(client=client, call_json=_llm(_ONE_CARD))

    links = client.table("thought_evidence_links").select("*").execute().data
    assert {link["chunk_id"] for link in links} == {"SRC_L_000", "SRC_L_001"}
    assert all(link["source_id"] == "SRC_L" for link in links)
    assert all(link["thought_id"] == "naihatsu_kaika" for link in links)
    # links も未承認から始まる
    assert all(link["status"] == "draft" for link in links)


def test_evidence_role_comes_from_the_llm_when_valid(clean_corpus, client):
    """根拠の役割(定義か区別か例か)を残す。検索の多様性制御が使う。"""
    _seed(client)
    payload = {"cards": [{
        **_ONE_CARD["cards"][0],
        "evidence": [
            {"chunk_id": "SRC_L_000", "evidence_role": "definition"},
            {"chunk_id": "SRC_L_001", "evidence_role": "distinction"},
        ],
    }]}

    gen_thought_cards.generate(client=client, call_json=_llm(payload))

    links = client.table("thought_evidence_links").select("*").execute().data
    assert {link["chunk_id"]: link["evidence_role"] for link in links} == {
        "SRC_L_000": "definition", "SRC_L_001": "distinction",
    }


def test_unknown_evidence_role_falls_back(clean_corpus, client):
    """制約に無い役割を返されても落とさず既定へ倒す。"""
    _seed(client)
    payload = {"cards": [{
        **_ONE_CARD["cards"][0],
        "evidence": [
            {"chunk_id": "SRC_L_000", "evidence_role": "support"},
            {"chunk_id": "SRC_L_001", "evidence_role": "なにか"},
        ],
    }]}

    gen_thought_cards.generate(client=client, call_json=_llm(payload))

    links = client.table("thought_evidence_links").select("evidence_role").execute().data
    assert all(link["evidence_role"] == gen_thought_cards.DEFAULT_EVIDENCE_ROLE
               for link in links)


def test_skips_cards_without_enough_evidence(clean_corpus, client):
    _seed(client)
    payload = {"cards": [{**_ONE_CARD["cards"][0], "evidence_chunk_ids": ["SRC_L_000"]}]}

    result = gen_thought_cards.generate(client=client, call_json=_llm(payload))

    assert result["created"] == 0
    assert result["skipped_no_evidence"] == 1


def test_generates_from_every_source_not_just_the_first(clean_corpus, client):
    """資料ごとに分けて渡す。

    全チャンクを1プロンプトに詰めると文字数上限で先頭の資料しか入らず、
    実データでは9資料中1つの冒頭からしかカードが出なかった。
    """
    _seed(client, source_id="SRC_A")
    _seed(client, source_id="SRC_B")
    seen_sources = []

    def call(**kwargs):
        prompt = kwargs["prompt"]
        source = "SRC_A" if "SRC_A_000" in prompt else "SRC_B"
        seen_sources.append(source)
        return {"cards": [{
            **_ONE_CARD["cards"][0],
            "thought_id": f"t_{source}",
            "evidence_chunk_ids": [f"{source}_000", f"{source}_001"],
        }]}

    result = gen_thought_cards.generate(client=client, call_json=call)

    assert sorted(seen_sources) == ["SRC_A", "SRC_B"], "資料ごとに1回ずつ呼ぶ"
    assert result["created"] == 2
    cards = client.table("thought_cards").select("representative_chunk_ids").execute().data
    assert {c["representative_chunk_ids"][0][:5] for c in cards} == {"SRC_A", "SRC_B"}


def test_is_idempotent(clean_corpus, client):
    """再実行しても同じカードを二重に作らない。"""
    _seed(client)
    gen_thought_cards.generate(client=client, call_json=_llm(_ONE_CARD))

    again = gen_thought_cards.generate(client=client, call_json=_llm(_ONE_CARD))

    assert again["created"] == 0
    assert again["skipped_existing"] == 1
    assert len(client.table("thought_cards").select("card_id").execute().data) == 1


def test_does_not_recreate_rejected_cards(clean_corpus, client):
    _seed(client)
    gen_thought_cards.generate(client=client, call_json=_llm(_ONE_CARD))
    card_id = client.table("thought_cards").select("card_id").execute().data[0]["card_id"]
    client.table("thought_cards").update({"status": "rejected"}).eq(
        "card_id", card_id).execute()

    again = gen_thought_cards.generate(client=client, call_json=_llm(_ONE_CARD))

    assert again["created"] == 0


# ── 承認 ──


def test_approve_requires_existing_evidence(clean_corpus, client):
    """根拠チャンクが消えていたら承認できない(創作カードと同じ規律)。"""
    _seed(client)
    gen_thought_cards.generate(client=client, call_json=_llm(_ONE_CARD))
    card_id = client.table("thought_cards").select("card_id").execute().data[0]["card_id"]
    client.table("source_chunks").delete().eq("chunk_id", "SRC_L_000").execute()

    with pytest.raises(ValueError, match="根拠"):
        gen_thought_cards.approve_card(card_id, reviewed_by="tester", client=client)


def test_approve_activates_card_and_links(clean_corpus, client):
    """承認するとカードと links の両方が使える状態になる。"""
    _seed(client)
    gen_thought_cards.generate(client=client, call_json=_llm(_ONE_CARD))
    card_id = client.table("thought_cards").select("card_id").execute().data[0]["card_id"]

    gen_thought_cards.approve_card(card_id, reviewed_by="tester", client=client)

    card = client.table("thought_cards").select("*").eq(
        "card_id", card_id).single().execute().data
    assert card["status"] == "approved"
    links = client.table("thought_evidence_links").select("status").execute().data
    assert all(link["status"] == "approved" for link in links), "承認済みlinksだけがRAGで引かれる"


def test_approve_refuses_evidence_outside_thought_index(clean_corpus, client):
    """承認時にも「小説が根拠になっていないか」を見る。"""
    _seed(client)
    gen_thought_cards.generate(client=client, call_json=_llm(_ONE_CARD))
    card_id = client.table("thought_cards").select("card_id").execute().data[0]["card_id"]
    # 承認前に根拠チャンクが小説側へ付け替えられた状況
    client.table("source_chunks").update({"speaker_role": "character"}).eq(
        "chunk_id", "SRC_L_000").execute()

    with pytest.raises(ValueError, match="作者"):
        gen_thought_cards.approve_card(card_id, reviewed_by="tester", client=client)
