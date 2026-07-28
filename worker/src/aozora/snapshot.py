"""corpus snapshot とデータ品質レポート(C-T8)。

仕様 docs/CORPUS_T1_SPEC.md の受入条件:
- #18 corpus snapshot を再現できる → `build_snapshot`
- #20 データ品質レポートを出力できる → `build_quality_report`

**snapshot は決定的**でなければならない。取り込みを再現できたかを digest の一致で
判定するのが目的なので、時刻・UUID・DBの返却順など「同じ内容でも変わる値」を
一切含めない。含めてしまうと digest が毎回変わり、照合の役に立たなくなる。
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter

from .. import db
from . import paged

PERSON_ID = "natsume_soseki"
PROVIDER = "aozora"

SNAPSHOT_VERSION = "corpus_snapshot_v1"

# 文字化けの痕跡。CP932→UTF-8 の失敗は置換文字に、青空文庫の外字は 〓 になる。
GARBLING_MARKERS = ("�", "〓")

# 小説本文が思想の中核Indexに入っていないかを見るための文書種別。
FICTION_GENRES = ("novel", "short_story", "sketch")

CHECK_NAMES = (
    "garbling_ratio",
    "duplicate_ratio",
    "unclassified_speaker_role_ratio",
    "fiction_in_core_thought",
    "sources_without_url",
    "editions_without_hash",
    "editions_without_parser_version",
    "chunks_without_embedding",
    "cards_with_missing_evidence",
    "known_novel_misclassified",
    "open_review_queue",
)


def _c(client):
    return client if client is not None else db.client()


def _fetch(client, table, columns, **filters):
    """全行を取得する(ページング必須。paged.py 参照)。"""
    def build():
        q = _c(client).table(table).select(columns)
        for key, value in filters.items():
            q = q.eq(key, value)
        return q.order(_ORDER_KEYS.get(table, "created_at"))

    return paged.fetch_all(build)


# ページングの順序キー。順序が安定しないと、ページ境界で行が重複・欠落する
_ORDER_KEYS = {
    "canonical_works": "canonical_work_id",
    "work_editions": "edition_id",
    "sources": "source_id",
    "source_chunks": "chunk_id",
    "creative_cards": "card_id",
    "canonical_work_review_queue": "queue_id",
}


# ── snapshot ──


def build_snapshot(person_id: str = PERSON_ID, *, client=None) -> dict:
    """取り込み結果の決定的なスナップショット。

    `digest` は本体(digest 以外の全部)の正規化JSONの sha256。空のDBから
    CLI を流し直して同じ digest になれば、取り込みが再現できたと言える。
    """
    works = _fetch(client, "canonical_works",
                   "canonical_work_id,canonical_title,canonical_title_reading",
                   person_id=person_id)
    editions = _fetch(client, "work_editions",
                      "edition_id,canonical_work_id,aozora_work_id,orthography,"
                      "work_status,is_primary_retrieval_edition,content_sha256,parser_version")
    sources = _fetch(client, "sources",
                     "source_id,title,corpus_role,document_genre,authority_level,edition_id",
                     person_id=person_id, source_provider=PROVIDER)
    chunks = _fetch(client, "source_chunks",
                    "chunk_id,source_id,chunk_hash,chunker_version,speaker_role,"
                    "thought_eligibility,creative_eligibility",
                    person_id=person_id)

    # 版は作品に紐づくものだけに絞る(他人物のデータが混ざらないように)
    work_ids = {w["canonical_work_id"] for w in works}
    editions = [e for e in editions if e["canonical_work_id"] in work_ids]

    chunks_by_source: dict[str, list[dict]] = {}
    for ch in chunks:
        chunks_by_source.setdefault(ch["source_id"], []).append(ch)

    body = {
        "snapshot_version": SNAPSHOT_VERSION,
        "person_id": person_id,
        "counts": {
            "canonical_works": len(works),
            "work_editions": len(editions),
            "sources": len(sources),
            "chunks": len(chunks),
        },
        "versions": {
            "parser": _counter(e["parser_version"] for e in editions),
            "chunker": _counter(ch["chunker_version"] for ch in chunks),
        },
        "works": sorted(
            (
                {
                    "canonical_work_id": w["canonical_work_id"],
                    "canonical_title": w["canonical_title"],
                    "canonical_title_reading": w["canonical_title_reading"],
                }
                for w in works
            ),
            key=lambda w: w["canonical_work_id"],
        ),
        "editions": sorted(
            (
                {
                    "edition_id": e["edition_id"],
                    "canonical_work_id": e["canonical_work_id"],
                    "aozora_work_id": e["aozora_work_id"],
                    "orthography": e["orthography"],
                    "work_status": e["work_status"],
                    "is_primary_retrieval_edition": e["is_primary_retrieval_edition"],
                    "content_sha256": e["content_sha256"],
                    "parser_version": e["parser_version"],
                }
                for e in editions
            ),
            key=lambda e: e["edition_id"],
        ),
        "sources": sorted(
            (
                {
                    "source_id": s["source_id"],
                    "title": s["title"],
                    "corpus_role": s["corpus_role"],
                    "document_genre": s["document_genre"],
                    "authority_level": s["authority_level"],
                    "edition_id": s["edition_id"],
                    "chunk_count": len(chunks_by_source.get(s["source_id"], [])),
                    # 本文そのものは載せず、チャンクの hash をまとめた指紋にする
                    # (snapshot を巨大にせず、内容の差分は検出できる)
                    "chunks_fingerprint": _fingerprint(
                        chunks_by_source.get(s["source_id"], [])
                    ),
                }
                for s in sources
            ),
            key=lambda s: s["source_id"],
        ),
    }
    return {**body, "digest": _digest(body)}


def _counter(values) -> dict:
    """None を "unknown" にまとめた、キー順の安定した集計。"""
    counted = Counter(v if v is not None else "unknown" for v in values)
    return dict(sorted(counted.items()))


def _fingerprint(chunks: list[dict]) -> str:
    """1文書ぶんのチャンクの指紋。chunk_id 順に固定してから hash する。

    ⚠️ **Pass2(LLM分類)の結果は含めない**。speaker_role / claim_type などは
    LLM が付けるので毎回同じ値になる保証がなく、digest に混ぜると
    「取り込みを再現できたか」の判定に使えなくなる（同じ取り込みでも不一致になる）。
    ここで見るのは取り込みの再現性 — どの本文をどう分割したか — に限る。
    タグの状態は `build_quality_report` と Pass4 レビューキューで見る。
    """
    if not chunks:
        return ""
    parts = sorted(f"{ch['chunk_id']}\t{ch['chunk_hash']}" for ch in chunks)
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def _digest(body: dict) -> str:
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def compare_snapshots(old: dict, new: dict) -> dict:
    """2つのスナップショットの差分。取り込みを再現できたかの照合に使う。

    digest が一致すれば同一。違ったときに「どこが違うか」を出すのが本体で、
    文書の増減と、同じ文書の中身の変化(指紋の相違)を分けて示す。
    """
    old_by_id = {s["source_id"]: s for s in old["sources"]}
    new_by_id = {s["source_id"]: s for s in new["sources"]}

    counts = {
        key: {"old": old["counts"][key], "new": new["counts"][key]}
        for key in sorted(set(old["counts"]) | set(new["counts"]))
        if old["counts"].get(key) != new["counts"].get(key)
    }
    return {
        "same": old["digest"] == new["digest"],
        "counts": counts,
        "sources_added": sorted(set(new_by_id) - set(old_by_id)),
        "sources_removed": sorted(set(old_by_id) - set(new_by_id)),
        "sources_changed": sorted(
            sid
            for sid in set(old_by_id) & set(new_by_id)
            if old_by_id[sid]["chunks_fingerprint"] != new_by_id[sid]["chunks_fingerprint"]
        ),
    }


# ── data quality report(指示書§14.6) ──


def build_quality_report(person_id: str = PERSON_ID, *, client=None) -> dict:
    """§14.6 の指標を判定する。通った項目も残す(何を見たかが分かるように)。"""
    sources = _fetch(client, "sources",
                     "source_id,title,corpus_role,document_genre,source_url,edition_id",
                     person_id=person_id, source_provider=PROVIDER)
    source_ids = {s["source_id"] for s in sources}
    chunks = [
        ch for ch in _fetch(client, "source_chunks",
                            "chunk_id,source_id,text,chunk_hash,speaker_role,"
                            "thought_eligibility,embedding", person_id=person_id)
        if ch["source_id"] in source_ids
    ]
    edition_ids = {s["edition_id"] for s in sources if s["edition_id"]}
    editions = [
        e for e in _fetch(client, "work_editions",
                          "edition_id,content_sha256,parser_version")
        if e["edition_id"] in edition_ids
    ]

    checks = [
        _ratio_check("garbling_ratio", "文字化けを含むチャンクの割合",
                     [ch for ch in chunks if _is_garbled(ch["text"])], chunks, 0.0),
        _ratio_check("duplicate_ratio", "chunk_hash が重複するチャンクの割合",
                     _duplicates(chunks), chunks, 0.0),
        _ratio_check("unclassified_speaker_role_ratio", "speaker_role 未分類の割合",
                     [ch for ch in chunks if not ch["speaker_role"]], chunks, 0.0),
        _count_check("fiction_in_core_thought",
                     "思想の中核Indexに入っている小説由来チャンク",
                     _fiction_in_core(sources, chunks)),
        _count_check("sources_without_url", "source_url が無い文書",
                     [s["source_id"] for s in sources if not s["source_url"]]),
        _count_check("editions_without_hash", "content_sha256 が無い版",
                     [e["edition_id"] for e in editions if not e["content_sha256"]]),
        _count_check("editions_without_parser_version", "parser_version が無い版",
                     [e["edition_id"] for e in editions if not e["parser_version"]]),
        _count_check("chunks_without_embedding", "embedding 未生成のチャンク",
                     [ch["chunk_id"] for ch in chunks if ch["embedding"] is None]),
        _count_check("cards_with_missing_evidence",
                     "根拠チャンクが実在しない承認済み創作カード",
                     _cards_with_missing_evidence(client)),
        _count_check("known_novel_misclassified",
                     "思想系の役割になっている既知の小説",
                     _known_novels_misclassified(sources)),
        _count_check("open_review_queue", "未解決の作品同定キュー",
                     _open_review_queue(client, person_id)),
    ]
    return {
        "person_id": person_id,
        "passed": all(c["passed"] for c in checks),
        "checks": checks,
    }


def _is_garbled(text: str | None) -> bool:
    return bool(text) and any(m in text for m in GARBLING_MARKERS)


# これ未満の本文は重複検査の対象外。小説では「何ですって」のような短い台詞が
# 正当に繰り返される(Phase C 実測: 重複33件はすべて短い台詞と章番号)。
# この検査の目的は取り込みミス(同じ段落の二重投入)の検出であって、
# 本文の反復表現の検出ではない。
DUPLICATE_MIN_CHARS = 30


def _duplicates(chunks: list[dict]) -> list[dict]:
    """同じ chunk_hash を持つ十分な長さのチャンク(1件目も含めて重複扱い)。"""
    long_enough = [
        ch for ch in chunks
        if ch["chunk_hash"] and len(ch.get("text") or "") >= DUPLICATE_MIN_CHARS
    ]
    counted = Counter(ch["chunk_hash"] for ch in long_enough)
    return [ch for ch in long_enough if counted[ch["chunk_hash"]] > 1]


def _fiction_in_core(sources: list[dict], chunks: list[dict]) -> list[str]:
    """core_thought の文書のうち小説由来のチャンク。

    仕様の最重要不変条件(小説中の発言を作者の思想として扱わない)を、
    データ側から機械的に確かめる。`excluded` は Index から外れているので対象外。
    """
    by_id = {s["source_id"]: s for s in sources}
    return [
        ch["chunk_id"]
        for ch in chunks
        if (src := by_id.get(ch["source_id"]))
        and src["corpus_role"] == "core_thought"
        and src["document_genre"] in FICTION_GENRES
        and ch["thought_eligibility"] != "excluded"
    ]


def _cards_with_missing_evidence(client) -> list[str]:
    """承認済みカードの evidence_chunk_ids が実在するか(§14.5)。

    draft/rejected は生成に使われないので対象外。原典を取り込み直したときに
    approved のまま根拠だけ消えている状態を見つけるのが目的。
    """
    cards = _fetch(client, "creative_cards", "card_id,evidence_chunk_ids", status="approved")
    wanted = {cid for c in cards for cid in (c["evidence_chunk_ids"] or [])}
    if not wanted:
        return []
    found = {
        row["chunk_id"]
        for row in _c(client).table("source_chunks")
        .select("chunk_id").in_("chunk_id", sorted(wanted)).execute().data or []
    }
    return sorted(
        c["card_id"]
        for c in cards
        if not set(c["evidence_chunk_ids"] or []) <= found
    )


def _known_novels_misclassified(sources: list[dict]) -> list[str]:
    """既知の小説が思想系の corpus_role に入っていないか(表題起点の検査)。

    実データで三四郎の NDC が空 → genre=other → supporting_thought に落ちた。
    genre 起点の fiction_in_core_thought はこの形の混入を見えない。
    """
    from . import tag

    thought_roles = {"core_thought", "supporting_thought"}
    return [
        s["source_id"]
        for s in sources
        if s["title"] in tag._KNOWN_NOVELS and s["corpus_role"] in thought_roles
    ]


def _open_review_queue(client, person_id: str) -> list[str]:
    rows = _fetch(client, "canonical_work_review_queue", "queue_id,aozora_work_ids",
                  person_id=person_id, status="open")
    return sorted(",".join(r["aozora_work_ids"]) for r in rows)


def _ratio_check(name, label, hits: list, total: list, threshold: float) -> dict:
    value = len(hits) / len(total) if total else 0.0
    return {
        "name": name,
        "label": label,
        "value": value,
        "threshold": threshold,
        "passed": value <= threshold,
        "detail": sorted(ch["chunk_id"] for ch in hits)[:20],
    }


def _count_check(name, label, hits: list[str]) -> dict:
    return {
        "name": name,
        "label": label,
        "value": len(hits),
        "threshold": 0,
        "passed": not hits,
        "detail": sorted(hits)[:20],
    }
