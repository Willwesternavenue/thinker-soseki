"""思想カードと最重要原典の整合性レビュー(再利用可能)。

各承認カードについて、最重要4冊(ポポイ/憧れの思想/超葉隠論/生くる)から関連箇所を
ベクトル検索し、Sonnetでカードの記述が「支持される/矛盾する/根拠なし」を判定する。
問題ありカードのレポートをMarkdownで出力する。

  uv run python -m src.review_cards            # 全承認カード
  uv run python -m src.review_cards --limit 5  # 先頭5枚だけ(試験)
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import config, db, llm
from .steps import embed

# 最重要4冊
CORE_BOOKS = {"BOOK_012", "BOOK_013", "BOOK_014", "BOOK_015"}
CORE_BOOK_NAMES = {
    "BOOK_012": "おゝポポイ(自伝対談)",
    "BOOK_013": "超葉隠論",
    "BOOK_014": "生くる",
    "BOOK_015": "憧れの思想",
}
PERSON_ID = "x_shigyo"

SYSTEM = """あなたは思想家アバターの「思想カード」を原典と突き合わせて校閲する編集者である。
与えられた原典の関連箇所だけに基づき、カードの記述が原典に支持されるか、矛盾・事実誤認が
あるか、そもそも関連する根拠が原典に見当たらないかを判定する。原典に無いことは補わない。JSONのみ出力する。"""

PROMPT = """## 検査対象の思想カード
タイトル: {title}
中核命題: {core_claim}
重要な区別: {distinctions}
禁止事項: {prohibitions}

## 最重要4冊からの関連箇所(これだけを根拠にする)
{passages}

## 判定してほしいこと
- verdict:
  - "supported"   … カードの主張が上記原典に概ね支持される
  - "contradicted"… 原典と矛盾する、または事実誤認・出来事の混同がある
  - "unsupported" … カードに関連する根拠が上記原典に見当たらない(真偽判断不可)
- issues: 具体的な問題(事実の誤り・別々の出来事の混同・原典に無い断定など)。無ければ空配列
- supporting_chunks: カードを支持する原典のchunk_id(あれば)

出力(JSONのみ): {{"verdict":"...","issues":["..."],"supporting_chunks":["..."]}}"""


def _related_passages(card: dict) -> list[dict]:
    """カードに関連する4冊の箇所を、ベクトル+全文検索で広めに集める(偽陽性=recall不足の低減)。"""
    query = (card.get("title") or "") + " " + (card.get("core_claim") or "")
    vec = embed.embed_texts([query])[0]
    seen: dict[str, dict] = {}

    vres = db.client().rpc("match_source_chunks_all", {
        "query_embedding": json.dumps(vec),
        "target_person_id": PERSON_ID,
        "match_count": 40,
    }).execute()
    for r in (vres.data or []):
        if r["source_id"] in CORE_BOOKS and r["chunk_id"] not in seen:
            seen[r["chunk_id"]] = r
        if len([c for c in seen.values()]) >= 10:
            break

    # タイトル語での全文検索も足す(ベクトルで埋もれる固有名詞・専門語を拾う)
    kres = db.client().rpc("search_source_chunks_fulltext", {
        "query_text": card.get("title") or "",
        "thought_ids": None,
        "target_person_id": PERSON_ID,
        "match_count": 8,
    }).execute()
    if not getattr(kres, "error", None):
        for r in (kres.data or []):
            if r["source_id"] in CORE_BOOKS and r["chunk_id"] not in seen:
                seen[r["chunk_id"]] = r

    # カードに紐づけ済みの代表チャンク(rep + approvedリンク)は必ず含める。
    # これがないと、リンク済みの根拠を「原典に無い」と誤判定してしまう(偽陽性)。
    linked_ids = set(card.get("representative_chunk_ids") or [])
    links = db.client().table("thought_evidence_links").select("chunk_id") \
        .eq("thought_id", card["thought_id"]).eq("status", "approved").execute().data
    linked_ids |= {l["chunk_id"] for l in links}
    missing = [cid for cid in linked_ids if cid not in seen]
    if missing:
        rows = db.select_in("source_chunks", "chunk_id, source_id, text", "chunk_id", missing)
        for r in rows:
            seen[r["chunk_id"]] = r

    return list(seen.values())[:14]


def review_card(card: dict) -> dict:
    passages = _related_passages(card)
    passage_text = "\n\n".join(
        f"[{p['chunk_id']} / {CORE_BOOK_NAMES.get(p['source_id'],'')}] {p['text'][:500]}"
        for p in passages
    ) or "(関連箇所が見つからない)"
    result = llm.call_json(
        agent_name="card_consistency_reviewer",
        model=config.MODEL_HEAVY_DISTILL,
        system=SYSTEM,
        prompt=PROMPT.format(
            title=card.get("title"),
            core_claim=card.get("core_claim"),
            distinctions=json.dumps(card.get("distinctions") or [], ensure_ascii=False),
            prohibitions=json.dumps(card.get("prohibitions") or [], ensure_ascii=False),
            passages=passage_text,
        ),
        input_ref=card["card_id"],
        max_tokens=2000,
    )
    return {
        "card_id": card["card_id"],
        "thought_id": card["thought_id"],
        "title": card.get("title"),
        "verdict": result.get("verdict", "unsupported"),
        "issues": result.get("issues", []),
        "supporting_chunks": result.get("supporting_chunks", []),
        "n_passages": len(passages),
    }


def main() -> None:
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    cards = (
        db.client().table("thought_cards")
        .select("card_id, thought_id, title, core_claim, distinctions, prohibitions, representative_chunk_ids")
        .eq("person_id", PERSON_ID).eq("status", "approved")
        .neq("card_id", "card_fallback_001")
        .order("thought_id")
        .execute()
    ).data
    if limit:
        cards = cards[:limit]
    print(f"レビュー対象: {len(cards)}枚(最重要4冊と照合)")

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=config.DISTILL_CONCURRENCY) as pool:
        futures = {pool.submit(review_card, c): c["card_id"] for c in cards}
        for i, future in enumerate(as_completed(futures), 1):
            try:
                r = future.result()
                results.append(r)
                mark = {"supported": "✓", "contradicted": "✗", "unsupported": "?"}.get(r["verdict"], "?")
                print(f"  [{i}/{len(cards)}] {mark} {r['title']}")
            except Exception as exc:
                print(f"  失敗 {futures[future]}: {exc}")

    order = {"contradicted": 0, "unsupported": 1, "supported": 2}
    results.sort(key=lambda r: (order.get(r["verdict"], 3), r["thought_id"]))

    counts = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    lines = ["# 思想カード×最重要4冊 整合性レビュー\n",
             f"対象4冊: {', '.join(CORE_BOOK_NAMES.values())}\n",
             f"結果: 矛盾/誤認 {counts.get('contradicted',0)} / "
             f"根拠なし {counts.get('unsupported',0)} / 支持 {counts.get('supported',0)}\n"]
    for r in results:
        label = {"contradicted": "✗ 矛盾・要修正", "unsupported": "? 根拠なし",
                 "supported": "✓ 支持"}.get(r["verdict"], r["verdict"])
        lines.append(f"\n## {label}: {r['title']} ({r['thought_id']})")
        if r["issues"]:
            for issue in r["issues"]:
                lines.append(f"- {issue}")
        if r["supporting_chunks"]:
            lines.append(f"- 支持chunk: {', '.join(r['supporting_chunks'])}")

    out = Path(config.REPO_ROOT) / "card_consistency_report.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n=== 集計: 矛盾/誤認 {counts.get('contradicted',0)} / "
          f"根拠なし {counts.get('unsupported',0)} / 支持 {counts.get('supported',0)} ===")
    print(f"レポート: {out}")


if __name__ == "__main__":
    main()
