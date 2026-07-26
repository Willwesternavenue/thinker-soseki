"""重蒸留(仕様6.6)。重要チャンク(importance=high)に限定してSonnetで実施。

抽出項目: 重要な区別 / 禁止すべき解釈 / 回答方針候補 /
代表引用候補(verbatim=trueのチャンクのみ)/ 思想カード更新候補 /
原典リンク候補(quote_allowed判断の材料を含む)
結果は chunk_distillations.heavy_json に格納する。
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from .. import config, db, llm

SYSTEM = """あなたは思想家の原典を深く分析する編集者である。
このチャンクは中核思想に関わる重要箇所と判定されている。
回答AIの「回答方針」を作るための材料を抽出する。
蒸留結果は編集的解釈であり、本人の発言そのものではない。JSONのみを出力する。"""

PROMPT_TEMPLATE = """以下は「{person_name}」の重要な原典チャンクである。

## 出典: {source_title}
## 軽蒸留の要約: {light_summary}
## verbatim(本人発言そのものか): {verbatim}

## テキスト
{text}

## 既知の思想ID
{known_thought_ids}

## 出力形式(JSONのみ)
{{
  "key_distinctions": [{{"not": "誤解されがちな解釈", "but": "正しい理解"}}],
  "prohibited_interpretations": ["禁止すべき解釈", "..."],
  "answer_policy_candidates": ["回答方針の候補", "..."],
  "quote_candidates": [
    {{"quote": "本文からの正確な抜粋(verbatim=trueの場合のみ。100字以内)", "reason": "代表引用にふさわしい理由"}}
  ],
  "card_update_candidates": [
    {{"thought_id": "対象思想ID", "suggestion": "カードへ反映すべき内容"}}
  ],
  "link_candidates": [
    {{"thought_id": "対象思想ID",
      "evidence_role": "definition|distinction|prohibition|example|application|style|biographical|quote|historical|metaphor",
      "strength": "high|medium|low",
      "quote_allowed_suggestion": true,
      "note": "quote_allowed判断の材料(文脈の切り取りリスク等)"}}
  ]
}}
verbatimがfalseの場合、quote_candidatesは必ず空配列にする。"""


def run(person_id: str = "merleau_ponty", source_id: str | None = None) -> int:
    """importance=high の未重蒸留チャンクを処理する。処理件数を返す。"""
    query = (
        db.client().table("chunk_distillations")
        .select("distillation_id, chunk_id, summary, importance, heavy_json")
        .eq("importance", "high")
        .is_("heavy_json", "null")
    )
    dists = query.execute().data
    if not dists:
        return 0

    chunk_ids = [d["chunk_id"] for d in dists]
    chunks_res = db.select_in(
        "source_chunks",
        "chunk_id, source_id, person_id, text, verbatim, status",
        "chunk_id",
        chunk_ids,
        person_id=person_id,
    )
    if source_id:
        chunks_res = [c for c in chunks_res if c["source_id"] == source_id]
    # 無効化(disabled)・置換済み(superseded)のチャンクは重蒸留しない。検索もカード生成も
    # active しか見ないため、処理してもLLM費用が無駄になるだけ。
    chunks_res = [c for c in chunks_res if c["status"] == "active"]
    chunks = {c["chunk_id"]: c for c in chunks_res}

    persona = (
        db.client().table("personas").select("display_name")
        .eq("person_id", person_id).single().execute().data
    )
    sources = {
        s["source_id"]: s["title"]
        for s in db.client().table("sources").select("source_id, title").execute().data
    }
    known = sorted({
        r["thought_id"]
        for r in db.client().table("thought_cards").select("thought_id")
        .eq("person_id", person_id).execute().data
    })
    known_text = "\n".join(f"- {t}" for t in known) or "(なし)"

    targets = [d for d in dists if d["chunk_id"] in chunks]

    def heavy_one(dist: dict) -> tuple[str, dict]:
        chunk = chunks[dist["chunk_id"]]
        result = llm.call_json(
            agent_name="heavy_distiller",
            model=config.MODEL_HEAVY_DISTILL,
            system=SYSTEM,
            prompt=PROMPT_TEMPLATE.format(
                person_name=persona["display_name"],
                source_title=sources.get(chunk["source_id"], chunk["source_id"]),
                light_summary=dist.get("summary") or "-",
                verbatim=chunk["verbatim"],
                text=chunk["text"],
                known_thought_ids=known_text,
            ),
            input_ref=dist["chunk_id"],
            max_tokens=3000,
        )
        if not chunk["verbatim"]:
            result["quote_candidates"] = []
        return dist["distillation_id"], result

    done = 0
    with ThreadPoolExecutor(max_workers=config.DISTILL_CONCURRENCY) as pool:
        futures = [pool.submit(heavy_one, d) for d in targets]
        for future in as_completed(futures):
            distillation_id, heavy = future.result()
            db.client().table("chunk_distillations").update(
                {"heavy_json": heavy, "status": "heavy"}
            ).eq("distillation_id", distillation_id).execute()
            done += 1
    return done
