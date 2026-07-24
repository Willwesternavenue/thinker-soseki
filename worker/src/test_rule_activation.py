"""Judgment Rule 発火ユニットテストランナー(仕様: docs/judgment_rules_spec_v0_2.md 10章)。

judgment_rule_examples(target='input')の各例に対し、規則のtrigger_conditions/exceptionsだけを
見せたLLMに発火判定させ、expected_activation と突き合わせる。
判定器は期待値・example_type・expected_reason を一切見ない(ブラインド判定)。

  uv run python -m src.test_rule_activation                 # 全規則
  uv run python -m src.test_rule_activation --rule JR_X     # 1規則のみ
  uv run python -m src.test_rule_activation --model <id>    # モデル上書き(既定はMODEL_LIGHT_DISTILL)

結果: 標準出力にサマリ + evaluation/rule_activation_report.json に詳細を保存。
- positive → fires=true が正解(外すと「見逃し」= recall問題)
- hard_negative / adversarial → fires=false が正解(外すと「誤発火」= 発火条件が広すぎる)
- boundary(expected=null)→ 合否をつけず判定結果を人間レビュー用に記録
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config, db, llm

REPORT_PATH = config.REPO_ROOT / "evaluation" / "rule_activation_report.json"

SYSTEM = """あなたは思想家AIの判断規則の発火判定器である。
与えられた規則の発火条件と例外条件だけに基づき、入力に対して規則が発火すべきかを判定する。
相談への回答はしない。JSONのみを返す。"""

PROMPT = (
    lambda rule_title, triggers, premises, exceptions, text: f"""# 規則: {rule_title}

## 発火条件(いずれかに該当すれば発火候補)
{chr(10).join(f"- {t}" for t in triggers)}

## 前提
{chr(10).join(f"- {p}" for p in premises) or "- (なし)"}

## 例外条件(該当する場合は発火させない)
{chr(10).join(f"- {e}" for e in exceptions) or "- (なし)"}

# 入力(ユーザー発話)
{text}

# 判定
発火条件への該当と例外条件を検討し、JSONのみで返答せよ:
{{"fires": true/false, "matched_trigger_conditions": ["該当した発火条件の文言"], "exception_applied": true/false, "reason": "一文の判定理由"}}"""
)


def load_rules(client, only_rule: str | None) -> dict[str, dict]:
    """rule_id → {title, content(最新version)}"""
    q = client.table("judgment_rules").select("rule_id, title")
    if only_rule:
        q = q.eq("rule_id", only_rule)
    rules = {r["rule_id"]: {"title": r["title"]} for r in q.execute().data}
    versions = (
        client.table("judgment_rule_versions")
        .select("rule_id, version, content")
        .in_("rule_id", list(rules.keys()))
        .order("version", desc=True)
        .execute()
        .data
    )
    for v in versions:  # version降順なので最初に見たものが最新
        rules[v["rule_id"]].setdefault("content", v["content"])
    return {k: v for k, v in rules.items() if "content" in v}


def load_examples(client, rule_ids: list[str]) -> list[dict]:
    return (
        client.table("judgment_rule_examples")
        .select("example_id, rule_id, example_type, example_text, expected_activation")
        .in_("rule_id", rule_ids)
        .eq("target", "input")
        .neq("status", "deprecated")
        .execute()
        .data
    )


def judge_one(model: str, rule: dict, example: dict) -> dict:
    content = rule["content"]
    result = llm.call_json(
        agent_name="rule_activation_test",
        model=model,
        system=SYSTEM,
        prompt=PROMPT(
            rule["title"],
            content.get("trigger_conditions", []),
            content.get("premises", []),
            content.get("exceptions", []),
            example["example_text"],
        ),
        input_ref=example["example_id"],
        max_tokens=500,
    )
    fired = bool(result.get("fires"))
    expected = example["expected_activation"]
    return {
        "rule_id": example["rule_id"],
        "example_id": example["example_id"],
        "example_type": example["example_type"],
        "example_text": example["example_text"],
        "expected": expected,
        "fired": fired,
        "exception_applied": bool(result.get("exception_applied")),
        "reason": result.get("reason", ""),
        "verdict": "boundary" if expected is None else ("pass" if fired == expected else "fail"),
    }


def main() -> None:
    args = sys.argv[1:]
    only_rule = args[args.index("--rule") + 1] if "--rule" in args else None
    model = args[args.index("--model") + 1] if "--model" in args else config.MODEL_LIGHT_DISTILL

    client = db.client()
    rules = load_rules(client, only_rule)
    examples = load_examples(client, list(rules.keys()))
    print(f"規則 {len(rules)}件 / 入力example {len(examples)}件 / model={model}\n")

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=config.DISTILL_CONCURRENCY) as pool:
        futures = {
            pool.submit(judge_one, model, rules[ex["rule_id"]], ex): ex["example_id"]
            for ex in examples
        }
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:  # 1件の失敗で全体を止めない
                print(f"error {futures[future]}: {exc}")

    # 集計
    scored = [r for r in results if r["verdict"] != "boundary"]
    passed = [r for r in scored if r["verdict"] == "pass"]
    missed = [r for r in scored if r["verdict"] == "fail" and r["expected"] is True]
    false_fires = [r for r in scored if r["verdict"] == "fail" and r["expected"] is False]
    boundaries = [r for r in results if r["verdict"] == "boundary"]

    print(f"=== 合計: {len(passed)}/{len(scored)} pass "
          f"(見逃し {len(missed)} / 誤発火 {len(false_fires)} / boundary {len(boundaries)}件は参考記録) ===\n")

    by_rule: dict[str, dict] = {}
    for r in scored:
        s = by_rule.setdefault(r["rule_id"], {"pass": 0, "fail": 0})
        s["pass" if r["verdict"] == "pass" else "fail"] += 1
    for rule_id in sorted(by_rule, key=lambda k: -by_rule[k]["fail"]):
        s = by_rule[rule_id]
        mark = "⚠️" if s["fail"] else "✅"
        print(f"{mark} {rule_id}: {s['pass']}/{s['pass'] + s['fail']}")

    if false_fires:
        print("\n--- 誤発火(発火条件が広すぎる候補) ---")
        for r in false_fires:
            print(f"[{r['rule_id']}] ({r['example_type']}) {r['example_text'][:40]}")
            print(f"   理由: {r['reason']}")
    if missed:
        print("\n--- 見逃し(発火条件が狭すぎる候補) ---")
        for r in missed:
            print(f"[{r['rule_id']}] {r['example_text'][:40]}")
            print(f"   理由: {r['reason']}")
    if boundaries:
        print("\n--- boundary判定(人間レビュー用) ---")
        for r in boundaries:
            print(f"[{r['rule_id']}] fires={r['fired']} : {r['example_text'][:40]}")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps({
        "model": model,
        "total_scored": len(scored),
        "passed": len(passed),
        "missed": len(missed),
        "false_fires": len(false_fires),
        "results": sorted(results, key=lambda r: (r["rule_id"], r["example_id"])),
    }, ensure_ascii=False, indent=1))
    print(f"\n詳細レポート: {REPORT_PATH}")


if __name__ == "__main__":
    main()
