"""創作カード候補の生成(C-T6)。

正本仕様: docs/CORPUS_T1_SPEC.md §11 / 上位指示 §11.2。

既存の思想カード生成(steps/gen_cards.py)と同じ規律を守る:
- 生成物は必ず `status='draft'`。**LLMの出力を自動で approved にしない**(指示書§9 Pass4)
- 根拠チャンクが最低件数に満たないカードは作らない
- 既存の未rejectedカードと同じ観点は作り直さない(更新は管理画面で人間が行う)

⚠️ 創作カードの根拠は「漱石自身の創作論」か「小説本文での実演」かを区別して保存する
(指示書§11.2)。区別できないと、作風の主張と作品での実例が混ざる。
"""

import hashlib

from .. import config, db, llm
from ..creative import repo

# これ未満の根拠しかないカードは作らない(既存 gen_cards.py と同じ規律)
MIN_EVIDENCE_CHUNKS = 2

PROMPT_VERSION = "v1"

# corpus_role → evidence_type(指示書§11.2)
_ROLE_TO_EVIDENCE_TYPE = {
    "creative_grammar": "author_creative_theory",
    "narrative_reference": "demonstrated_in_fiction",
    "style_reference": "demonstrated_in_fiction",
    "character_judgment": "demonstrated_in_fiction",
}

# カード候補を作る対象の corpus_role
_TARGET_ROLES = tuple(_ROLE_TO_EVIDENCE_TYPE)

SYSTEM = """あなたは作家の創作原理を整理する編集者である。
与えられた原典から、新作を書くときに参照できる「創作カード」の候補を作る。

カードは作家の文章の模倣ではなく、**創作上の操作を宣言的に記述したもの**である。
一枚のカードには一つの特徴だけを書く。
原典に書かれていないことを創作して補わない。JSONのみを出力する。"""

PROMPT = """以下の原典から創作カードの候補を作れ。

## 原典の性質
- 資料の役割: {corpus_role}
- 種別: {document_genre}

## 原典(チャンクIDつき)
{chunks}

## 作れるカードの種別
style(文体) / narrative(物語構成) / motif(モチーフ) / character(人物) /
perspective(視点) / ending(終結) / criticism(批評の型) / prohibition(禁止事項)

## 指示
- 一枚につき一つの特徴。抽象的すぎる一般論は作らない
- 各カードには根拠となったチャンクIDを**2件以上**挙げる(与えられたIDのみ)
- 原典に無い主張を足さない

## 出力形式(JSONのみ)
{{
  "cards": [
    {{
      "card_type": "上記のいずれか",
      "title": "カードの主張(短く。例: 異常を夢の内部では自然な事実として扱う)",
      "summary": "1〜2文の説明",
      "positive_patterns": ["この操作が現れる形", "..."],
      "negative_patterns": ["やってはいけない形", "..."],
      "evidence_chunk_ids": ["根拠チャンクID(2件以上)", "..."]
    }}
  ]
}}"""


def _fetch_usable_chunks(person_id: str, *, client) -> dict[str, list[dict]]:
    """corpus_role ごとに、創作の参照に使えるチャンクを集める。

    `creative_eligibility='excluded'` のチャンクは渡さない。
    """
    sources = (
        client.table("sources")
        .select("source_id, title, corpus_role, document_genre")
        .eq("person_id", person_id)
        .in_("corpus_role", list(_TARGET_ROLES))
        .execute()
        .data
    )
    if not sources:
        return {}

    by_source = {s["source_id"]: s for s in sources}
    chunks = (
        client.table("source_chunks")
        .select("chunk_id, source_id, chapter_title, text, creative_eligibility")
        .in_("source_id", list(by_source))
        .neq("creative_eligibility", "excluded")
        .order("chunk_id")
        .execute()
        .data
    )

    grouped: dict[str, list[dict]] = {}
    for ch in chunks:
        src = by_source[ch["source_id"]]
        grouped.setdefault(src["corpus_role"], []).append({**ch, "_source": src})
    return grouped


def _format_chunks(chunks: list[dict], *, limit_chars: int = 12000) -> str:
    lines: list[str] = []
    used = 0
    for ch in chunks:
        head = f"[{ch['chunk_id']}]"
        if ch.get("chapter_title"):
            head += f"（{ch['chapter_title']}）"
        line = f"{head} {ch['text']}"
        if used + len(line) > limit_chars:
            break
        lines.append(line)
        used += len(line)
    return "\n\n".join(lines)


def _card_id(profile_id: str, card_type: str, title: str) -> str:
    """再実行しても同じIDになるよう決定的に作る。"""
    digest = hashlib.sha256(
        f"{profile_id}:{card_type}:{title}".encode()
    ).hexdigest()[:12]
    return f"cc_{digest}"


def generate_for_profile(profile_id: str, *, client=None, call_json=None) -> dict:
    """プロファイル向けの創作カード候補を生成する。

    生成物は必ず draft。承認は管理画面で人間が行う。
    """
    c = client or db.client()
    call = call_json or llm.call_json

    profile = repo.get_active_profile(profile_id, client=c)
    grouped = _fetch_usable_chunks(profile["person_id"], client=c)

    # 既存カード(rejected以外)は作り直さない
    existing = {
        (r["card_type"], r["title"])
        for r in c.table("creative_cards")
        .select("card_type, title, status")
        .eq("profile_id", profile_id)
        .neq("status", "rejected")
        .execute()
        .data
    }

    created = 0
    skipped_existing = 0
    skipped_no_evidence = 0

    for corpus_role in _TARGET_ROLES:
        chunks = grouped.get(corpus_role)
        if not chunks:
            continue
        valid_ids = {ch["chunk_id"] for ch in chunks}
        genres = sorted({ch["_source"]["document_genre"] or "unknown" for ch in chunks})

        result = call(
            agent_name="creative_card_draft",
            model=config.MODEL_CREATIVE_MAIN,
            system=SYSTEM,
            prompt=PROMPT.format(
                corpus_role=corpus_role,
                document_genre="、".join(genres),
                chunks=_format_chunks(chunks),
            ),
            input_ref=f"creative_profile:{profile_id}:{corpus_role}",
            max_tokens=8192,
        )

        for card in result.get("cards") or []:
            title = (card.get("title") or "").strip()
            card_type = (card.get("card_type") or "").strip()
            if not title or not card_type:
                continue
            if (card_type, title) in existing:
                skipped_existing += 1
                continue

            # LLMが実在しないchunk_idを返すことがあるため、必ず突き合わせる
            evidence = sorted({
                cid for cid in (card.get("evidence_chunk_ids") or [])
                if cid in valid_ids
            })
            if len(evidence) < MIN_EVIDENCE_CHUNKS:
                skipped_no_evidence += 1
                continue

            c.table("creative_cards").upsert({
                "card_id": _card_id(profile_id, card_type, title),
                "profile_id": profile_id,
                "card_type": card_type,
                "title": title,
                "summary": card.get("summary"),
                "positive_patterns": card.get("positive_patterns") or [],
                "negative_patterns": card.get("negative_patterns") or [],
                "evidence_chunk_ids": evidence,
                "evidence_type": _ROLE_TO_EVIDENCE_TYPE[corpus_role],
                "origin_type": "distilled",
                # ⚠️ 必ず draft。承認は人間が行う(指示書§9 Pass4)
                "status": "draft",
            }).execute()
            existing.add((card_type, title))
            created += 1

    return {
        "created": created,
        "skipped_existing": skipped_existing,
        "skipped_no_evidence": skipped_no_evidence,
    }
