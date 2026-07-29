"""Bridge Rule の読み出しと創作モードへの注入(仕様§6 / 引き継ぎ B-1)。

思想チャンクを創作へそのまま渡すことは禁止されている（文言が登場人物の台詞に
なる）。**承認済みの Bridge Rule を介した場合だけ**、思想は「書き方の対応」として
創作の文脈に入る。橋が無ければ何も入らない — これがこのモジュールの唯一の役目。

承認の鎖は読み出し時にも検証する: 規則の版が approved でも、元の思想カード・
先の創作カードのどちらかが承認済みでなくなっていれば橋は架からない
（frontend の `composeBridges` と同じ規律。承認後にカード側だけ取り消された
場合に効く）。

⚠️ `frontend/src/lib/rag/bridges.ts` と**対**の実装。片方だけ直すと、チャットの
創作依頼と創作モードで注入される橋が食い違う。禁止文言は
`aozora.gen_rules.BRIDGE_DEFAULT_PROHIBITION` の複製で、同期テストで守る
（creative から aozora へは依存させない）。
"""

from . import repo

# 仕様§6 の禁止。LLM の出力・規則の内容に関わらず必ず添える
DEFAULT_BRIDGE_PROHIBITION = "思想の文言を登場人物の台詞としてそのまま言わせない"

# 生成設定 `rules` の値(仕様 CREATIVE_MODE_SPEC_v0.2 §L3)。
#   off    … 橋を読まない。思想は創作へ一切入らない
#   shadow … 架かる橋を trace に記録するが、プロンプトへは入れない(観察用)
#   assist … outline に注入し、trace にも記録する
RULES_MODES = ("off", "shadow", "assist")
DEFAULT_RULES_MODE = "off"


def rules_mode(profile: dict) -> str:
    """profile の生成設定から rules モードを読む。

    Guard の閾値と同じく profile を単一の出所にする(コードへ直書きしない。
    仕様§8.1)。未知の値は `off` に倒す — 設定の誤記で思想が黙って創作へ
    流れ込む方が、注入されない方より危険なため。
    """
    settings = profile.get("default_generation_settings") or {}
    mode = settings.get("rules") or DEFAULT_RULES_MODE
    return mode if mode in RULES_MODES else DEFAULT_RULES_MODE


def compose_bridges(
    *,
    rules: list[dict],
    versions: list[dict],
    thought_cards: list[dict],
    creative_cards: list[dict],
) -> list[dict]:
    """橋を組み立てる(純粋関数。DB取得は `fetch_bridges`)。

    揃わない橋は黙って落とす — 片側の承認が取り消された規則が残っていても、
    創作の文脈に思想が漏れないことを最優先にする。
    """
    thought_by_id = {
        t["thought_id"]: t for t in thought_cards if t.get("status") == "approved"
    }
    creative_by_id = {
        c["card_id"]: c for c in creative_cards if c.get("status") == "approved"
    }

    # 規則ごとに最新の承認版を採る
    latest: dict[str, dict] = {}
    for v in versions:
        if v.get("status") != "approved":
            continue
        held = latest.get(v["rule_id"])
        if held is None or v["version"] > held["version"]:
            latest[v["rule_id"]] = v

    composed: list[dict] = []
    for rule in rules:
        if rule.get("lifecycle") != "active":
            continue
        version = latest.get(rule["rule_id"])
        if not version:
            continue

        content = version.get("content") or {}
        thought = thought_by_id.get(content.get("source_thought_id"))
        creative = creative_by_id.get(content.get("target_creative_card_id"))
        if not thought or not creative:
            continue

        forbidden = list(content.get("forbidden_inferences") or [])
        if not any("台詞" in f for f in forbidden):
            forbidden.append(DEFAULT_BRIDGE_PROHIBITION)

        composed.append({
            "rule_id": rule["rule_id"],
            "title": rule["title"],
            "thought_id": thought["thought_id"],
            "thought_title": thought["title"],
            "thought_claim": thought.get("core_claim"),
            "technique_card_id": creative["card_id"],
            "technique_title": creative["title"],
            "technique_summary": creative.get("summary"),
            "rationale": content.get("rationale"),
            "forbidden": forbidden,
        })
    return composed


def fetch_bridges(person_id: str, *, client=None) -> list[dict]:
    """承認済みの Bridge Rule を読み出して組み立てる。"""
    c = client or repo.db.client()

    rules = (
        c.table("judgment_rules")
        .select("rule_id, title, lifecycle")
        .eq("person_id", person_id)
        .eq("rule_scope", "bridge_rule")
        .eq("lifecycle", "active")
        .execute()
        .data
        or []
    )
    if not rules:
        return []

    versions = (
        c.table("judgment_rule_versions")
        .select("rule_id, version, status, content")
        .in_("rule_id", [r["rule_id"] for r in rules])
        .eq("status", "approved")
        .execute()
        .data
        or []
    )
    if not versions:
        return []

    thought_ids = sorted({
        tid for v in versions
        if isinstance(tid := (v.get("content") or {}).get("source_thought_id"), str)
    })
    creative_ids = sorted({
        cid for v in versions
        if isinstance(cid := (v.get("content") or {}).get("target_creative_card_id"), str)
    })

    thought_cards = (
        c.table("thought_cards")
        .select("thought_id, title, core_claim, status")
        .eq("person_id", person_id)
        .in_("thought_id", thought_ids)
        .execute()
        .data
        or []
    ) if thought_ids else []
    creative_cards = (
        c.table("creative_cards")
        .select("card_id, card_type, title, summary, status")
        .in_("card_id", creative_ids)
        .execute()
        .data
        or []
    ) if creative_ids else []

    return compose_bridges(
        rules=rules,
        versions=versions,
        thought_cards=thought_cards,
        creative_cards=creative_cards,
    )


def render_bridge_section(bridges: list[dict]) -> str:
    """outline プロンプトに入れる節。橋が無ければ空(節ごと出さない)。"""
    if not bridges:
        return ""
    parts = []
    for b in bridges:
        lines = [f"### {b['title']}"]
        claim = f" — {b['thought_claim']}" if b.get("thought_claim") else ""
        lines.append(f"思想: {b['thought_title']}{claim}")
        summary = f" — {b['technique_summary']}" if b.get("technique_summary") else ""
        lines.append(f"書き方: {b['technique_title']}{summary}")
        if b.get("rationale"):
            lines.append(f"対応の理由: {b['rationale']}")
        if b.get("forbidden"):
            lines.append(
                "してはいけないこと:\n"
                + "\n".join(f"  - {f}" for f in b["forbidden"])
            )
        parts.append("\n".join(lines))
    return (
        "## 【思想と書き方の対応(承認済みの橋のみ)】\n"
        "思想はそのまま書かず、以下の対応が示す「書き方」としてだけ作品へ現す。\n\n"
        + "\n\n".join(parts)
    )
