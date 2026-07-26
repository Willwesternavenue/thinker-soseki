"""軽蒸留(仕様6.5)。全チャンクにHaikuで実施。並列5〜10(仕様13.5)。

抽出項目: summary / keywords / claims / candidate_thought_ids /
related_concepts / evidence_roles / misreading_risks / importance
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from .. import config, db, llm

SYSTEM = """あなたは思想家の原典テキストを分析するアナリストである。
与えられたテキストチャンクから、指定されたJSON形式で情報を抽出する。
蒸留結果は編集的解釈であり、本人の発言そのものではないことに留意し、
本文にない主張を創作しない。JSONのみを出力する。"""

PROMPT_TEMPLATE = """以下は「{person_name}」の原典の一部である。

## 原典情報
- 出典: {source_title}({source_type})
- 章: {chapter_title}

## テキスト
{text}

## 既知の思想ID候補(該当があれば candidate_thought_ids に使う。無理に当てはめない)
{known_thought_ids}

## 出力形式(JSONのみ)
{{
  "summary": "このチャンクの要約(100字程度)",
  "keywords": ["重要語", "..."],
  "claims": ["本文が主張していること(本文に忠実に)", "..."],
  "candidate_thought_ids": ["該当しうる思想ID(SCREAMING_SNAKE_CASE、新規候補も可)"],
  "related_concepts": ["関連概念", "..."],
  "evidence_roles": ["definition|distinction|prohibition|example|application|style|biographical|quote|historical|metaphor のいずれか"],
  "misreading_risks": ["誤読されやすいポイント", "..."],
  "importance": "high|normal|low"
}}"""


def distill_chunks(
    chunks: list[dict],
    *,
    person_name: str,
    source_title: str,
    source_type: str,
    known_thought_ids: list[str],
    job_id: str | None = None,
) -> list[dict]:
    """チャンク群を並列で軽蒸留し、chunk_distillations行のリストを返す。"""
    known = "\n".join(f"- {t}" for t in known_thought_ids) or "(なし)"

    def distill_one(chunk: dict) -> dict:
        result = llm.call_json(
            agent_name="chunk_distiller",
            model=config.MODEL_LIGHT_DISTILL,
            system=SYSTEM,
            prompt=PROMPT_TEMPLATE.format(
                person_name=person_name,
                source_title=source_title,
                source_type=source_type,
                chapter_title=chunk.get("chapter_title") or "-",
                text=chunk["text"],
                known_thought_ids=known,
            ),
            input_ref=chunk["chunk_id"],
            job_id=job_id,
        )
        return {
            "distillation_id": f"DIST_{chunk['chunk_id']}",
            "chunk_id": chunk["chunk_id"],
            "summary": result.get("summary"),
            "keywords": result.get("keywords", []),
            "claims": result.get("claims", []),
            "candidate_thought_ids": result.get("candidate_thought_ids", []),
            "related_concepts": result.get("related_concepts", []),
            "evidence_roles": result.get("evidence_roles", []),
            "misreading_risks": result.get("misreading_risks", []),
            "importance": result.get("importance", "normal"),
            "status": "draft",
        }

    results: list[dict] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=config.DISTILL_CONCURRENCY) as pool:
        futures = {pool.submit(distill_one, c): c["chunk_id"] for c in chunks}
        for future in as_completed(futures):
            chunk_id = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # 個別失敗はジョブ全体を止めず集約
                errors.append(f"{chunk_id}: {exc}")

    # 失敗があっても成功分は先に保存する。main.py は chunk_distillations に行が
    # 無いチャンクだけを再蒸留するので、保存しておけば再実行は失敗分だけで済む。
    # (従来は raise で results が返らず upsert に到達せず、成功分のLLM出力が
    #  丸ごと捨てられていた。529等の一時エラーのたびに全チャンクやり直しになる)
    upsert_distillations(results)

    if errors:
        raise RuntimeError(
            f"軽蒸留で {len(errors)}/{len(chunks)} 件失敗"
            f"(成功{len(results)}件は保存済み。再実行で失敗分のみ処理されます): "
            + "; ".join(errors[:5])
        )
    return results


def upsert_distillations(rows: list[dict]) -> None:
    if rows:
        db.client().table("chunk_distillations").upsert(rows).execute()
