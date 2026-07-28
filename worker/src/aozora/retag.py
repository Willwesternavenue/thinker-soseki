"""Pass2 の適用と Pass4 レビューキュー(C-T4b)。

正本仕様: docs/CORPUS_T1_SPEC.md §4・§11。

取り込み(`ingest.py`)とは**別のステップ**にしてある。理由は2つ:
- 取り込みを LLM 無しで再実行できる状態に保ちたい（snapshot の再現性検証で使う）
- 分類の付け直しだけを独立に回せるようにしたい（`tagger_version` を上げたとき）

⚠️ Pass2 も人手の修正も、**小説を作者の直接発言へ昇格させられない**。
これは指示書の核心（作者と作中人物を混同しない）で、経路を増やすたびに
穴が開きうるため、`tag.merge_pass2` と `resolve_review` の両方で閉じている。
"""

from __future__ import annotations

from datetime import datetime, timezone

from .. import db
from . import characters, tag

# 人手で直してよい項目。eligibility は Pass1 の安全側判定なので含めない
CORRECTABLE_FIELDS = frozenset({
    "speaker_role", "claim_type", "assertion_status", "character_id", "addressee",
})


def _c(client):
    return client if client is not None else db.client()


def _sources(client) -> dict[str, dict]:
    rows = (
        _c(client).table("sources")
        .select("source_id, title, document_genre, corpus_role")
        .eq("source_provider", "aozora").execute().data or []
    )
    return {r["source_id"]: r for r in rows}


def retag_pending(
    *, client=None, call_json=None, limit: int | None = None, job_id: str | None = None
) -> dict:
    """Pass2 が未適用のチャンクを分類する。

    対象は `tagger_version` が現行版でないもの。文書ごとにまとめて分類する
    （文書種別と役割が分類の前提になるため、混ぜて投げない）。
    """
    c = _c(client)
    sources = _sources(c)
    if not sources:
        return {"updated": 0, "needs_review": 0}

    # ⚠️ PostgREST は1リクエスト最大1000行しか返さない(ローカル既定)。1回の取得で
    # 全件が来る前提にすると、実データ9,669件の retag が**1000件で黙って止まる**
    # (実測)。処理済み(現行版)は絞り込みから抜けるので、空になるまで取得を繰り返す。
    updated = review_count = 0
    while True:
        q = (
            c.table("source_chunks")
            .select("chunk_id, source_id, text, chunk_type")
            .in_("source_id", sorted(sources))
            .neq("tagger_version", tag.TAGGER_VERSION)
            # 人手レビューの結論(reviewed/corrected)は再分類で上書きしない。
            # LLMの再実行が人の判断を黙って覆すと、レビューという関門の意味が無くなる
            .not_.in_("tag_review_status", ["reviewed", "corrected"])
            .order("chunk_id")
        )
        if limit:
            remaining_quota = limit - updated
            if remaining_quota <= 0:
                break
            q = q.limit(remaining_quota)
        pending = q.execute().data or []
        if not pending:
            break

        round_updated, round_review = _retag_round(
            c, pending, sources, call_json=call_json, job_id=job_id
        )
        updated += round_updated
        review_count += round_review
        # 1件も進まなければ打ち切る(同じ集合を無限に回さない)
        if round_updated == 0:
            break

    return {"updated": updated, "needs_review": review_count}


def _retag_round(
    c, pending: list[dict], sources: dict[str, dict], *, call_json, job_id
) -> tuple[int, int]:
    """取得済みの1回ぶん(最大1000件)を分類して書き込む。"""
    by_source: dict[str, list[dict]] = {}
    for ck in pending:
        by_source.setdefault(ck["source_id"], []).append(ck)

    updated = review_count = 0
    for source_id, chunks in by_source.items():
        src = sources[source_id]
        genre = src["document_genre"] or "other"
        # 作品の人物一覧(語彙)。辞書に載らない作品(夢十夜など)は空 = 常に null
        roster = characters.roster_for_work(src.get("title") or "")
        tagged = tag.classify_chunks(
            chunks,
            document_genre=genre,
            corpus_role=src["corpus_role"],
            characters=roster,
            call_json=call_json,
            job_id=job_id,
        )
        for chunk_id, tags in tagged.items():
            # Pass3: 決定的metadataと矛盾していないか
            issues = tag.check_consistency(
                tags, document_genre=genre, corpus_role=src["corpus_role"]
            )
            needs = tag.needs_review(tags, issues)
            reasons = [r for r in (tags.get("classification_reason"), *issues) if r]
            c.table("source_chunks").update({
                "speaker_role": tags["speaker_role"],
                "character_id": tags.get("character_id"),
                "claim_type": tags["claim_type"],
                "assertion_status": tags["assertion_status"],
                "thought_eligibility": tags["thought_eligibility"],
                "creative_eligibility": tags["creative_eligibility"],
                "is_quotation": tags["is_quotation"],
                "is_hypothetical": tags.get("is_hypothetical", False),
                "is_ironic": tags.get("is_ironic", False),
                # verbatim: 本人の言葉そのものか。語り手・人物・引用は本人ではない
                "verbatim": tags["speaker_role"] == "author_direct",
                "tag_confidence": tags["tag_confidence"],
                "classification_reason": "; ".join(reasons) or None,
                "tag_review_status": "needs_review" if needs else "auto_ok",
                "tagger_version": tag.TAGGER_VERSION,
            }).eq("chunk_id", chunk_id).execute()
            updated += 1
            review_count += 1 if needs else 0

    return updated, review_count


def review_queue(*, client=None, limit: int = 200) -> list[dict]:
    """Pass4 のレビュー待ち。確信度の低い順に出す(危ういものから見る)。"""
    c = _c(client)
    return (
        c.table("source_chunks")
        .select(
            "chunk_id, source_id, text, speaker_role, claim_type, assertion_status,"
            " thought_eligibility, tag_confidence, classification_reason"
        )
        .eq("tag_review_status", "needs_review")
        .order("tag_confidence")
        .order("chunk_id")
        .limit(limit)
        .execute()
        .data
        or []
    )


def resolve_review(
    chunk_id: str, *, reviewed_by: str, corrections: dict | None = None, client=None
) -> dict:
    """レビューを終える。値を直した場合は `corrected`、追認なら `reviewed`。

    ⚠️ 人手であっても小説のチャンクを `author_direct` にはできない。
    レビュー画面からの操作は「LLMの誤りを直す」ためのもので、
    作者と作中人物の区別そのものを覆す手段ではない。
    """
    c = _c(client)
    rows = (
        c.table("source_chunks")
        .select("chunk_id, source_id, speaker_role")
        .eq("chunk_id", chunk_id).execute().data or []
    )
    chunk = rows[0] if rows else None
    if not chunk:
        return {"error": f"チャンク {chunk_id} が見つかりません"}

    src = _sources(c).get(chunk["source_id"], {})
    genre = src.get("document_genre") or "other"
    corrections = {
        k: v for k, v in (corrections or {}).items() if k in CORRECTABLE_FIELDS
    }

    # character_id は語彙(その作品の人物一覧)の中からしか付けられない。
    # 人手であっても一覧の外のIDを許すと、質問側の検出と結合できない値が混ざる
    if corrections.get("character_id"):
        roster_ids = {
            entry["character_id"]
            for entry in characters.roster_for_work(src.get("title") or "")
        }
        if corrections["character_id"] not in roster_ids:
            return {
                "error": (
                    f"{corrections['character_id']} はこの作品の登場人物一覧に"
                    "ありません。辞書(characters.json)に追加してから修正してください"
                )
            }

    if (
        genre in tag.FICTION_GENRES
        and corrections.get("speaker_role") not in (None, *tag.FICTION_SPEAKER_ROLES)
    ):
        return {
            "error": (
                f"{chunk_id} は小説({genre})のチャンクです。"
                "作者の直接発言には変更できません"
            )
        }

    update = {
        **corrections,
        "tag_review_status": "corrected" if corrections else "reviewed",
        "tag_reviewed_by": reviewed_by,
        "tag_reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    if "speaker_role" in corrections:
        update["verbatim"] = corrections["speaker_role"] == "author_direct"
    c.table("source_chunks").update(update).eq("chunk_id", chunk_id).execute()
    return {"chunk_id": chunk_id, "status": update["tag_review_status"]}
