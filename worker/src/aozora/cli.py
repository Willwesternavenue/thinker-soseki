"""青空文庫コーパスの取り込みCLI(C-T5)。

使い方(worker ディレクトリで):
  uv run python -m src.aozora.cli manifest            # 113件のマニフェスト取込
  uv run python -m src.aozora.cli in-progress         # 作業中8件の記録(本文は取らない)
  uv run python -m src.aozora.cli ingest 000799       # 版を1つ取り込む
  uv run python -m src.aozora.cli ingest-phase-a      # Phase A 13資料をまとめて取り込む
  uv run python -m src.aozora.cli gen-thought-cards   # 思想カード候補(draft)
  uv run python -m src.aozora.cli gen-rules           # 判断規則 + Bridge Rule 候補
  uv run python -m src.aozora.cli retag               # Pass2(LLM分類)を未適用チャンクへ
  uv run python -m src.aozora.cli review-tags         # Pass4 レビュー待ちを見る
  uv run python -m src.aozora.cli report              # コーパスの状態と品質を出す
  uv run python -m src.aozora.cli snapshot --out s.json    # スナップショットを保存
  uv run python -m src.aozora.cli snapshot --compare s.json  # 取り込みの再現を照合

正本仕様: docs/CORPUS_T1_SPEC.md
"""

import argparse
import csv
import io
import json
import sys
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

from .. import db
from ..creative import card_devices, device_catalog
from ..steps import gen_questions
from . import (
    gen_creative_cards, gen_rules, gen_thought_cards, ingest, manifest,
    paged, person_page, retag, snapshot as snapshot_mod,
)

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


def cmd_create_profile(args) -> None:
    """creative profile を作る(C-T6 の前提)。既定値は『夢十夜』(引数省略時は従来通り)。

    ⚠️ 第2作品向けにプロファイルを作る場合、source_scope.source_ids を
    その作品の source_id だけに絞ること。gen_creative_cards は
    (source_scope 修正後)ここを見てカードの根拠チャンクを絞り込む —
    絞らないと他作品の本文まで根拠に混ざる(引き継ぎ B-2 で発覚した穴)。
    """
    c = db.client()
    source_ids = (
        [s.strip() for s in args.source_ids.split(",") if s.strip()]
        if args.source_ids else ["AOZORA_000799"]
    )
    corpus_roles = (
        [r.strip() for r in args.corpus_roles.split(",") if r.strip()]
        if args.corpus_roles else ["narrative_reference", "creative_grammar"]
    )
    c.table("creative_profiles").upsert({
        "profile_id": args.profile_id,
        "person_id": PERSON_ID,
        "name": args.name,
        "slug": args.slug,
        "description": args.description or f"『{args.name}』を参照した新作短編を生成するためのプロファイル",
        "source_scope": {"source_ids": source_ids, "corpus_roles": corpus_roles},
        # 生成文の正書法。青空文庫の底本(新字新仮名)に合わせる
        "orthography_policy": "新字新仮名",
        "target_language": "ja",
        "historical_period": args.historical_period,
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
        "copyright_policy": args.copyright_policy,
        "status": "active",
    }).execute()
    print(f"creative_profile を作成/更新: {args.profile_id} (source_ids={source_ids})")


def cmd_gen_cards(args) -> None:
    """承認前の創作カード候補を生成する(必ず draft)。"""
    result = gen_creative_cards.generate_for_profile(args.profile_id)
    print(f"カード候補: 新規{result['created']}件 / "
          f"既存スキップ{result['skipped_existing']}件 / "
          f"根拠不足スキップ{result['skipped_no_evidence']}件")
    c = db.client()
    cards = (
        c.table("creative_cards").select("card_id,card_type,title,evidence_type,status")
        .eq("profile_id", args.profile_id).order("card_type").execute().data
    )
    for card in cards:
        print(f"  {card['card_id']} [{card['status']:8s}] {card['card_type']:12s} "
              f"{card['evidence_type'][:24]:24s} {card['title']}")


def cmd_gen_device_catalog(args) -> None:
    """原作の章ごとの装置カタログを作る(続編生成の防具)。人手承認の対象ではない。"""
    catalog = device_catalog.generate_catalog(args.source_id, work_title=args.title)
    path = device_catalog.save_catalog(catalog)
    meta = catalog["meta"]
    print(f"装置カタログ: {meta['chapters']}章 / {meta['devices']}件 → {path}")
    for chapter in catalog["chapters"]:
        for d in chapter["devices"]:
            mark = "★中心" if d["role"] == device_catalog.ROLE_CENTRAL else "  付随"
            print(f"  {chapter['chapter_title']} {mark} {d['name']}"
                  f"  ({','.join(d['evidence_chunk_ids'])})")


def cmd_classify_cards(args) -> None:
    """承認済みカードを移植テストで装置/作風に判定する。カード本体は書き換えない。"""
    result = card_devices.classify_profile_cards(args.profile_id)
    path = card_devices.save_classification(result)
    meta = result["meta"]
    print(f"移植テスト: {meta['cards']}枚中 装置{meta['device_bound']}枚 → {path}")
    for row in result["cards"]:
        mark = "装置" if row["verdict"] == card_devices.DEVICE_BOUND else "作風"
        print(f"  [{mark}] {row['card_type']:12s} {row['title']}")
        print(f"         {row['reason']}")


def cmd_gen_thought_cards(_args) -> None:
    """思想カード候補を生成する(必ず draft)。"""
    result = gen_thought_cards.generate()
    print(f"思想カード候補: 新規{result['created']}件 / "
          f"既存スキップ{result['skipped_existing']}件 / "
          f"根拠不足スキップ{result['skipped_no_evidence']}件")
    c = db.client()
    for card in (c.table("thought_cards").select("card_id,thought_id,title,status")
                 .eq("person_id", PERSON_ID).order("thought_id").execute().data):
        print(f"  {card['card_id']} [{card['status']:8s}] {card['thought_id']:24s} "
              f"{card['title']}")


def cmd_approve_thought(args) -> None:
    for card_id in args.card_ids:
        try:
            gen_thought_cards.approve_card(card_id, reviewed_by=args.by)
            print(f"承認: {card_id}")
        except ValueError as exc:
            print(f"承認できず: {card_id}: {exc}")


def cmd_gen_questions(_args) -> None:
    """承認済み思想カードの代表質問を生成する。

    これが無いと Thought Router がベクトル検索で当てられず、思想質問が
    すべてフォールバックカードへ流れる(回答は返るが、質問に合ったカードが選ばれない)。
    """
    c = db.client()
    cards = (
        c.table("thought_cards").select("card_id, title")
        .eq("person_id", PERSON_ID).eq("status", "approved")
        .order("thought_id").execute().data
    )
    existing = {
        r["target_card_id"]
        for r in c.table("thought_questions").select("target_card_id")
        .eq("person_id", PERSON_ID).execute().data
    }
    total = 0
    for card in cards:
        if card["card_id"] in existing:
            continue
        n = gen_questions.run(card["card_id"])
        total += n
        print(f"  {card['card_id']} {card['title'][:40]}: {n}件")
    print(f"代表質問: {total}件")


def cmd_gen_rules(_args) -> None:
    """判断規則と Bridge Rule の候補を生成する(必ず draft)。"""
    j = gen_rules.generate_judgment_rules()
    print(f"判断規則: 新規{j['created']}件 / 既存{j['skipped_existing']}件 / "
          f"不正{j['skipped_invalid']}件")
    b = gen_rules.generate_bridge_rules()
    print(f"Bridge Rule: 新規{b['created']}件 / 既存{b['skipped_existing']}件 / "
          f"不正{b['skipped_invalid']}件")
    c = db.client()
    for r in (c.table("judgment_rules").select("rule_id,rule_scope,rule_type,title")
              .eq("person_id", PERSON_ID).order("rule_scope").execute().data):
        print(f"  {r['rule_id']} [{r['rule_scope']:12s}] {r['rule_type']:20s} "
              f"{r['title']}")


def cmd_approve_rule(args) -> None:
    for rule_id in args.rule_ids:
        try:
            gen_rules.approve_rule(rule_id, reviewed_by=args.by)
            print(f"承認: {rule_id}")
        except ValueError as exc:
            print(f"承認できず: {rule_id}: {exc}")


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


def cmd_retag(args) -> None:
    """Pass2(LLM分類)を未適用チャンクへ適用する。"""
    result = retag.retag_pending(
        limit=args.limit, source_id=args.source, force=args.force
    )
    print(f"Pass2 適用: {result['updated']}件 / "
          f"うち要レビュー {result['needs_review']}件")


def cmd_review_tags(_args) -> None:
    """Pass4 のレビュー待ちを出す(確信度の低い順)。"""
    queue = retag.review_queue()
    print(f"レビュー待ち: {len(queue)}件")
    for q in queue:
        conf = q["tag_confidence"]
        print(f"  {q['chunk_id']} conf={conf if conf is not None else '-'} "
              f"speaker={q['speaker_role']} claim={q['claim_type']}")
        print(f"      {(q['text'] or '')[:60].replace(chr(10), ' ')}")
        if q["classification_reason"]:
            print(f"      理由: {q['classification_reason'][:100]}")


def cmd_tag_review(args) -> None:
    """レビューを終える。--set KEY=VALUE で値を直す。"""
    corrections = dict(kv.split("=", 1) for kv in (args.set or []))
    result = retag.resolve_review(
        args.chunk_id, reviewed_by=args.by, corrections=corrections or None
    )
    print(result.get("error") or f"{result['chunk_id']}: {result['status']}")


def cmd_report(_args) -> None:
    """コーパスの状態とデータ品質を出す(指示書§14.6)。"""
    c = db.client()
    # 全件取得はページング必須(PostgRESTの1000行上限。paged.py 参照)
    srcs = paged.fetch_all(
        lambda: c.table("sources")
        .select("source_id,title,corpus_role,document_genre")
        .eq("source_provider", "aozora").order("source_id")
    )
    chunks = paged.fetch_all(
        lambda: c.table("source_chunks")
        .select("source_id,speaker_role,thought_eligibility,tag_review_status")
        .eq("chunker_version", "aozora_v1").order("chunk_id")
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

    # データ品質レポート(受入#20 / 指示書§14.6)
    report = snapshot_mod.build_quality_report()
    print(f"\nデータ品質: {'OK' if report['passed'] else 'NG'}")
    for check in report["checks"]:
        mark = "  " if check["passed"] else "← NG"
        value = (
            f"{check['value']:.3%}" if isinstance(check["value"], float)
            else f"{check['value']}件"
        )
        print(f"  {check['label']}: {value} {mark}")
        if not check["passed"] and check["detail"]:
            print(f"      {', '.join(map(str, check['detail']))}")


def cmd_snapshot(args) -> None:
    """corpus snapshot(受入#18)。取り込みを再現できたかを digest で照合する。"""
    snap = snapshot_mod.build_snapshot()
    print(f"digest: {snap['digest']}")
    print(f"件数: {snap['counts']}")

    if args.compare:
        previous = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        diff = snapshot_mod.compare_snapshots(previous, snap)
        if diff["same"]:
            print(f"\n{args.compare} と一致(取り込みを再現できている)")
        else:
            print(f"\n{args.compare} と不一致:")
            for key, values in diff["counts"].items():
                print(f"  {key}: {values['old']} → {values['new']}")
            for label, key in (("追加", "sources_added"), ("欠落", "sources_removed"),
                               ("内容変更", "sources_changed")):
                if diff[key]:
                    print(f"  {label}: {', '.join(diff[key])}")

    if args.out:
        Path(args.out).write_text(
            json.dumps(snap, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\n保存: {args.out}")


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
    p_cp = sub.add_parser(
        "create-profile", help="creative profileを作る(既定は『夢十夜』)")
    p_cp.add_argument("--profile-id", default=YUME_PROFILE_ID)
    p_cp.add_argument("--name", default="夢十夜")
    p_cp.add_argument("--slug", default="yume-juya")
    p_cp.add_argument("--source-ids", help="対象作品のsource_id(カンマ区切り。省略時は夢十夜)")
    p_cp.add_argument("--corpus-roles",
                      help="対象corpus_role(カンマ区切り。省略時は narrative_reference,creative_grammar)")
    p_cp.add_argument("--description")
    p_cp.add_argument("--historical-period", default="明治")
    p_cp.add_argument("--copyright-policy",
                      default="原典はパブリックドメイン(夏目漱石・没1916年)")
    p_cp.set_defaults(func=cmd_create_profile)
    p_gc = sub.add_parser("gen-cards", help="創作カード候補を生成する(draft)")
    p_gc.add_argument("--profile-id", default=YUME_PROFILE_ID)
    p_gc.set_defaults(func=cmd_gen_cards)
    p_dc = sub.add_parser("gen-device-catalog",
                          help="原作の章ごとの装置カタログを作る(続編生成の防具)")
    p_dc.add_argument("--source-id", default="AOZORA_000799")
    p_dc.add_argument("--title", default="夢十夜")
    p_dc.set_defaults(func=cmd_gen_device_catalog)
    p_cc = sub.add_parser("classify-cards",
                          help="カードを移植テストで装置/作風に判定する")
    p_cc.add_argument("--profile-id", default=YUME_PROFILE_ID)
    p_cc.set_defaults(func=cmd_classify_cards)
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
    sub.add_parser("gen-thought-cards", help="思想カード候補を生成する").set_defaults(
        func=cmd_gen_thought_cards)
    p_at = sub.add_parser("approve-thought", help="思想カードを承認する")
    p_at.add_argument("card_ids", nargs="+")
    p_at.add_argument("--by", default="cli")
    p_at.set_defaults(func=cmd_approve_thought)
    sub.add_parser(
        "gen-questions", help="承認済み思想カードの代表質問を生成する"
    ).set_defaults(func=cmd_gen_questions)
    sub.add_parser("gen-rules", help="判断規則 + Bridge Rule 候補を生成する").set_defaults(
        func=cmd_gen_rules)
    p_ar = sub.add_parser("approve-rule", help="規則を承認する")
    p_ar.add_argument("rule_ids", nargs="+")
    p_ar.add_argument("--by", default="cli")
    p_ar.set_defaults(func=cmd_approve_rule)
    p_retag = sub.add_parser("retag", help="Pass2(LLM分類)を未適用チャンクへ適用する")
    p_retag.add_argument("--limit", type=int, help="処理するチャンク数の上限")
    p_retag.add_argument("--source", help="この source_id だけを対象にする")
    p_retag.add_argument("--force", action="store_true",
                         help="分類済みでも付け直す(--source 必須。辞書更新後の再実行用)")
    p_retag.set_defaults(func=cmd_retag)
    sub.add_parser("review-tags", help="Pass4 レビュー待ちを出す").set_defaults(
        func=cmd_review_tags)
    p_tr = sub.add_parser("tag-review", help="レビューを終える")
    p_tr.add_argument("chunk_id")
    p_tr.add_argument("--by", default="cli")
    p_tr.add_argument("--set", action="append", help="speaker_role=author_direct の形")
    p_tr.set_defaults(func=cmd_tag_review)
    sub.add_parser("report", help="コーパスの状態と品質を出す").set_defaults(func=cmd_report)
    p_snap = sub.add_parser("snapshot", help="corpus snapshot を出す/照合する")
    p_snap.add_argument("--out", help="スナップショットを書き出すパス")
    p_snap.add_argument("--compare", help="照合するスナップショットのパス")
    p_snap.set_defaults(func=cmd_snapshot)

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
