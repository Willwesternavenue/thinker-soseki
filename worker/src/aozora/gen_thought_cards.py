"""思想カード候補の生成(C-T6)。

正本仕様: docs/CORPUS_T1_SPEC.md §5・§11 / 受入#13。

⚠️ **入力は author_thought_core Index に限る**。
corpus_role=core_thought かつ speaker_role=author_direct かつ
thought_eligibility≠excluded のチャンクだけを LLM に渡す。ここを緩めると、
作中人物の言葉が本人の思想カードになり、回答で本人の主張として提示される。

生成物は必ず `status='draft'`。承認は人間が行う(指示書§9 Pass4)。
受入#13 のため、カードには根拠チャンク(`representative_chunk_ids`)と
`thought_evidence_links`(どの原典のどこか)を必ず伴わせる。
"""

import hashlib

from .. import config, db, llm
from . import paged, routing

PERSON_ID = "natsume_soseki"

# これ未満の根拠しかないカードは作らない(既存 gen_creative_cards と同じ規律)
MIN_EVIDENCE_CHUNKS = 2

PROMPT_VERSION = "v1"

# thought_evidence_links.evidence_role の許容値(既存スキーマの check 制約)
EVIDENCE_ROLES = frozenset({
    "definition", "distinction", "prohibition", "example", "application",
    "style", "biographical", "quote", "historical", "metaphor",
})
DEFAULT_EVIDENCE_ROLE = "definition"

SYSTEM = """あなたは思想家の主張を整理する編集者である。
与えられた原典から、本人の思想を答えるときに参照する「思想カード」の候補を作る。

カードは本人の文章の要約ではなく、**判断の型を宣言的に記述したもの**である。
一枚のカードには一つの主張だけを書く。
原典に書かれていないことを創作して補わない。JSONのみを出力する。"""

PROMPT = """以下の原典から思想カードの候補を作れ。

## 原典(チャンクIDつき)
{chunks}

## 指示
- 一枚につき一つの主張。抽象的すぎる一般論は作らない
- `title` は**概念ラベル**（短い名詞句・体言止め）。命題文にしない。
  命題は `core_claim` に書く — 両者の役割は分ける
  （例: title「内発的開化」/ core_claim「開化は内発的でなければならない」）。
  原典が論説・講演だと題が命題文に流れやすいので特に注意する
- `thought_id` は主張を表す英小文字スネークケースの短い識別子
- `distinctions` は「何ではなく何か」の区別。本人の主張の輪郭を決める。
  `core_claim` の言い換えを書かない — それは区別を1つも足さない。
  取り違えると結論が変わる**具体的な二項**を名指しする
  （可: not「価値相対主義」/ but「自分の因果に立てという立脚地の主張」）
- `answer_policy` はこの主張に沿って答えるときの方針
- `prohibitions` はこの主張に反する言い方
- 各カードには根拠を**2件以上**挙げる(与えられたチャンクIDのみ)
- 根拠ごとに役割を付ける:
  definition(定義) / distinction(区別) / prohibition(禁止) / example(例) /
  application(適用) / style(語り口) / biographical(経歴) / quote(引用に適する) /
  historical(時代背景) / metaphor(比喩)
- 原典に無い主張を足さない

## 出力形式(JSONのみ)
{{
  "cards": [
    {{
      "thought_id": "naihatsu_kaika",
      "title": "概念ラベル(短い名詞句。命題文にしない)",
      "core_claim": "1〜2文の中核(命題はここに書く)",
      "distinctions": [{{"not": "…ではなく", "but": "…である"}}],
      "answer_policy": ["答えるときの方針", "..."],
      "prohibitions": ["この主張に反する言い方", "..."],
      "evidence": [{{"chunk_id": "根拠チャンクID", "evidence_role": "definition"}}]
    }}
  ]
}}"""


def _thought_index_filters() -> dict:
    """思想の中核Index の条件。routing の定義を単一の出所にする。"""
    return routing.INDEXES["author_thought_core"]


def fetch_index_chunks(person_id: str = PERSON_ID, *, client=None) -> list[dict]:
    """author_thought_core Index のチャンクだけを返す。

    3条件（corpus_role / speaker_role / thought_eligibility）をすべて満たすもの。
    どれか1つでも欠けたら渡さない — 1層の設定ミスで小説が混ざらないようにする。
    """
    c = client or db.client()
    idx = _thought_index_filters()

    sources = (
        c.table("sources")
        .select("source_id, title, corpus_role, document_genre")
        .eq("person_id", person_id)
        .in_("corpus_role", idx["corpus_roles"])
        .execute()
        .data
        or []
    )
    if not sources:
        return []
    by_source = {s["source_id"]: s for s in sources}

    # 全件取得はページング必須(PostgRESTの1000行上限。paged.py 参照)
    chunks = paged.fetch_all(
        lambda: c.table("source_chunks")
        .select("chunk_id, source_id, chapter_title, text, speaker_role,"
                " thought_eligibility")
        .in_("source_id", sorted(by_source))
        .in_("speaker_role", idx["speaker_roles"])
        .neq("thought_eligibility", "excluded")
        .order("chunk_id")
    )
    return [{**ch, "_source": by_source[ch["source_id"]]} for ch in chunks]


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


def _card_id(person_id: str, thought_id: str) -> str:
    """再実行しても同じIDになるよう決定的に作る。"""
    digest = hashlib.sha256(f"{person_id}:{thought_id}".encode()).hexdigest()[:12]
    return f"tc_{digest}"


def _evidence_roles(card: dict) -> dict[str, str]:
    """根拠チャンクID → 役割。制約に無い値は既定へ倒す(落とさない)。

    旧形式(`evidence_chunk_ids` の文字列配列)も受ける。LLM が形式を守らない回が
    あり、そのたびにカードを捨てると候補が出なくなるため。
    """
    roles: dict[str, str] = {}
    for item in card.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        cid = item.get("chunk_id")
        if not cid:
            continue
        role = item.get("evidence_role")
        roles[cid] = role if role in EVIDENCE_ROLES else DEFAULT_EVIDENCE_ROLE
    for cid in card.get("evidence_chunk_ids") or []:
        roles.setdefault(cid, DEFAULT_EVIDENCE_ROLE)
    return roles


def generate(
    person_id: str = PERSON_ID, *, client=None, call_json=None, job_id: str | None = None
) -> dict:
    """思想カード候補を作る。生成物は必ず draft。"""
    c = client or db.client()
    call = call_json or llm.call_json

    chunks = fetch_index_chunks(person_id, client=c)
    if len(chunks) < MIN_EVIDENCE_CHUNKS:
        return {"created": 0, "skipped_existing": 0, "skipped_no_evidence": 0}

    valid = {ch["chunk_id"]: ch for ch in chunks}

    # 既存カード(rejected 以外)は作り直さない。更新は管理画面で人間が行う
    existing = {
        r["thought_id"]
        for r in c.table("thought_cards")
        .select("thought_id, status").eq("person_id", person_id).execute().data or []
        if r["status"] != "rejected"
    }
    # rejected も含めて「一度作ったもの」は作り直さない
    seen = {
        r["thought_id"]
        for r in c.table("thought_cards")
        .select("thought_id").eq("person_id", person_id).execute().data or []
    }

    # ⚠️ 資料ごとに分けて渡す。全チャンクを1プロンプトに詰めると文字数上限で
    # 先頭の資料しか入らない（実データでは9資料中1つの冒頭からしか出なかった）
    by_source: dict[str, list[dict]] = {}
    for ch in chunks:
        by_source.setdefault(ch["source_id"], []).append(ch)

    created = skipped_existing = skipped_no_evidence = 0
    for source_id in sorted(by_source):
        source_chunks = by_source[source_id]
        if len(source_chunks) < MIN_EVIDENCE_CHUNKS:
            continue
        try:
            response = call(
                agent_name="aozora_gen_thought_cards",
                model=config.MODEL_CARD_DRAFT,
                system=SYSTEM,
                prompt=PROMPT.format(chunks=_format_chunks(source_chunks)),
                input_ref=source_id,
                job_id=job_id,
                max_tokens=8192,
            )
        except Exception:  # noqa: BLE001 - 1資料の失敗で他資料を落とさない
            continue
        created_here, se, sn = _absorb(
            c, response, person_id=person_id, valid=valid, seen=seen, existing=existing
        )
        created += created_here
        skipped_existing += se
        skipped_no_evidence += sn

    return {
        "created": created,
        "skipped_existing": skipped_existing,
        "skipped_no_evidence": skipped_no_evidence,
    }


def _absorb(c, response, *, person_id, valid, seen, existing) -> tuple[int, int, int]:
    """1資料ぶんの応答をカードとして取り込む。"""
    created = skipped_existing = skipped_no_evidence = 0
    for card in (response or {}).get("cards") or []:
        thought_id = (card.get("thought_id") or "").strip()
        title = (card.get("title") or "").strip()
        if not thought_id or not title:
            continue
        if thought_id in seen:
            skipped_existing += 1 if thought_id in existing else 0
            continue

        # ⚠️ Index 外のチャンクIDは捨てる。LLM が小説を根拠に挙げても通さない
        roles = _evidence_roles(card)
        evidence = sorted(cid for cid in roles if cid in valid)
        if len(evidence) < MIN_EVIDENCE_CHUNKS:
            skipped_no_evidence += 1
            continue

        card_id = _card_id(person_id, thought_id)
        c.table("thought_cards").upsert({
            "card_id": card_id,
            "person_id": person_id,
            "thought_id": thought_id,
            "title": title,
            "core_claim": card.get("core_claim"),
            "distinctions": card.get("distinctions") or [],
            "answer_policy": card.get("answer_policy") or [],
            "prohibitions": card.get("prohibitions") or [],
            "representative_chunk_ids": evidence,
            "search_text": " ".join(
                [title, card.get("core_claim") or "", *(card.get("answer_policy") or [])]
            ),
            # ⚠️ 必ず draft。承認は人間が行う(指示書§9 Pass4)
            "status": "draft",
        }).execute()

        # 受入#13: どの原典のどこが根拠かを links にも残す
        for cid in evidence:
            src = valid[cid]
            c.table("thought_evidence_links").upsert({
                "link_id": f"tel_{hashlib.sha256(f'{thought_id}:{cid}'.encode()).hexdigest()[:16]}",
                "person_id": person_id,
                "thought_id": thought_id,
                "source_id": src["source_id"],
                "chunk_id": cid,
                "evidence_role": roles[cid],
                "strength": "medium",
                # カードと同じく未承認から始める(approved の links だけがRAGで引かれる)
                "status": "draft",
            }).execute()

        seen.add(thought_id)
        created += 1

    return created, skipped_existing, skipped_no_evidence


def approve_card(card_id: str, *, reviewed_by: str, client=None) -> dict:
    """思想カードを承認する。

    承認は回答生成へ直結するため、**根拠チャンクが今も思想Indexに居るか**を
    確かめてから通す。取り込み直しやタグの修正で、根拠が小説側へ移っていることがある。
    """
    c = client or db.client()
    rows = (
        c.table("thought_cards").select("*").eq("card_id", card_id).execute().data or []
    )
    if not rows:
        raise ValueError(f"カードが見つかりません(card_id={card_id})")
    card = rows[0]

    evidence = card.get("representative_chunk_ids") or []
    if not evidence:
        raise ValueError(f"根拠チャンクが無いカードは承認できません(card_id={card_id})")

    index_ids = {ch["chunk_id"] for ch in fetch_index_chunks(card["person_id"], client=c)}
    existing = {
        r["chunk_id"]
        for r in c.table("source_chunks").select("chunk_id")
        .in_("chunk_id", evidence).execute().data or []
    }

    missing = [cid for cid in evidence if cid not in existing]
    if missing:
        raise ValueError(
            f"根拠チャンクが実在しないため承認できません: {', '.join(missing)}"
        )
    outside = [cid for cid in evidence if cid not in index_ids]
    if outside:
        raise ValueError(
            "作者の直接発言でない根拠が含まれるため承認できません: "
            + ", ".join(outside)
        )

    c.table("thought_cards").update({"status": "approved"}).eq(
        "card_id", card_id).execute()
    # links も同時に有効化する。approved の links だけが回答時に引かれる
    c.table("thought_evidence_links").update({"status": "approved"}).eq(
        "thought_id", card["thought_id"]).execute()
    # 代表質問も active 化する(仕様6.11)。
    # ⚠️ これが漏れると Thought Router の Stage2(代表質問のベクトル検索。RPC
    # match_thought_questions は status='active' で絞る)が常に空振りし、思想質問が
    # すべてフォールバックカードへ流れる。回答自体は返るので気づきにくい
    # (実測: 承認済み12枚に対し質問113件が全てdraftのまま放置されていた)
    c.table("thought_questions").update({"status": "active"}).eq(
        "target_thought_id", card["thought_id"]).execute()
    return {"card_id": card_id, "status": "approved", "reviewed_by": reviewed_by}


def reject_card(card_id: str, *, reviewed_by: str, client=None) -> dict:
    c = client or db.client()
    rows = (
        c.table("thought_cards").update({"status": "rejected"})
        .eq("card_id", card_id).execute().data or []
    )
    if not rows:
        raise ValueError(f"カードが見つかりません(card_id={card_id})")
    return {"card_id": card_id, "status": "rejected", "reviewed_by": reviewed_by}
