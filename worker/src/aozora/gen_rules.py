"""L3 判断規則 と Bridge Rule の候補生成(C-T6)。

正本仕様: docs/CORPUS_T1_SPEC.md §6・§14 / 受入#13。

⚠️ 規則は**承認済みの思想カードから導く**。原典から直接作らないのは、
カードの承認という人手の関門を規則が迂回できてしまうため。
生成物は必ず draft。承認は人間が行う(指示書§9 Pass4)。

Bridge Rule は思想と創作を繋ぐ**唯一の経路**（仕様§6）。これが無い限り、
思想チャンクは創作依頼へ渡らない（`frontend/src/lib/rag/corpus-routing.ts` の
`retrievalFiltersFor` が bridge 段を検索から外している）。
"""

import hashlib

from .. import config, db, llm

PERSON_ID = "natsume_soseki"

PROMPT_VERSION = "v1"

# judgment_rules.rule_type の許容値(既存スキーマの check 制約)
RULE_TYPES = frozenset({
    "value_transformation", "distinction", "priority", "contradiction_hold",
    "boundary", "exception", "prohibition", "question_rule", "abstention",
    "temporal_override",
})

# Bridge Rule に必ず入れる禁止事項。仕様§6 の禁止（思想チャンクを登場人物の
# 台詞へそのまま注入しない）を、LLM の出力に関わらず必ず持たせる
BRIDGE_DEFAULT_PROHIBITION = "思想の文言を登場人物の台詞としてそのまま言わせない"

_JUDGMENT_SYSTEM = """あなたは思想家の判断の型を規則として書き出す編集者である。
規則は主張の言い換えではなく、**判断の操作**である（何と何を区別するか、
何を何より優先するか、何を禁じるか）。JSONのみを出力する。"""

_JUDGMENT_PROMPT = """以下は承認済みの思想カードである。ここから判断規則の候補を作れ。

## 思想カード
{cards}

## rule_type
distinction(区別) / priority(優先) / value_transformation(捉え直し) /
prohibition(禁止) / boundary(適用範囲) / exception(例外) /
contradiction_hold(両立させたまま保持) / question_rule(問い返し) /
abstention(留保) / temporal_override(時代による上書き)

## 指示
- 1つの規則に1つの操作
- `source_thought_id` は必ず上のカードの thought_id から選ぶ
- カードに書かれていない判断を足さない

## 出力形式(JSONのみ)
{{
  "rules": [
    {{
      "rule_family_id": "英小文字スネークケース",
      "title": "規則の名前",
      "rule_type": "上記のいずれか",
      "action": {{"between": ["A", "B"], "criterion": "区別の基準"}},
      "derived_claims": ["この規則から導ける主張"],
      "required_distinctions": ["必ず区別すべきもの"],
      "forbidden_inferences": ["導いてはいけない結論"],
      "source_thought_id": "…"
    }}
  ]
}}"""

_BRIDGE_SYSTEM = """あなたは思想と作風の対応を書き出す編集者である。
思想がどのような**書き方**として作品に現れるかを規則にする。
思想の文言をそのまま台詞にすることは対応ではない。JSONのみを出力する。"""

_BRIDGE_PROMPT = """承認済みの思想カードと創作カードの対応を作れ。

## 思想カード
{thought_cards}

## 創作カード
{creative_cards}

## 指示
- 対応が読み取れるものだけを挙げる。無理に全部を結ばない
- `target_creative_card_id` は上の創作カードのIDから選ぶ
- 思想を台詞にする対応は作らない。あくまで**書き方**の対応

## 出力形式(JSONのみ)
{{
  "bridges": [
    {{
      "title": "対応の名前",
      "source_thought_id": "…",
      "target_creative_card_id": "…",
      "rationale": "なぜ対応すると言えるか",
      "forbidden_inferences": ["この対応から導いてはいけないこと"]
    }}
  ]
}}"""


def _c(client):
    return client if client is not None else db.client()


def _rule_id(prefix: str, family_id: str) -> str:
    digest = hashlib.sha256(f"{prefix}:{family_id}".encode()).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _approved_thought_cards(person_id: str, *, client) -> list[dict]:
    return (
        _c(client).table("thought_cards")
        .select("card_id, thought_id, title, core_claim, distinctions,"
                " answer_policy, prohibitions, representative_chunk_ids")
        .eq("person_id", person_id).eq("status", "approved")
        .order("thought_id").execute().data or []
    )


def _approved_creative_cards(*, client) -> list[dict]:
    return (
        _c(client).table("creative_cards")
        .select("card_id, card_type, title, summary")
        .eq("status", "approved").order("card_id").execute().data or []
    )


def _format_thought_cards(cards: list[dict]) -> str:
    return "\n\n".join(
        f"[{c['thought_id']}] {c['title']}\n  中核: {c.get('core_claim') or ''}\n"
        f"  区別: {c.get('distinctions')}"
        for c in cards
    )


def _format_creative_cards(cards: list[dict]) -> str:
    return "\n".join(
        f"[{c['card_id']}] ({c['card_type']}) {c['title']} — {c.get('summary') or ''}"
        for c in cards
    )


def _write_rule(
    c, *, rule_id: str, person_id: str, family_id: str, title: str,
    rule_scope: str, rule_type: str, rationale: str | None, content: dict,
) -> None:
    c.table("judgment_rules").upsert({
        "rule_id": rule_id,
        "person_id": person_id,
        "rule_family_id": family_id,
        "title": title,
        "rule_scope": rule_scope,
        "rule_type": rule_type,
        "creation_method": "corpus_extraction",
        "creation_rationale": rationale,
        "lifecycle": "active",
    }).execute()
    c.table("judgment_rule_versions").upsert({
        "rule_id": rule_id,
        "version": 1,
        # ⚠️ 必ず draft。承認は人間が行う
        "status": "draft",
        "content": content,
    }, on_conflict="rule_id,version").execute()


def generate_judgment_rules(
    person_id: str = PERSON_ID, *, client=None, call_json=None, job_id: str | None = None
) -> dict:
    """承認済み思想カードから L3 判断規則の候補を作る。"""
    c = _c(client)
    call = call_json or llm.call_json

    cards = _approved_thought_cards(person_id, client=c)
    if not cards:
        return {"created": 0, "skipped_existing": 0, "skipped_invalid": 0}
    by_thought = {card["thought_id"]: card for card in cards}

    existing = {
        r["rule_family_id"]
        for r in c.table("judgment_rules").select("rule_family_id")
        .eq("person_id", person_id).execute().data or []
    }

    response = call(
        agent_name="aozora_gen_judgment_rules",
        model=config.MODEL_CARD_DRAFT,
        system=_JUDGMENT_SYSTEM,
        prompt=_JUDGMENT_PROMPT.format(cards=_format_thought_cards(cards)),
        input_ref=person_id,
        job_id=job_id,
        max_tokens=8192,
    )

    created = skipped_existing = skipped_invalid = 0
    for rule in (response or {}).get("rules") or []:
        family_id = (rule.get("rule_family_id") or "").strip()
        title = (rule.get("title") or "").strip()
        rule_type = rule.get("rule_type")
        source = by_thought.get(rule.get("source_thought_id"))

        if not family_id or not title:
            continue
        if family_id in existing:
            skipped_existing += 1
            continue
        # スキーマに無い型・存在しないカード参照は候補ごと捨てる
        if rule_type not in RULE_TYPES or source is None:
            skipped_invalid += 1
            continue

        rule_id = _rule_id("jr", family_id)
        _write_rule(
            c, rule_id=rule_id, person_id=person_id, family_id=family_id, title=title,
            rule_scope="judgment", rule_type=rule_type,
            rationale=f"思想カード {source['thought_id']} から導出",
            content={
                "action": rule.get("action") or {},
                "derived_claims": rule.get("derived_claims") or [],
                "required_distinctions": rule.get("required_distinctions") or [],
                "forbidden_inferences": rule.get("forbidden_inferences") or [],
                "source_thought_id": source["thought_id"],
                "prompt_version": PROMPT_VERSION,
            },
        )
        _write_evidence(c, rule_id, source)
        existing.add(family_id)
        created += 1

    return {
        "created": created,
        "skipped_existing": skipped_existing,
        "skipped_invalid": skipped_invalid,
    }


def _write_evidence(c, rule_id: str, card: dict, *, note: str | None = None) -> None:
    """受入#13: 規則の根拠を、カードと原典チャンクの両方で残す。"""
    c.table("judgment_rule_evidence").upsert({
        "evidence_id": _evidence_id(rule_id, card["card_id"], None),
        "rule_id": rule_id,
        "card_id": card["card_id"],
        "evidence_role": "supports",
        "origin_type": "corpus_inferred",
        "note": note,
    }).execute()
    for chunk_id in card.get("representative_chunk_ids") or []:
        c.table("judgment_rule_evidence").upsert({
            "evidence_id": _evidence_id(rule_id, card["card_id"], chunk_id),
            "rule_id": rule_id,
            "card_id": card["card_id"],
            "chunk_id": chunk_id,
            "evidence_role": "supports",
            "origin_type": "corpus_inferred",
            "note": note,
        }).execute()


def _evidence_id(rule_id: str, card_id: str, chunk_id: str | None) -> str:
    """再実行しても同じ行を指すよう決定的なUUIDにする。"""
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{rule_id}|{card_id}|{chunk_id or ''}"))


def generate_bridge_rules(
    person_id: str = PERSON_ID, *, client=None, call_json=None, job_id: str | None = None
) -> dict:
    """思想カードと創作カードを結ぶ Bridge Rule の候補を作る。

    **両方が承認済みのときだけ**橋を架ける。片側が未承認のまま結ぶと、
    人手の関門を通っていないものが創作へ流れ込む経路になる。
    """
    c = _c(client)
    call = call_json or llm.call_json

    thought_cards = _approved_thought_cards(person_id, client=c)
    creative_cards = _approved_creative_cards(client=c)
    if not thought_cards or not creative_cards:
        return {"created": 0, "skipped_existing": 0, "skipped_invalid": 0}

    by_thought = {card["thought_id"]: card for card in thought_cards}
    creative_ids = {card["card_id"] for card in creative_cards}

    existing = {
        r["rule_family_id"]
        for r in c.table("judgment_rules").select("rule_family_id")
        .eq("person_id", person_id).eq("rule_scope", "bridge_rule").execute().data or []
    }

    response = call(
        agent_name="aozora_gen_bridge_rules",
        model=config.MODEL_CARD_DRAFT,
        system=_BRIDGE_SYSTEM,
        prompt=_BRIDGE_PROMPT.format(
            thought_cards=_format_thought_cards(thought_cards),
            creative_cards=_format_creative_cards(creative_cards),
        ),
        input_ref=person_id,
        job_id=job_id,
        max_tokens=8192,
    )

    created = skipped_existing = skipped_invalid = 0
    for bridge in (response or {}).get("bridges") or []:
        title = (bridge.get("title") or "").strip()
        source = by_thought.get(bridge.get("source_thought_id"))
        target = bridge.get("target_creative_card_id")

        if not title:
            continue
        if source is None or target not in creative_ids:
            skipped_invalid += 1
            continue

        family_id = f"bridge_{source['thought_id']}_{target}"
        if family_id in existing:
            skipped_existing += 1
            continue

        # 仕様§6 の禁止は LLM の出力に関わらず必ず入れる
        forbidden = list(bridge.get("forbidden_inferences") or [])
        if not any("台詞" in f for f in forbidden):
            forbidden.append(BRIDGE_DEFAULT_PROHIBITION)

        rule_id = _rule_id("br", family_id)
        _write_rule(
            c, rule_id=rule_id, person_id=person_id, family_id=family_id, title=title,
            rule_scope="bridge_rule", rule_type="boundary",
            rationale=bridge.get("rationale"),
            content={
                "source_thought_id": source["thought_id"],
                "target_creative_card_id": target,
                "rationale": bridge.get("rationale"),
                "forbidden_inferences": forbidden,
                "prompt_version": PROMPT_VERSION,
            },
        )
        _write_evidence(c, rule_id, source, note=f"創作カード {target} と対応")
        existing.add(family_id)
        created += 1

    return {
        "created": created,
        "skipped_existing": skipped_existing,
        "skipped_invalid": skipped_invalid,
    }


def approve_rule(rule_id: str, *, reviewed_by: str, client=None) -> dict:
    """規則を承認する。

    元の思想カードが承認済みのままかを確かめてから通す。カードが取り消されたのに
    規則だけ生き残ると、承認を取り消した判断が回答に効かなくなる。
    """
    c = _c(client)
    versions = (
        c.table("judgment_rule_versions").select("*")
        .eq("rule_id", rule_id).order("version", desc=True).limit(1)
        .execute().data or []
    )
    if not versions:
        raise ValueError(f"規則が見つかりません(rule_id={rule_id})")
    version = versions[0]

    thought_id = (version["content"] or {}).get("source_thought_id")
    if thought_id:
        rows = (
            c.table("thought_cards").select("status")
            .eq("thought_id", thought_id).execute().data or []
        )
        if not rows or rows[0]["status"] != "approved":
            raise ValueError(
                f"元の思想カード({thought_id})が承認済みではないため承認できません"
            )

    c.table("judgment_rule_versions").update({
        "status": "approved", "created_by": reviewed_by,
    }).eq("rule_version_id", version["rule_version_id"]).execute()
    return {"rule_id": rule_id, "status": "approved"}
