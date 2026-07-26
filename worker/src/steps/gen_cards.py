"""思想カード候補生成(仕様6.8)。複数原典を横断してSonnetで生成する。

- AI生成直後は status='draft'。人間レビュー後にapproved化(仕様6.10)
- 同時に原典リンク候補(thought_evidence_links, status='draft')も生成する
- 既存カード(rejected以外)がある思想IDはスキップ(更新は管理画面で人間が行う)
"""

from collections import defaultdict

from .. import config, db, llm
from . import embed as embed_step

SYSTEM = """あなたは思想家の思想体系を整理する編集者である。
複数の原典から集めた蒸留結果をもとに、回答AIの「回答方針」となる思想カードを作る。
カードの内容は回答方針であり、本人の発言そのものではない。JSONのみを出力する。"""

PROMPT_TEMPLATE = """思想ID「{thought_id}」の思想カードを作成する。人物: {person_name}

## この思想に関係するチャンク蒸留結果
{evidence}

## 重蒸留からの材料(区別・禁止解釈・回答方針候補)
{heavy_material}

## 既存の思想ID一覧(related_thought_idsに使える)
{known_thought_ids}

## 出力形式(JSONのみ)
{{
  "title": "カードタイトル(短い日本語)",
  "importance": "core|important|normal",
  "core_claim": "中核命題(この思想の本質を1〜3文で)",
  "distinctions": [{{"not": "よくある誤解", "but": "正しい理解"}}],
  "answer_policy": ["回答時の方針", "..."],
  "prohibitions": ["回答で禁止すべきこと", "..."],
  "related_thought_ids": ["関連思想ID", "..."],
  "representative_chunk_ids": ["代表チャンクID(与えられたIDから3件以内)"]
}}"""

MIN_EVIDENCE_CHUNKS = 2  # これ未満の思想ID候補はカード化しない


def run(person_id: str = "natsume_soseki") -> list[str]:
    """カード候補を生成し、作成した card_id のリストを返す。"""
    c = db.client()
    persona = (
        c.table("personas").select("display_name")
        .eq("person_id", person_id).single().execute().data
    )

    # チャンク蒸留から思想ID候補ごとの根拠チャンクを集める
    chunks = {
        r["chunk_id"]: r
        for r in c.table("source_chunks")
        .select("chunk_id, source_id, verbatim")
        .eq("person_id", person_id).eq("status", "active").execute().data
    }
    dists = db.select_in(
        "chunk_distillations",
        "chunk_id, summary, claims, candidate_thought_ids, evidence_roles, "
        "misreading_risks, importance, heavy_json",
        "chunk_id",
        list(chunks.keys()),
    )

    by_thought: dict[str, list[dict]] = defaultdict(list)
    for d in dists:
        for tid in d.get("candidate_thought_ids") or []:
            by_thought[tid].append(d)

    existing = {
        r["thought_id"]
        for r in c.table("thought_cards").select("thought_id, status")
        .eq("person_id", person_id).neq("status", "rejected").execute().data
    }
    known_text = "\n".join(f"- {t}" for t in sorted(set(by_thought) | existing)) or "(なし)"

    created: list[str] = []
    for thought_id, evidence in sorted(by_thought.items()):
        if thought_id in existing or len(evidence) < MIN_EVIDENCE_CHUNKS:
            continue

        evidence_lines = [
            f"- {d['chunk_id']} [{d['importance']}] {d['summary']}\n"
            f"  主張: {'; '.join(d['claims'] or [])}\n"
            f"  誤読リスク: {'; '.join(d['misreading_risks'] or [])}"
            for d in evidence
        ]
        heavy_lines = []
        for d in evidence:
            h = d.get("heavy_json") or {}
            for item in h.get("key_distinctions", []):
                heavy_lines.append(f"- 区別: {item.get('not')} ではなく {item.get('but')}")
            for item in h.get("prohibited_interpretations", []):
                heavy_lines.append(f"- 禁止解釈: {item}")
            for item in h.get("answer_policy_candidates", []):
                heavy_lines.append(f"- 方針候補: {item}")

        result = llm.call_json(
            agent_name="card_draft_generator",
            model=config.MODEL_CARD_DRAFT,
            system=SYSTEM,
            prompt=PROMPT_TEMPLATE.format(
                thought_id=thought_id,
                person_name=persona["display_name"],
                evidence="\n".join(evidence_lines),
                heavy_material="\n".join(heavy_lines) or "(なし)",
                known_thought_ids=known_text,
            ),
            input_ref=thought_id,
            max_tokens=3000,
        )

        card_id = f"card_{thought_id.lower()}"
        valid_chunk_ids = {d["chunk_id"] for d in evidence}
        rep_ids = [
            cid for cid in result.get("representative_chunk_ids", [])
            if cid in valid_chunk_ids
        ][:3]
        search_text = " ".join(
            [result.get("title", ""), result.get("core_claim", "")]
            + result.get("answer_policy", [])
        )
        c.table("thought_cards").upsert(
            {
                "card_id": card_id,
                "person_id": person_id,
                "thought_id": thought_id,
                "title": result.get("title", thought_id),
                "importance": result.get("importance", "normal"),
                "status": "draft",
                "core_claim": result.get("core_claim"),
                "distinctions": result.get("distinctions", []),
                "answer_policy": result.get("answer_policy", []),
                "prohibitions": result.get("prohibitions", []),
                "related_thought_ids": result.get("related_thought_ids", []),
                "representative_chunk_ids": rep_ids,
                "search_text": search_text,
                "embedding": embed_step.embed_texts([search_text])[0],
            }
        ).execute()
        created.append(card_id)

        # 原典リンク候補(draft)。重蒸留のlink_candidatesを優先、なければ蒸留結果から生成
        links = []
        for d in evidence:
            h = d.get("heavy_json") or {}
            chunk = chunks[d["chunk_id"]]
            heavy_links = [
                lc for lc in h.get("link_candidates", [])
                if lc.get("thought_id") == thought_id
            ]
            if heavy_links:
                for lc in heavy_links:
                    links.append(_link_row(person_id, thought_id, chunk, lc))
            else:
                roles = d.get("evidence_roles") or ["example"]
                links.append(
                    _link_row(
                        person_id, thought_id, chunk,
                        {"evidence_role": roles[0], "strength": "medium",
                         "quote_allowed_suggestion": False,
                         "note": "軽蒸留からの自動候補"},
                    )
                )
        if links:
            c.table("thought_evidence_links").upsert(
                links, ignore_duplicates=True
            ).execute()

    return created


VALID_ROLES = {
    "definition", "distinction", "prohibition", "example", "application",
    "style", "biographical", "quote", "historical", "metaphor",
}


def _link_row(person_id: str, thought_id: str, chunk: dict, candidate: dict) -> dict:
    role = candidate.get("evidence_role", "example")
    if role not in VALID_ROLES:
        role = "example"
    # quote_allowed はAIの提案値。verbatim=falseなら物理的に引用不可なので必ずfalse
    quote_suggested = bool(candidate.get("quote_allowed_suggestion")) and chunk["verbatim"]
    return {
        "link_id": f"LINK_{thought_id}_{chunk['chunk_id']}",
        "person_id": person_id,
        "thought_id": thought_id,
        "source_id": chunk["source_id"],
        "chunk_id": chunk["chunk_id"],
        "evidence_role": role,
        "strength": candidate.get("strength", "medium"),
        "quote_allowed": quote_suggested,
        "note": candidate.get("note"),
        "status": "draft",
    }
