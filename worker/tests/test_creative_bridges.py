"""創作モードへの Bridge Rule 注入(引き継ぎ B-1 / 仕様§6)。

最重要は「**承認済みの橋以外から思想が創作へ入らない**」こと。思想の文言が
そのまま登場人物の台詞になる経路を塞ぐため、橋は読み出し時にも鎖を検証する。

frontend/src/lib/rag/bridges.ts と対の実装。片方だけ直すと、チャットと
創作モードで注入される橋が食い違う。
"""

import pytest

from src.aozora import gen_rules
from src.creative import bridges


def _seed_bridge(
    client,
    *,
    rule_id="br_test01",
    person_id="natsume_soseki",
    profile_id,
    thought_status="approved",
    creative_status="approved",
    version_status="approved",
    lifecycle="active",
    forbidden=None,
    version=1,
):
    """1本の橋（思想カード → 規則 → 創作カード）を作る。"""
    thought_id = f"th_{rule_id}"
    card_id = f"cc_{rule_id}"
    client.table("personas").upsert(
        {"person_id": person_id, "display_name": "X漱石"}
    ).execute()
    client.table("thought_cards").upsert({
        "card_id": f"tc_{rule_id}", "person_id": person_id, "thought_id": thought_id,
        "title": "物我合着", "core_claim": "物と我の区別は便宜にすぎない",
        "status": thought_status,
    }).execute()
    client.table("creative_cards").upsert({
        "card_id": card_id, "profile_id": profile_id, "card_type": "setting",
        "title": "夢の宣言", "summary": "「こんな夢を見た」で始める",
        "status": creative_status,
    }).execute()
    client.table("judgment_rules").upsert({
        "rule_id": rule_id, "person_id": person_id, "rule_family_id": rule_id,
        "title": "物我の区別が溶ける場としての夢の宣言",
        "rule_scope": "bridge_rule", "rule_type": "boundary",
        "lifecycle": lifecycle, "creation_method": "corpus_extraction",
    }).execute()
    client.table("judgment_rule_versions").upsert({
        "rule_id": rule_id, "version": version, "status": version_status,
        "content": {
            "source_thought_id": thought_id,
            "target_creative_card_id": card_id,
            "rationale": "夢の枠が物我の境を溶かす",
            "forbidden_inferences": forbidden if forbidden is not None else [],
        },
    }).execute()
    return rule_id


# ── 承認の鎖（読み出し時の再検証） ──


def test_returns_approved_bridge(clean_corpus, client, profile):
    _seed_bridge(client, profile_id=profile)

    got = bridges.fetch_bridges("natsume_soseki", client=client)

    assert [b["rule_id"] for b in got] == ["br_test01"]
    assert got[0]["thought_title"] == "物我合着"
    assert got[0]["technique_title"] == "夢の宣言"


def test_drops_bridge_when_thought_card_is_not_approved(clean_corpus, client, profile):
    """規則が承認済みでも、元の思想カードが未承認なら橋は架からない。"""
    _seed_bridge(client, profile_id=profile, thought_status="draft")

    assert bridges.fetch_bridges("natsume_soseki", client=client) == []


def test_drops_bridge_when_creative_card_is_not_approved(clean_corpus, client, profile):
    _seed_bridge(client, profile_id=profile, creative_status="draft")

    assert bridges.fetch_bridges("natsume_soseki", client=client) == []


def test_drops_bridge_when_version_is_draft(clean_corpus, client, profile):
    """⚠️ LLM が作った draft の規則を創作へ流さない。"""
    _seed_bridge(client, profile_id=profile, version_status="draft")

    assert bridges.fetch_bridges("natsume_soseki", client=client) == []


def test_drops_bridge_when_rule_is_retired(clean_corpus, client, profile):
    _seed_bridge(client, profile_id=profile, lifecycle="deprecated")

    assert bridges.fetch_bridges("natsume_soseki", client=client) == []


def test_uses_the_latest_approved_version(clean_corpus, client, profile):
    _seed_bridge(client, profile_id=profile, version=1)
    client.table("judgment_rule_versions").upsert({
        "rule_id": "br_test01", "version": 2, "status": "approved",
        "content": {
            "source_thought_id": "th_br_test01",
            "target_creative_card_id": "cc_br_test01",
            "rationale": "新しい理由",
            "forbidden_inferences": [],
        },
    }).execute()

    got = bridges.fetch_bridges("natsume_soseki", client=client)

    assert [b["rationale"] for b in got] == ["新しい理由"]


# ── 禁止事項 ──


def test_always_attaches_the_speech_prohibition(clean_corpus, client, profile):
    """LLM が禁止事項を書かなくても、台詞化の禁止は必ず付く(仕様§6)。"""
    _seed_bridge(client, profile_id=profile, forbidden=[])

    got = bridges.fetch_bridges("natsume_soseki", client=client)

    assert bridges.DEFAULT_BRIDGE_PROHIBITION in got[0]["forbidden"]


def test_keeps_the_llm_prohibition_when_it_covers_speech(clean_corpus, client, profile):
    _seed_bridge(client, profile_id=profile,
                 forbidden=["思想を台詞でそのまま説明させない"])

    got = bridges.fetch_bridges("natsume_soseki", client=client)

    assert got[0]["forbidden"] == ["思想を台詞でそのまま説明させない"]


def test_prohibition_matches_the_rule_generator(clean_corpus, client, profile):
    """gen_rules と文言がずれたら落とす（複製の同期）。"""
    assert bridges.DEFAULT_BRIDGE_PROHIBITION == gen_rules.BRIDGE_DEFAULT_PROHIBITION


# ── プロンプト節 ──


def test_renders_nothing_without_bridges():
    """橋が無ければ節ごと出さない（空の見出しを残さない）。"""
    assert bridges.render_bridge_section([]) == ""


def test_rendered_section_states_the_prohibition(clean_corpus, client, profile):
    _seed_bridge(client, profile_id=profile)

    section = bridges.render_bridge_section(
        bridges.fetch_bridges("natsume_soseki", client=client)
    )

    assert "物我合着" in section
    assert "夢の宣言" in section
    assert bridges.DEFAULT_BRIDGE_PROHIBITION in section


# ── モード ──


@pytest.mark.parametrize(
    "settings,expected",
    [
        ({}, "off"),
        ({"rules": "off"}, "off"),
        ({"rules": "shadow"}, "shadow"),
        ({"rules": "assist"}, "assist"),
        ({"rules": "なにか"}, "off"),  # 未知の値は安全側（注入しない）
    ],
)
def test_rules_mode(settings, expected):
    profile = {"default_generation_settings": settings}
    assert bridges.rules_mode(profile) == expected
