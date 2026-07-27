"""青空文庫コーパスの取り込みCLI(C-T5)。

使い方(worker ディレクトリで):
  uv run python -m src.aozora.cli manifest            # 113件のマニフェスト取込
  uv run python -m src.aozora.cli in-progress         # 作業中8件の記録(本文は取らない)
  uv run python -m src.aozora.cli ingest 000799       # 版を1つ取り込む
  uv run python -m src.aozora.cli ingest-phase-a      # Phase A 13資料をまとめて取り込む
  uv run python -m src.aozora.cli report              # コーパスの状態を出す

正本仕様: docs/CORPUS_T1_SPEC.md
"""

import argparse
import csv
import io
import sys
import urllib.request
import zipfile
from collections import Counter

from .. import db
from . import gen_creative_cards, ingest, manifest, person_page

PERSON_ID = "natsume_soseki"
YUME_PROFILE_ID = "cp_yume_juya"
AOZORA_PERSON_ID = "000148"
CSV_URL = "https://www.aozora.gr.jp/index_pages/list_person_all_extended_utf8.zip"
PERSON_PAGE_URL = f"https://www.aozora.gr.jp/index_pages/person{int(AOZORA_PERSON_ID)}.html"

# Phase A のコア資料(仕様 §4.1)。edition_id → 作品名
PHASE_A_EDITIONS = {
    # 思想の中核(core_thought)
    "000772": "私の個人主義",
    "000759": "現代日本の開化",
    "000788": "中味と形式",
    "001747": "模倣と独立",
    "000757": "道楽と職業",
    "000755": "文芸の哲学的基礎",
    "000756": "文芸と道徳",
    "000778": "教育と文芸",
    # 創作・Bridge Rule の中核(creative_grammar)
    "001102": "創作家の態度",
    "000796": "写生文",
    "000793": "作物の批評",
    "002667": "高浜虚子著『鶏頭』序",
    # 創作の参照(narrative_reference / style_reference)
    "000799": "夢十夜",
}


def _fetch_manifest_rows() -> list[dict]:
    """公式CSVを取得し、対象人物の行を返す。"""
    with urllib.request.urlopen(CSV_URL, timeout=120) as r:
        data = r.read()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        name = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
        text = z.read(name).decode("utf-8-sig")
    return [
        row for row in csv.DictReader(io.StringIO(text))
        if row.get("人物ID") == AOZORA_PERSON_ID
    ]


def cmd_manifest(_args) -> None:
    rows = _fetch_manifest_rows()
    result = manifest.import_manifest(rows, person_id=PERSON_ID)
    print(f"CSV {len(rows)}行 → 作品{result['works']}件 / 版{result['editions']}件 "
          f"/ 要確認{result['review_queued']}件")


def cmd_in_progress(_args) -> None:
    with urllib.request.urlopen(PERSON_PAGE_URL, timeout=60) as r:
        html = r.read().decode("utf-8", errors="replace")
    entries = person_page.parse_in_progress(html)
    manifest.import_in_progress_entries(
        entries, person_id=PERSON_ID, source_page_url=PERSON_PAGE_URL
    )
    print(f"作業中 {len(entries)}件を記録(本文は取得しない)")
    for e in entries:
        print(f"  {e['aozora_work_id']} {e['title']} ({e['orthography']})")


def cmd_ingest(args) -> None:
    result = ingest.ingest_edition(args.edition_id)
    print(f"{args.edition_id}: genre={result['genre']} role={result['corpus_role']} "
          f"chunks={result['chunks']} 化け率={result['garbling_ratio']:.4f}")


def cmd_ingest_phase_a(_args) -> None:
    total = 0
    for edition_id, title in PHASE_A_EDITIONS.items():
        result = ingest.ingest_edition(edition_id)
        total += result["chunks"]
        print(f"  {edition_id} {title:20s} genre={result['genre']:16s} "
              f"role={result['corpus_role']:20s} chunks={result['chunks']:4d}")
    print(f"合計 {len(PHASE_A_EDITIONS)}資料 / {total}チャンク")


def cmd_create_profile(_args) -> None:
    """『夢十夜』の creative profile を作る(C-T6 の前提)。"""
    c = db.client()
    c.table("creative_profiles").upsert({
        "profile_id": YUME_PROFILE_ID,
        "person_id": PERSON_ID,
        "name": "夢十夜",
        "slug": "yume-juya",
        "description": "『夢十夜』を参照した新作短編を生成するためのプロファイル",
        # 参照する原典。C-T5 で投入した夢十夜と創作論
        "source_scope": {"source_ids": ["AOZORA_000799"],
                         "corpus_roles": ["narrative_reference", "creative_grammar"]},
        # 生成文の正書法。青空文庫の底本(新字新仮名)に合わせる
        "orthography_policy": "新字新仮名",
        "target_language": "ja",
        "historical_period": "明治",
        "default_generation_settings": {
            "use_rag": True, "use_cards": True, "rules": "off",
            "preset_name": "cards_only",
            "guard": {"ngram_n": 10, "lcs_threshold": 20,
                      "ngram_overlap_ratio_max": 0.05, "max_regenerations": 2},
        },
        "disclosure_text": (
            "本文はAIが公開原典と承認済み創作カードを参照して生成した創作物であり、"
            "原作者本人の作品ではありません。"
        ),
        # 誤認防止のため題名レベルで固定する(仕様§5.1)
        "display_title_format": "{title}（AI創作）",
        "copyright_policy": "原典はパブリックドメイン(夏目漱石・没1916年)",
        "status": "active",
    }).execute()
    print(f"creative_profile を作成/更新: {YUME_PROFILE_ID}")


def cmd_gen_cards(_args) -> None:
    """承認前の創作カード候補を生成する(必ず draft)。"""
    result = gen_creative_cards.generate_for_profile(YUME_PROFILE_ID)
    print(f"カード候補: 新規{result['created']}件 / "
          f"既存スキップ{result['skipped_existing']}件 / "
          f"根拠不足スキップ{result['skipped_no_evidence']}件")
    c = db.client()
    cards = (
        c.table("creative_cards").select("card_id,card_type,title,evidence_type,status")
        .eq("profile_id", YUME_PROFILE_ID).order("card_type").execute().data
    )
    for card in cards:
        print(f"  {card['card_id']} [{card['status']:8s}] {card['card_type']:12s} "
              f"{card['evidence_type'][:24]:24s} {card['title']}")


def cmd_show_card(args) -> None:
    """カードの内容と根拠原文を表示する(承認前の確認用)。"""
    c = db.client()
    card = (
        c.table("creative_cards").select("*").eq("card_id", args.card_id)
        .single().execute().data
    )
    print(f"[{card['status']}] {card['card_type']} / {card['evidence_type']}")
    print(f"  {card['title']}")
    if card.get("summary"):
        print(f"  {card['summary']}")
    for key in ("positive_patterns", "negative_patterns"):
        for v in card.get(key) or []:
            print(f"    {'+' if key.startswith('positive') else '-'} {v}")
    print("  --- 根拠原文 ---")
    for chunk_id in card.get("evidence_chunk_ids") or []:
        rows = (
            c.table("source_chunks").select("chunk_id,chapter_title,text")
            .eq("chunk_id", chunk_id).execute().data
        )
        if not rows:
            print(f"    [{chunk_id}] ⚠️ 実在しない(このカードは承認できない)")
            continue
        r = rows[0]
        head = f"({r['chapter_title']})" if r.get("chapter_title") else ""
        print(f"    [{r['chunk_id']}]{head} {r['text'][:90]}")


def cmd_approve(args) -> None:
    for card_id in args.card_ids:
        try:
            gen_creative_cards.approve_card(card_id, reviewed_by=args.by)
            print(f"承認: {card_id}")
        except ValueError as exc:
            print(f"承認できず: {card_id}: {exc}")


def cmd_reject(args) -> None:
    for card_id in args.card_ids:
        gen_creative_cards.reject_card(card_id, reviewed_by=args.by)
        print(f"却下: {card_id}")


def cmd_embed(_args) -> None:
    """未生成のチャンクにembeddingを付ける(OpenAIの実キーが要る)。"""
    done = ingest.embed_pending_chunks()
    print(f"embedding生成: {done}件")


def cmd_report(_args) -> None:
    """コーパスの状態とデータ品質を出す(指示書§14.6)。"""
    c = db.client()
    srcs = (
        c.table("sources")
        .select("source_id,title,corpus_role,document_genre")
        .eq("source_provider", "aozora").execute().data
    )
    chunks = (
        c.table("source_chunks")
        .select("source_id,speaker_role,thought_eligibility,tag_review_status")
        .eq("chunker_version", "aozora_v1").execute().data
    )
    print(f"文書: {len(srcs)}件 / チャンク: {len(chunks)}件")
    print(f"corpus_role: {dict(Counter(s['corpus_role'] for s in srcs))}")
    print(f"document_genre: {dict(Counter(s['document_genre'] for s in srcs))}")
    print(f"speaker_role: {dict(Counter(x['speaker_role'] for x in chunks))}")
    print(f"tag_review_status: {dict(Counter(x['tag_review_status'] for x in chunks))}")

    by_src = {s["source_id"]: s for s in srcs}
    core = [
        x for x in chunks
        if by_src.get(x["source_id"], {}).get("corpus_role") == "core_thought"
        and x["speaker_role"] == "author_direct"
        and x["thought_eligibility"] != "excluded"
    ]
    fiction_in_core = [
        x for x in core
        if by_src[x["source_id"]]["document_genre"] in ("novel", "short_story", "sketch")
    ]
    print(f"\nauthor_thought_core_index: {len(core)}件 / "
          f"うち小説由来 {len(fiction_in_core)}件 "
          f"{'OK' if not fiction_in_core else '← NG(混入している)'}")

    queue = (
        c.table("canonical_work_review_queue").select("*")
        .eq("status", "open").execute().data
    )
    print(f"未解決の作品同定キュー: {len(queue)}件")
    for q in queue:
        print(f"  {q['aozora_work_ids']}: {q['reason'][:80]}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="青空文庫コーパス取り込み")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("manifest", help="公式CSVから作品・版を取り込む").set_defaults(
        func=cmd_manifest)
    sub.add_parser("in-progress", help="作業中作品を記録する(本文は取らない)").set_defaults(
        func=cmd_in_progress)
    p_ingest = sub.add_parser("ingest", help="版を1つ取り込む")
    p_ingest.add_argument("edition_id")
    p_ingest.set_defaults(func=cmd_ingest)
    sub.add_parser("ingest-phase-a", help="Phase A 13資料を取り込む").set_defaults(
        func=cmd_ingest_phase_a)
    sub.add_parser("embed", help="未生成チャンクのembeddingを作る").set_defaults(
        func=cmd_embed)
    sub.add_parser("create-profile", help="『夢十夜』のcreative profileを作る").set_defaults(
        func=cmd_create_profile)
    sub.add_parser("gen-cards", help="創作カード候補を生成する(draft)").set_defaults(
        func=cmd_gen_cards)
    p_show = sub.add_parser("show-card", help="カードと根拠原文を表示する")
    p_show.add_argument("card_id")
    p_show.set_defaults(func=cmd_show_card)
    p_ok = sub.add_parser("approve", help="カードを承認する(根拠の実在を検証する)")
    p_ok.add_argument("card_ids", nargs="+")
    p_ok.add_argument("--by", default="cli", help="承認者")
    p_ok.set_defaults(func=cmd_approve)
    p_ng = sub.add_parser("reject", help="カードを却下する")
    p_ng.add_argument("card_ids", nargs="+")
    p_ng.add_argument("--by", default="cli", help="却下者")
    p_ng.set_defaults(func=cmd_reject)
    sub.add_parser("report", help="コーパスの状態を出す").set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
