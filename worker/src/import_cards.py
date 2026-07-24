"""既存の thought_questions.txt(カード内容つき質問集)を取り込む。

1ファイルから thought_cards(思想IDで重複排除)と thought_questions を復元する。
  uv run python -m src.import_cards            # ドライラン(解析結果を表示のみ)
  uv run python -m src.import_cards --commit   # 実際にDBへ投入(embedding付与)
"""

import sys
from collections import OrderedDict

from . import config, db
from .steps import embed as embed_step

SRC = config.REPO_ROOT / "data" / "thought_questions.txt"
PERSON_ID = "x_shigyo"
FALLBACK_THOUGHT_ID = "FALLBACK_LIFE_ADVICE_BASICS"

# ファイルの日本語 intent → スキーマの enum
INTENT_MAP = {
    "定義を聞く質問": "definition",
    "誤解を含む質問": "misunderstanding",
    "関連・比較を聞く質問": "comparison",
    "理由・根拠を聞く質問": "critical_question",
    "具体例・適用場面を聞く質問": "example_request",
    "日常相談": "daily_advice",
    "応用": "application",
    "関係": "relationship_question",
}
IMPORTANCE_MAP = {"high": "important", "medium": "normal", "low": "normal"}

HEADER_KEYS = {
    "id", "type", "question", "target_card_id", "thought_id",
    "title", "work", "intent", "priority",
}


def parse_records(text: str) -> list[dict]:
    records = []
    for block in text.split("=== THOUGHT_QUESTION ==="):
        block = block.strip()
        if not block:
            continue
        rec: dict = {}
        body: dict = {}
        in_text = False
        for line in block.splitlines():
            if not in_text:
                if line.strip() == "text:":
                    in_text = True
                    continue
                if ":" in line:
                    k, _, v = line.partition(":")
                    if k.strip() in HEADER_KEYS:
                        rec[k.strip()] = v.strip()
            else:
                if ":" in line:
                    k, _, v = line.partition(":")
                    body[k.strip()] = v.strip()
        rec["_body"] = body
        if rec.get("thought_id") and rec.get("question"):
            records.append(rec)
    return records


def _split_prohibitions(text: str) -> list[str]:
    return [p.strip() + "。" for p in text.split("。") if p.strip()]


def _split_policy(text: str) -> list[str]:
    return [p.strip() + "。" for p in text.split("。") if p.strip()]


def _parse_distinctions(text: str) -> list[dict]:
    if not text:
        return []
    parts = [p.strip() for p in text.split("／") if p.strip()]
    if len(parts) >= 2:
        return [{"not": parts[0], "but": parts[1]}]
    return [{"not": "", "but": text.strip()}]


def build(records: list[dict]) -> tuple[dict, list[dict]]:
    cards: "OrderedDict[str, dict]" = OrderedDict()
    questions: list[dict] = []

    for rec in records:
        tid = rec["thought_id"]
        b = rec["_body"]
        if tid == FALLBACK_THOUGHT_ID:
            continue  # 既存の汎用フォールバックカードは保持

        if tid not in cards:
            cards[tid] = {
                "card_id": rec.get("target_card_id") or f"card_{tid.lower()}",
                "person_id": PERSON_ID,
                "thought_id": tid,
                "title": rec.get("title") or b.get("対応思想") or tid,
                "importance": IMPORTANCE_MAP.get(rec.get("priority", ""), "normal"),
                "status": "approved",
                "core_claim": b.get("中核命題"),
                "distinctions": _parse_distinctions(b.get("重要な区別", "")),
                "answer_policy": _split_policy(b.get("回答の方向性", "")),
                "prohibitions": _split_prohibitions(b.get("禁止", "")),
                "related_thought_ids": [],
                "representative_chunk_ids": [],
                "search_text": " ".join(
                    filter(None, [rec.get("title"), b.get("中核命題"), b.get("回答の方向性")])
                ),
            }

        questions.append(
            {
                "question_id": rec.get("id") or f"q_{tid.lower()}_{len(questions)}",
                "person_id": PERSON_ID,
                "question": rec["question"],
                "target_thought_id": tid,
                "target_card_id": cards[tid]["card_id"],
                "intent": INTENT_MAP.get(rec.get("intent", ""), "definition"),
                "answer_direction": b.get("回答の方向性") or b.get("検索用要約"),
                "status": "active",
            }
        )
    return cards, questions


def main() -> None:
    commit = "--commit" in sys.argv
    records = parse_records(SRC.read_text(encoding="utf-8"))
    cards, questions = build(records)

    print(f"解析: {len(records)}レコード → カード{len(cards)}枚 / 質問{len(questions)}問")
    print("intent内訳:", end=" ")
    counts: dict = {}
    for q in questions:
        counts[q["intent"]] = counts.get(q["intent"], 0) + 1
    print(counts)
    sample = next(iter(cards.values()))
    print("\n--- サンプルカード ---")
    print("card_id:", sample["card_id"], "/ thought_id:", sample["thought_id"])
    print("title:", sample["title"], "/ importance:", sample["importance"])
    print("core_claim:", (sample["core_claim"] or "")[:80])
    print("distinctions:", sample["distinctions"])
    print("answer_policy(先頭2):", sample["answer_policy"][:2])
    print("prohibitions(先頭2):", sample["prohibitions"][:2])

    if not commit:
        print("\n[ドライラン] --commit で実際に投入します")
        return

    c = db.client()
    # 1. 衝突する既存approvedカード(自動生成等)を deprecated に
    imported_tids = list(cards.keys())
    imported_card_ids = {v["card_id"] for v in cards.values()}
    existing = (
        c.table("thought_cards").select("card_id, thought_id, status")
        .eq("person_id", PERSON_ID).eq("status", "approved").execute().data
    )
    deprecated_ids = []
    for e in existing:
        if e["thought_id"] in imported_tids and e["card_id"] not in imported_card_ids:
            deprecated_ids.append(e["card_id"])
    if deprecated_ids:
        db.update_in("thought_cards", {"status": "deprecated"}, "card_id", deprecated_ids)
        db.update_in("thought_questions", {"status": "inactive"}, "target_card_id", deprecated_ids)
        print(f"既存approvedカードを置き換え(deprecated): {deprecated_ids}")

    # 2. カードのembedding付与 → 投入
    card_list = list(cards.values())
    vecs = embed_step.embed_texts([c_["search_text"] or c_["title"] for c_ in card_list])
    for c_, v in zip(card_list, vecs):
        c_["embedding"] = v
    c.table("thought_cards").upsert(card_list).execute()
    print(f"カード投入: {len(card_list)}枚 (approved)")

    # 3. 質問のembedding付与 → 投入(バッチ)
    qvecs = embed_step.embed_texts([q["question"] for q in questions])
    for q, v in zip(questions, qvecs):
        q["embedding"] = v
    for i in range(0, len(questions), 200):
        c.table("thought_questions").upsert(questions[i : i + 200]).execute()
    print(f"質問投入: {len(questions)}問 (active)")
    print("完了")


if __name__ == "__main__":
    main()
