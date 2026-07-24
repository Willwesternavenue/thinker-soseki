"""初期Judgment Ruleドラフトを judgment_rules 5テーブルへ投入する。

data/judgment_rules_initial_v1.json(docs/judgment_rules_initial_draft.md のデータ版)を読み、
identity / version(draft) / evidence(カード+チャンク) / examples を作成する。

  uv run python -m src.import_judgment_rules            # ドライラン(解析結果を表示のみ)
  uv run python -m src.import_judgment_rules --commit   # 実際にDBへ投入

再実行可能(同じrule_idは削除→再投入。versionsやexamplesはcascadeで消える)。
既にレビューが付いたバージョンがある規則はスキップする(レビュー履歴を消さないため)。
"""

import json
import sys

from . import config, db

SRC = config.REPO_ROOT / "data" / "judgment_rules_initial_v1.json"
PERSON_ID = "x_shigyo"
CREATED_BY = "claude-draft-v1"


def load_rules() -> list[dict]:
    data = json.loads(SRC.read_text())
    return data["rules"]


def build_card_map(client) -> dict[str, str]:
    """thought_id → card_id(approvedのみ)。thought_idはカード側で一意でない可能性が
    あるため、複数あれば最初の1枚(承認ゲートの暫定。L2正規化時に見直し)。"""
    rows = (
        client.table("thought_cards")
        .select("thought_id, card_id")
        .eq("person_id", PERSON_ID)
        .eq("status", "approved")
        .execute()
        .data
    )
    mapping: dict[str, str] = {}
    for r in rows:
        mapping.setdefault(r["thought_id"], r["card_id"])
    return mapping


def has_reviews(client, rule_id: str) -> bool:
    """既存バージョンにレビューが付いていれば、削除・再投入しない。"""
    versions = (
        client.table("judgment_rule_versions")
        .select("rule_version_id")
        .eq("rule_id", rule_id)
        .execute()
        .data
    )
    if not versions:
        return False
    ids = [v["rule_version_id"] for v in versions]
    reviews = (
        client.table("judgment_rule_reviews")
        .select("review_id", count="exact")
        .in_("rule_version_id", ids)
        .limit(0)
        .execute()
    )
    return (reviews.count or 0) > 0


def main() -> None:
    commit = "--commit" in sys.argv
    rules = load_rules()
    client = db.client()
    card_map = build_card_map(client)

    total_evidence = 0
    total_examples = 0
    missing_concepts: set[str] = set()

    for rule in rules:
        content = rule["content"]
        concepts = content.get("input_concepts", [])
        cards = [(c, card_map.get(c)) for c in concepts]
        missing_concepts.update(c for c, cid in cards if cid is None)
        n_evidence = sum(1 for _, cid in cards if cid) + len(rule.get("evidence_chunk_ids", []))
        n_examples = len(rule.get("examples", []))
        total_evidence += n_evidence
        total_examples += n_examples
        print(
            f"{rule['rule_id']}: scope={rule['rule_scope']} type={rule['rule_type']} "
            f"evidence={n_evidence} examples={n_examples}"
        )

    print(f"\n合計: rules={len(rules)} evidence={total_evidence} examples={total_examples}")
    if missing_concepts:
        print(f"警告: approvedカードが見つからないthought_id: {sorted(missing_concepts)}")

    if not commit:
        print("\n(ドライラン。投入するには --commit を付ける)")
        return

    for rule in rules:
        rule_id = rule["rule_id"]
        if has_reviews(client, rule_id):
            print(f"skip {rule_id}: レビュー済みバージョンが存在するため上書きしない")
            continue

        # 冪等化: 既存を削除(versions/evidence/examplesはcascade)
        client.table("judgment_rules").delete().eq("rule_id", rule_id).execute()

        content = rule["content"]
        concepts = content.get("input_concepts", [])
        primary_card = next((card_map[c] for c in concepts if c in card_map), None)

        client.table("judgment_rules").insert(
            {
                "rule_id": rule_id,
                "person_id": PERSON_ID,
                "rule_family_id": rule_id,
                "variant_type": "integrated",
                "title": rule["title"],
                "rule_scope": rule["rule_scope"],
                "rule_type": rule["rule_type"],
                "creation_method": "corpus_extraction",
                "source_card_id": primary_card,
                "created_by": CREATED_BY,
                "creation_rationale": rule.get("creation_rationale"),
            }
        ).execute()

        client.table("judgment_rule_versions").insert(
            {
                "rule_id": rule_id,
                "version": 1,
                "status": "draft",
                "content": content,
                "change_reason": "初期ドラフト(docs/judgment_rules_initial_draft.md)",
                "created_by": CREATED_BY,
            }
        ).execute()

        evidence_rows = [
            {
                "rule_id": rule_id,
                "card_id": card_map[c],
                "evidence_role": "supports",
                "origin_type": "corpus_inferred",
                "note": f"thought_id={c} のapprovedカード由来",
            }
            for c in concepts
            if c in card_map
        ] + [
            {
                "rule_id": rule_id,
                "chunk_id": chunk_id,
                "evidence_role": "supports",
                "origin_type": "corpus_inferred",
            }
            for chunk_id in rule.get("evidence_chunk_ids", [])
        ]
        if evidence_rows:
            client.table("judgment_rule_evidence").insert(evidence_rows).execute()

        example_rows = [
            {
                "rule_id": rule_id,
                "example_type": ex["example_type"],
                "target": ex.get("target", "input"),
                "example_text": ex["example_text"],
                "expected_activation": ex.get("expected_activation"),
                "expected_reason": ex.get("expected_reason"),
                "dataset_split": "development",
                "status": "draft",
            }
            for ex in rule.get("examples", [])
        ]
        if example_rows:
            client.table("judgment_rule_examples").insert(example_rows).execute()

        print(f"ok {rule_id}")

    print("\n投入完了")


if __name__ == "__main__":
    main()
