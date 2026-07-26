"""創作カード候補の生成テスト(C-T6 / 仕様 docs/CORPUS_T1_SPEC.md §11)。

- 創作論由来(creative_grammar)と小説本文由来(narrative_reference)を
  evidence_type で区別する(指示書§11.2)
- LLM分類だけで approved にしない。必ず draft で作る(指示書§9 Pass4)
- evidence が最低件数に満たなければカード化しない(既存 gen_cards の規律)
"""

import pytest

from src.aozora import gen_creative_cards as gen
from src.creative import repo


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0) if self.responses else {"cards": []}


def _seed_profile(client, profile_id="cp_yume"):
    client.table("personas").upsert(
        {"person_id": "natsume_soseki", "display_name": "X漱石"}
    ).execute()
    client.table("creative_profiles").upsert({
        "profile_id": profile_id, "person_id": "natsume_soseki",
        "name": "夢十夜", "slug": profile_id, "orthography_policy": "新字新仮名",
        "disclosure_text": "AIが生成した創作物です。",
        "display_title_format": "{title}（AI創作）", "status": "active",
    }).execute()
    return profile_id


def _seed_source(client, source_id, *, corpus_role, genre, chunks):
    client.table("canonical_works").upsert({
        "canonical_work_id": f"cw_{source_id}", "person_id": "natsume_soseki",
        "canonical_title": source_id,
    }).execute()
    client.table("work_editions").upsert({
        "edition_id": f"ed_{source_id}", "canonical_work_id": f"cw_{source_id}",
        "aozora_work_id": "000000", "orthography": "新字新仮名",
    }).execute()
    client.table("sources").upsert({
        "source_id": source_id, "person_id": "natsume_soseki", "title": source_id,
        "source_type": "essay", "edition_id": f"ed_{source_id}",
        "corpus_role": corpus_role, "document_genre": genre,
        "source_provider": "aozora",
    }).execute()
    for i, (text, eligibility) in enumerate(chunks):
        client.table("source_chunks").upsert({
            "chunk_id": f"{source_id}_{i:03d}", "source_id": source_id,
            "person_id": "natsume_soseki", "text": text,
            "chunker_version": "aozora_v1", "chunk_hash": f"h{source_id}{i}",
            "creative_eligibility": eligibility,
            "speaker_role": "author_direct" if corpus_role == "creative_grammar"
            else "narrator",
        }).execute()


def test_generates_cards_from_creative_grammar_as_author_theory(clean_corpus, client):
    """創作論由来のカードは evidence_type=author_creative_theory になる。"""
    profile = _seed_profile(client)
    _seed_source(client, "SRC_SHASEI", corpus_role="creative_grammar",
                 genre="literary_theory",
                 chunks=[("写生文家は事物を観察する。", "support"),
                         ("写生文家の態度は距離を保つ。", "support")])
    llm = FakeLLM({"cards": [{
        "card_type": "style", "title": "説明より観察を優先する",
        "summary": "対象を説明せず観察として書く",
        "positive_patterns": ["事物を淡々と描写する"],
        "evidence_chunk_ids": ["SRC_SHASEI_000", "SRC_SHASEI_001"],
    }]})

    result = gen.generate_for_profile(profile, client=client, call_json=llm)

    assert result["created"] == 1
    card = client.table("creative_cards").select("*").execute().data[0]
    assert card["evidence_type"] == "author_creative_theory"
    assert card["status"] == "draft", "LLM生成物を自動でapprovedにしない"
    assert card["origin_type"] == "distilled"
    assert sorted(card["evidence_chunk_ids"]) == ["SRC_SHASEI_000", "SRC_SHASEI_001"]


def test_generates_cards_from_fiction_as_demonstrated(clean_corpus, client):
    """小説本文由来のカードは evidence_type=demonstrated_in_fiction になる。"""
    profile = _seed_profile(client)
    _seed_source(client, "SRC_YUME", corpus_role="narrative_reference",
                 genre="short_story",
                 chunks=[("こんな夢を見た。", "candidate"),
                         ("自分は驚かなかった。", "candidate")])
    # corpus_role ごとに呼ばれる。creative_grammar は資料が無いので呼ばれず、
    # narrative_reference の1回だけ応答が使われる
    llm = FakeLLM({"cards": [{
        "card_type": "narrative", "title": "異常を自然な事実として扱う",
        "summary": "夢の内部では異常も当然のこととして受け入れる",
        "evidence_chunk_ids": ["SRC_YUME_000", "SRC_YUME_001"],
    }]})

    result = gen.generate_for_profile(profile, client=client, call_json=llm)

    assert result["created"] == 1
    card = client.table("creative_cards").select("*").execute().data[0]
    assert card["evidence_type"] == "demonstrated_in_fiction"


def test_skips_card_without_enough_evidence(clean_corpus, client):
    """根拠チャンクが最低件数に満たないカードは作らない(既存の規律)。"""
    profile = _seed_profile(client)
    _seed_source(client, "SRC_X", corpus_role="creative_grammar", genre="criticism",
                 chunks=[("批評は複数の軸で行う。", "support"),
                         ("一軸に還元しない。", "support")])
    llm = FakeLLM({"cards": [{
        "card_type": "criticism", "title": "評価軸を一軸に還元しない",
        "evidence_chunk_ids": ["SRC_X_000"],  # 1件しかない
    }]})

    result = gen.generate_for_profile(profile, client=client, call_json=llm)

    assert result["created"] == 0
    assert result["skipped_no_evidence"] == 1
    assert client.table("creative_cards").select("card_id").execute().data == []


def test_rejects_evidence_chunk_ids_not_in_corpus(clean_corpus, client):
    """LLMが実在しないchunk_idを返しても採用しない(evidence span整合性)。"""
    profile = _seed_profile(client)
    _seed_source(client, "SRC_X", corpus_role="creative_grammar", genre="criticism",
                 chunks=[("本文A。", "support"), ("本文B。", "support")])
    llm = FakeLLM({"cards": [{
        "card_type": "style", "title": "架空の根拠を持つカード",
        "evidence_chunk_ids": ["SRC_X_000", "SRC_NOT_EXIST_999"],
    }]})

    result = gen.generate_for_profile(profile, client=client, call_json=llm)

    # 実在する根拠が1件しか残らないため、最低件数を満たさずカード化されない
    assert result["created"] == 0
    assert result["skipped_no_evidence"] == 1


def test_excludes_chunks_marked_as_not_usable(clean_corpus, client):
    """creative_eligibility=excluded のチャンクはプロンプトへ渡さない。"""
    profile = _seed_profile(client)
    _seed_source(client, "SRC_X", corpus_role="creative_grammar", genre="criticism",
                 chunks=[("使ってよい本文。", "support"),
                         ("使ってはいけない本文。", "excluded")])
    llm = FakeLLM({"cards": []})

    gen.generate_for_profile(profile, client=client, call_json=llm)

    prompt = llm.calls[0]["prompt"]
    assert "使ってよい本文。" in prompt
    assert "使ってはいけない本文。" not in prompt


def test_is_idempotent_and_skips_existing_cards(clean_corpus, client):
    """既存の未rejectedカードと同じ観点は作り直さない。"""
    profile = _seed_profile(client)
    _seed_source(client, "SRC_X", corpus_role="creative_grammar", genre="criticism",
                 chunks=[("本文A。", "support"), ("本文B。", "support")])
    card = {"card_type": "style", "title": "説明より観察を優先する",
            "evidence_chunk_ids": ["SRC_X_000", "SRC_X_001"]}

    first = gen.generate_for_profile(
        profile, client=client, call_json=FakeLLM({"cards": [card]})
    )
    second = gen.generate_for_profile(
        profile, client=client, call_json=FakeLLM({"cards": [card]})
    )

    assert first["created"] == 1
    assert second["created"] == 0
    assert second["skipped_existing"] == 1
    assert len(client.table("creative_cards").select("card_id").execute().data) == 1


def test_uses_main_model_for_card_drafting(clean_corpus, client):
    profile = _seed_profile(client)
    _seed_source(client, "SRC_X", corpus_role="creative_grammar", genre="criticism",
                 chunks=[("本文A。", "support"), ("本文B。", "support")])
    llm = FakeLLM({"cards": []})

    gen.generate_for_profile(profile, client=client, call_json=llm)

    assert llm.calls[0]["model"] == gen.config.MODEL_CREATIVE_MAIN


def test_requires_active_profile(clean_corpus, client):
    _seed_profile(client)
    client.table("creative_profiles").update({"status": "archived"}).eq(
        "profile_id", "cp_yume").execute()

    with pytest.raises(repo.CreativeInvariantError, match="利用可能な状態ではありません"):
        gen.generate_for_profile("cp_yume", client=client, call_json=FakeLLM())
