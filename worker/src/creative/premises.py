"""十一夜目の中心前提を3案立てて選ぶ(収束問題への直接の手当て)。

装置除外と検出は**フィルタ**であって、アトラクタの除去ではない。カードを抜き
橋を塞いでもなお、「『夢十夜』の続編」という依頼は最も有名な第一夜へ滑る
（実測: 汚染源を全部抜いた後でも3本中1本が計数へ落ち、1本は3試行を第一夜で
焼き切った）。装置の**出現率そのもの**を下げる手はここしかない。

もう一つ、生成間の収束もここで扱う。除外は原作への収束を防ぐが、3本が互いに
酷似する現象は防げない（実測: 橋・子供・計数・幼時の顔が3本とも共通した）。
過去に採用した前提を負例として見せることでしか防げないので、採用前提を蓄積して
照合する。

⚠️ 通過案から**ランダムに**1つ選ぶ。judge に最良を選ばせない — 「judge が好む
前提」という新しいアトラクタができ、3本酷似問題が判定器の趣味の形で再来する。
エントロピー源はランダム選択で確保する。

判定は1回の呼び出しで同時に行う（同時比較の原理はここでも同じ。独立した
二値判定に割ると偽陽性が試行回数で膨らむ）。
"""

import random

from .. import config, llm
from . import repo

PREMISE_COUNT = 3
MAX_PREMISE_ROUNDS = 2  # 全案が落ちたときの作り直し上限

DRAFT_SYSTEM = """あなたは短編小説の中心前提を立てる編集者である。
筋書きではなく、「この話にだけある一つの奇妙な前提」を立てる。
既存作品の仕掛けをなぞらない。JSONのみを出力する。"""

DRAFT_PROMPT = """『{work_title}』の続編として書く一夜の、**中心前提**を{count}案立てよ。

## 依頼
- モチーフ: {motif}
- 状況: {situation}
- 目指す読後感: {emotional_target}
- 追加制約: {constraints}

## 中心前提とは
その一夜にだけある、一つの奇妙な前提。筋書きでも教訓でもない。
「何が一つだけ異常か」を一文で言い切り、それを支える中心イメージを1つ添える。

## 避けること
原作の各夜が使った仕掛け（数える／期限を切る／全知の子供／鏡／伝聞の枠など）を
持ち込まない。**十一夜目にしかない前提**を立てる。
{avoid_section}
## 指示
- {count}案は互いに**別の仕掛け**であること。同じ前提の言い換えを並べない
- 中心イメージは1案につき1つだけ

## 出力形式(JSONのみ)
{{
  "premises": [
    {{"premise": "一文の奇妙な前提", "image": "それを支える中心イメージ1つ"}}
  ]
}}"""

SCREEN_SYSTEM = """あなたは中心前提が既存の仕掛けと重なっていないかを判定する検査員である。
語句ではなく、仕掛けとして同じことをしているかを見る。JSONのみを出力する。"""

SCREEN_PROMPT = """## 検査する中心前提（{count}案）
{premises}

## 原作『{work_title}』の装置
{devices}

## 過去に採用済みの前提
{past}

## 判定
各案について、次の3点を同時に見る。

1. `device_overlap`: 原作の装置と**前提のレベルで**重なるか。
   ⚠️ 装置の成分が一場面の所作として残響する程度は重なりではない。
   その案の**骨格**が装置（またはその2成分以上）でできているときだけ true
2. `duplicate_of`: 他の案と実質同じ仕掛けなら、その案の番号。無ければ null
3. `repeats_past`: 過去に採用済みの前提と実質同じなら true

## 出力形式(JSONのみ)
{{
  "results": [
    {{"index": 0, "device_overlap": true/false, "overlapped_device_id": "…またはnull",
      "duplicate_of": null, "repeats_past": false, "reason": "一文で"}}
  ]
}}"""


def _format_constraints(brief: dict) -> str:
    return "、".join(brief.get("constraints") or []) or "(なし)"


def build_premises(
    brief: dict, *, work_title: str, avoid=None, job_id=None, call_json=None
) -> list[dict]:
    """中心前提を3案立てる。"""
    call = call_json or llm.call_json
    avoid_section = ""
    if avoid:
        avoid_section = (
            "\n## 前回の案が落ちた理由（同じ轍を踏まない）\n"
            + "\n".join(f"- {a}" for a in avoid)
            + "\n"
        )
    result = call(
        agent_name="creative_premise_draft",
        model=config.MODEL_CREATIVE_MAIN,
        system=DRAFT_SYSTEM,
        prompt=DRAFT_PROMPT.format(
            work_title=work_title,
            count=PREMISE_COUNT,
            motif=brief.get("motif") or "(指定なし)",
            situation=brief.get("situation") or "(指定なし)",
            emotional_target=brief.get("emotional_target") or "(指定なし)",
            constraints=_format_constraints(brief),
            avoid_section=avoid_section,
        ),
        input_ref=f"creative_generation:{job_id}",
        max_tokens=2048,
    )
    out = []
    for raw in (result.get("premises") or [])[:PREMISE_COUNT]:
        premise = (raw.get("premise") or "").strip()
        if premise:
            out.append({"premise": premise, "image": (raw.get("image") or "").strip()})
    return out


def screen_premises(
    premises: list[dict], catalog: dict, *, past=None, work_title: str = "",
    job_id=None, call_json=None,
) -> list[dict]:
    """3案を1回の呼び出しで同時に判定する。"""
    if not premises:
        return []
    call = call_json or llm.call_json
    devices = [
        d
        for chapter in catalog.get("chapters") or []
        for d in chapter.get("devices") or []
    ]
    listing = "\n".join(
        f"- device_id: {d['device_id']} / {d.get('chapter_title')}「{d.get('name')}」"
        + (f"\n  成分: {' / '.join(d.get('components') or [])}" if d.get("components") else "")
        for d in devices
    )
    result = call(
        agent_name="creative_premise_screen",
        model=config.MODEL_CREATIVE_MAIN,
        system=SCREEN_SYSTEM,
        prompt=SCREEN_PROMPT.format(
            count=len(premises),
            premises="\n".join(
                f"{i}. {p['premise']}（中心イメージ: {p.get('image') or '—'}）"
                for i, p in enumerate(premises)
            ),
            work_title=work_title or catalog.get("meta", {}).get("work_title") or "",
            devices=listing,
            past="\n".join(f"- {p}" for p in (past or [])) or "(なし)",
        ),
        input_ref=f"creative_generation:{job_id}",
        # カタログ43装置を全部渡すので入力が長く、3案×判定理由で出力も伸びる。
        # 2048 では実運用で打ち切られた（検出層で踏んだのと同じ失敗）
        max_tokens=8192,
    )

    by_index = {}
    for r in result.get("results") or []:
        try:
            by_index[int(r.get("index"))] = r
        except (TypeError, ValueError):
            continue

    screened = []
    for i, p in enumerate(premises):
        r = by_index.get(i, {})
        # 判定が返らなかった案は落とす（素通りさせない）
        passed = bool(r) and not r.get("device_overlap") and not r.get("repeats_past") \
            and r.get("duplicate_of") in (None, "", i)
        screened.append({
            **p,
            "index": i,
            "passed": passed,
            "device_overlap": bool(r.get("device_overlap")),
            "overlapped_device_id": r.get("overlapped_device_id"),
            "duplicate_of": r.get("duplicate_of"),
            "repeats_past": bool(r.get("repeats_past")),
            "reason": (r.get("reason") or "").strip(),
        })
    return screened


def choose_premise(screened: list[dict], *, rng=None) -> dict | None:
    """通過案から**ランダムに**1つ選ぶ。

    judge に最良を選ばせない — 「judge が好む前提」という新しいアトラクタが
    でき、生成間の酷似が判定器の趣味の形で再来する。
    """
    survivors = [s for s in screened if s["passed"]]
    if not survivors:
        return None
    return (rng or random).choice(survivors)


def load_past_premises(profile_id: str, *, client=None, limit: int = 30) -> list[str]:
    """過去に採用した前提。(iii)の照合リストとして蓄積する。"""
    c = client or repo.db.client()
    rows = (
        c.table("creative_generations")
        .select("brief_normalized, created_at")
        .eq("profile_id", profile_id)
        # 成否で絞らない。失敗した走行で使った前提も「使用済み」として扱う
        # （同じ前提の再利用は生成間の類似 (f) の測定を汚す）
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )
    out = []
    for row in rows:
        premise = ((row.get("brief_normalized") or {}).get("premise") or {}).get("premise")
        if premise:
            out.append(premise)
    return out


class PremiseExhaustedError(RuntimeError):
    """作り直しても装置に重ならない前提が立たなかった。安全側で失敗させる。"""


def resolve_premise(
    brief: dict, catalog: dict, *, past=None, work_title: str = "",
    job_id=None, call_json=None, rng=None,
) -> dict:
    """3案立てて判定し、通過案からランダムに1つ選ぶ。落ちたら作り直す。"""
    avoid: list[str] = []
    attempts = []
    for _ in range(MAX_PREMISE_ROUNDS + 1):
        premises = build_premises(
            brief, work_title=work_title, avoid=avoid, job_id=job_id, call_json=call_json
        )
        screened = screen_premises(
            premises, catalog, past=past, work_title=work_title,
            job_id=job_id, call_json=call_json,
        )
        attempts.append(screened)
        chosen = choose_premise(screened, rng=rng)
        if chosen:
            return {"premise": chosen, "attempts": attempts}
        avoid = [s["reason"] for s in screened if s.get("reason")]
    raise PremiseExhaustedError(
        "装置に重ならない中心前提が立ちませんでした: "
        + " / ".join(a for s in attempts for a in [x.get("reason", "") for x in s] if a)
    )
