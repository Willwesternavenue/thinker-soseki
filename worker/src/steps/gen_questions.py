"""質問対応情報生成(仕様6.9)。各思想カードに対し想定質問を生成する。

最低限: 定義 / 誤解訂正 / 比較 / 応用 / 日常相談 / 批判的 / 具体例要求
生成直後は status='draft'。カードapproved時に active 化する(6.11)。
"""

from .. import config, db, llm
from . import embed as embed_step

SYSTEM = """あなたは思想家へのユーザー質問を想定する編集者である。
思想カードに対して、実際のユーザーが投げそうな質問と回答方向を作る。JSONのみを出力する。"""

PROMPT_TEMPLATE = """思想カード「{title}」(thought_id: {thought_id})への想定質問を作成する。

## カード内容
中核命題: {core_claim}
区別: {distinctions}
回答方針: {answer_policy}
禁止事項: {prohibitions}

## 質問タイプ(それぞれ1〜2問、日常相談は3問)
definition / misunderstanding / comparison / daily_advice / application /
critical_question / example_request

## 出力形式(JSONのみ)
{{
  "questions": [
    {{
      "question": "ユーザーが実際に入力しそうな質問文",
      "intent": "definition|misunderstanding|comparison|daily_advice|application|critical_question|example_request|relationship_question",
      "answer_direction": "この質問への回答方向(カードの方針に沿って)"
    }}
  ]
}}"""

VALID_INTENTS = {
    "definition", "misunderstanding", "comparison", "daily_advice",
    "application", "critical_question", "example_request", "relationship_question",
}


def run(card_id: str) -> int:
    """カード1枚に対する質問群を生成する。生成数を返す。"""
    c = db.client()
    card = c.table("thought_cards").select("*").eq("card_id", card_id).single().execute().data

    result = llm.call_json(
        agent_name="question_pattern_generator",
        model=config.MODEL_CARD_DRAFT,
        system=SYSTEM,
        prompt=PROMPT_TEMPLATE.format(
            title=card["title"],
            thought_id=card["thought_id"],
            core_claim=card.get("core_claim") or "-",
            distinctions=card.get("distinctions") or [],
            answer_policy=card.get("answer_policy") or [],
            prohibitions=card.get("prohibitions") or [],
        ),
        input_ref=card_id,
        max_tokens=3000,
    )
    questions = [
        q for q in result.get("questions", [])
        if q.get("question") and q.get("intent") in VALID_INTENTS
    ]
    if not questions:
        return 0

    vectors = embed_step.embed_texts([q["question"] for q in questions])
    rows = []
    for i, (q, vec) in enumerate(zip(questions, vectors), start=1):
        rows.append(
            {
                "question_id": f"q_{card['thought_id'].lower()}_{i:03d}",
                "person_id": card["person_id"],
                "question": q["question"],
                "target_thought_id": card["thought_id"],
                "target_card_id": card_id,
                "intent": q["intent"],
                "answer_direction": q.get("answer_direction"),
                "embedding": vec,
                "status": "draft",
            }
        )
    c.table("thought_questions").upsert(rows).execute()
    return len(rows)


def run_for_all_cards(person_id: str = "x_shigyo") -> int:
    """質問が未生成のカード(rejected以外)すべてに対して生成する。"""
    c = db.client()
    cards = (
        c.table("thought_cards").select("card_id")
        .eq("person_id", person_id)
        .in_("status", ["draft", "reviewing", "approved"])
        .execute()
    ).data
    existing = {
        r["target_card_id"]
        for r in c.table("thought_questions").select("target_card_id")
        .eq("person_id", person_id).execute().data
    }
    total = 0
    for card in cards:
        if card["card_id"] not in existing:
            total += run(card["card_id"])
    return total
