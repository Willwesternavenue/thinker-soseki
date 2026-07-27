"""青空文庫 ingestion(C-T5)。

edition → 本文取得 → 正規化 → チャンク化 → タグ付け → sources/source_chunks 投入。
正本仕様: docs/CORPUS_T1_SPEC.md §7。

⚠️ 取得は **GitHub公式ミラー**経由にする。aozora.gr.jp へ機械的に連続アクセス
しない(docs/AOZORA_INGESTION.md §2)。
"""

import io
import re
import urllib.request
import zipfile
from datetime import datetime, timezone

from .. import db
from ..steps import embed as embed_step
from . import chunk as chunk_mod
from . import parse, tag

# GitHub公式ミラー。青空文庫はサイト全体をここにミラーしている
MIRROR_BASE = "https://raw.githubusercontent.com/aozorabunko/aozorabunko/master/"

# 文字化け率がこれを超えたら Index 登録しない(指示書§8.2)
MAX_GARBLING_RATIO = 0.01

# genre → チャンカーに渡す source_type
_NOVEL_GENRES = ("novel", "short_story", "sketch")

_AOZORA_PATH_RE = re.compile(r"aozora\.gr\.jp/(cards/.+)$")


def mirror_url(aozora_url: str) -> str:
    """aozora.gr.jp のURLを GitHub ミラーのURLへ変換する。"""
    m = _AOZORA_PATH_RE.search(aozora_url)
    if not m:
        raise ValueError(f"青空文庫のURLとして解釈できません: {aozora_url}")
    return MIRROR_BASE + m.group(1)


def default_fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def extract_text_from_zip(data: bytes) -> tuple[str, float]:
    """zipから本文を取り出しUTF-8へ変換する。文字化け率も返す。"""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".txt")]
        if not names:
            raise ValueError("zip内にテキストファイルが見つかりません")
        raw = z.read(names[0])
    return parse.decode_aozora_bytes(raw, with_ratio=True)


def _source_id(edition_id: str) -> str:
    return f"AOZORA_{edition_id}"


def ingest_edition(edition_id: str, *, client=None, fetch=None) -> dict:
    """1つの版を取り込む。

    作業中の版・本文ファイルが無い版は取り込まない(指示書§2.1 / §1.3)。
    source_id と chunk_id は決定的なので、再実行しても重複しない。
    """
    c = client or db.client()
    fetch = fetch or default_fetch

    edition = (
        c.table("work_editions").select("*").eq("edition_id", edition_id)
        .single().execute().data
    )
    if edition["work_status"] != "published":
        raise ValueError(
            f"作業中の版は取り込まない(edition_id={edition_id})。"
            "記録は aozora_manifest_entries のみに残す(指示書§2.1)"
        )
    if not edition.get("text_file_url"):
        raise ValueError(f"テキストファイルが存在しない版です(edition_id={edition_id})")

    work = (
        c.table("canonical_works").select("*")
        .eq("canonical_work_id", edition["canonical_work_id"]).single().execute().data
    )

    data = fetch(mirror_url(edition["text_file_url"]))
    text, garbling = extract_text_from_zip(data)
    if garbling > MAX_GARBLING_RATIO:
        raise ValueError(
            f"文字化け率が閾値を超えたためIndex登録しません"
            f"({garbling:.4f} > {MAX_GARBLING_RATIO})"
        )

    doc = parse.normalize_document(text)

    # Pass1: 文書単位の決定的タグ
    genre = tag.infer_document_genre(title=work["canonical_title"], ndc=work.get("ndc"))
    corpus_role = tag.default_corpus_role(genre)
    authority = tag.default_authority_level(genre)

    source_id = _source_id(edition_id)
    c.table("sources").upsert({
        "source_id": source_id,
        "person_id": work["person_id"],
        "title": work["canonical_title"],
        # 既存の source_type は必須項目。genre と対応付ける
        "source_type": "book" if genre in _NOVEL_GENRES else "essay",
        "author": doc["author"] or "夏目漱石",
        "source_url": edition.get("card_url"),
        "edition_id": edition_id,
        "corpus_role": corpus_role,
        "document_genre": genre,
        "authority_level": authority,
        "source_provider": "aozora",
        "corpus_metadata": {
            "colophon": doc["colophon"],
            "aozora_work_id": edition["aozora_work_id"],
            "orthography": edition["orthography"],
            "ruby_count": len(doc["rubies"]),
            "note_count": len(doc["notes"]),
            "garbling_ratio": garbling,
        },
        "status": "active",
    }).execute()

    # 章 → チャンク。本文はルビ・注記を外したものを使う(検索・embedding用)
    chapters = []
    for ch in parse.split_chapters(doc["raw_text"]):
        body, _ = parse.extract_notes(parse.extract_ruby(ch["text"])[0])
        chapters.append({"chapter_title": ch["chapter_title"], "text": body})

    source_type = genre if genre in _NOVEL_GENRES else "lecture"
    chunks = chunk_mod.chunk_document(source_id, chapters, source_type=source_type)

    for ck in chunks:
        tags = tag.deterministic_chunk_tags(ck, document_genre=genre)
        issues = tag.check_consistency(
            tags, document_genre=genre, corpus_role=corpus_role
        )
        c.table("source_chunks").upsert({
            "chunk_id": ck["chunk_id"],
            "source_id": source_id,
            "person_id": work["person_id"],
            "chapter_title": ck["chapter_title"],
            "char_start": ck["char_start"],
            "char_end": ck["char_end"],
            "chunk_type": ck["chunk_type"],
            "text": ck["text"],
            "chunker_version": ck["chunker_version"],
            "chunk_hash": ck["chunk_hash"],
            # verbatim: 本人の言葉そのものか。小説の語り手・人物は本人ではない
            "verbatim": tags["speaker_role"] == "author_direct",
            "speaker_role": tags["speaker_role"],
            "claim_type": tags["claim_type"],
            "assertion_status": tags["assertion_status"],
            "thought_eligibility": tags["thought_eligibility"],
            "creative_eligibility": tags["creative_eligibility"],
            "is_quotation": tags["is_quotation"],
            "tagger_version": tag.PASS1_VERSION,
            "tag_review_status": (
                "needs_review"
                if tag.needs_review({**tags, "tag_confidence": 1.0}, issues)
                else "auto_ok"
            ),
            "classification_reason": "; ".join(issues) or None,
            "status": "active",
        }).execute()

    # 由来情報を版へ記録(再現性のため。指示書§15-19)
    c.table("work_editions").update({
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "content_sha256": doc["content_sha256"],
        "parser_version": doc["parser_version"],
    }).eq("edition_id", edition_id).execute()

    return {
        "source_id": source_id,
        "chunks": len(chunks),
        "genre": genre,
        "corpus_role": corpus_role,
        "garbling_ratio": garbling,
    }


# 1回のDB書き込みでまとめる件数。OpenAI側のバッチはembed_texts内で行う
EMBED_BATCH = 64


def embed_pending_chunks(*, client=None, embed=None, limit: int | None = None) -> int:
    """embedding未生成の青空文庫チャンクを埋める。

    対象は `chunker_version='aozora_v1'` かつ embedding が null のものだけ。
    既存の思想モード(`v1`)のチャンクには触らない。
    何度実行しても対象が尽きれば0を返す(冪等)。
    """
    c = client or db.client()
    embed = embed or embed_step.embed_texts

    total = 0
    while True:
        query = (
            c.table("source_chunks")
            .select("chunk_id, text")
            .eq("chunker_version", chunk_mod.CHUNKER_VERSION)
            .is_("embedding", "null")
            .order("chunk_id")
            .limit(EMBED_BATCH)
        )
        rows = query.execute().data
        if not rows:
            return total

        vectors = embed([r["text"] for r in rows])
        for row, vector in zip(rows, vectors, strict=True):
            c.table("source_chunks").update({"embedding": vector}).eq(
                "chunk_id", row["chunk_id"]
            ).execute()
        total += len(rows)
        if limit is not None and total >= limit:
            return total
