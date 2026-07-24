"""書籍・動画単位蒸留(仕様6.7)。チャンク蒸留結果を統合し原典単位で生成する。"""

import json

from .. import config, db, llm
from . import embed as embed_step

SYSTEM = """あなたは思想家の原典群を整理する編集者である。
1つの原典(書籍・動画)のチャンク蒸留結果を統合し、原典単位の蒸留情報を作る。
蒸留結果は編集的解釈であり、本人の発言そのものではない。JSONのみを出力する。"""

PROMPT_TEMPLATE = """原典「{title}」({source_type})のチャンク蒸留結果一覧:

{chunk_summaries}

## 出力形式(JSONのみ)
{{
  "core_summary": "この原典の位置づけ(150字程度)",
  "main_themes": ["中心テーマ", "..."],
  "strong_thought_ids": ["この原典が強く支える思想ID", "..."],
  "unique_contributions": ["この原典独自の論点", "..."],
  "overlapping_points": ["他原典と重複しそうな論点", "..."],
  "misreading_risks": ["誤読リスク", "..."],
  "best_used_for": ["検索時の使いどころ", "..."],
  "not_best_for": ["使うべきでない用途", "..."],
  "representative_chunk_ids": ["代表チャンクID(与えられたIDから選ぶ)", "..."]
}}"""


def run(source_id: str) -> str:
    """指定原典の source_distillations を生成・更新する。"""
    source = (
        db.client().table("sources").select("*").eq("source_id", source_id)
        .single().execute().data
    )
    chunks = (
        db.client().table("source_chunks")
        .select("chunk_id")
        .eq("source_id", source_id).eq("status", "active")
        .execute()
    ).data
    chunk_ids = [c["chunk_id"] for c in chunks]
    dists = db.select_in(
        "chunk_distillations",
        "chunk_id, summary, claims, candidate_thought_ids, importance",
        "chunk_id",
        chunk_ids,
    )
    if not dists:
        raise RuntimeError(f"{source_id}: チャンク蒸留がまだありません(軽蒸留を先に実行)")

    lines = [
        f"- {d['chunk_id']} [{d['importance']}] {d['summary']} "
        f"(思想候補: {', '.join(d['candidate_thought_ids']) or 'なし'})"
        for d in dists
    ]
    result = llm.call_json(
        agent_name="source_distiller",
        model=config.MODEL_HEAVY_DISTILL,
        system=SYSTEM,
        prompt=PROMPT_TEMPLATE.format(
            title=source["title"],
            source_type=source["source_type"],
            chunk_summaries="\n".join(lines),
        ),
        input_ref=source_id,
        max_tokens=3000,
    )

    row = {
        "distilled_id": f"DISTILL_{source_id}",
        "source_id": source_id,
        "core_summary": result.get("core_summary"),
        "main_themes": result.get("main_themes", []),
        "strong_thought_ids": result.get("strong_thought_ids", []),
        "unique_contributions": result.get("unique_contributions", []),
        "best_used_for": result.get("best_used_for", []),
        "not_best_for": result.get("not_best_for", []),
        "representative_chunk_ids": [
            cid for cid in result.get("representative_chunk_ids", [])
            if cid in set(chunk_ids)
        ],
        "status": "draft",
    }
    # 検索ガイドとして補助的に使うためembeddingも付与(仕様6.4)
    search_text = json.dumps(
        {k: row[k] for k in ("core_summary", "main_themes", "best_used_for")},
        ensure_ascii=False,
    )
    row["embedding"] = embed_step.embed_texts([search_text])[0]
    db.client().table("source_distillations").upsert(row).execute()
    return row["distilled_id"]
