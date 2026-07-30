"""装置カタログ: 原作の各章が「何をやったか」を列挙する(続編生成の防具)。

続編を書かせると、個々の章から蒸留したカードが章固有の装置を運び込み、
出力が原作のメドレーになる（実測: 夢十一夜で第一夜の「赤い日を数える」・
第三夜の「全知の子供」が n=7 で毎回再現した）。文字列類似の Guard では
原理的に捕まらない — 筋・装置のレベルの再現だから。

このカタログは2つの用途を**1つの資料**で兼ねる:
  (2) カード除外 … 中心装置を含むカードは続編モードでハード除外する
  (3) judge      … 草稿と突き合わせ、中心装置の再現 / 付随装置の共起を検出する

⚠️ 除外の可否をフラグ単独で書かないこと。`role` は素材で、判定は
`verdict_for_matches` に集約する（中心=即fail、付随=同一章で2つ以上の共起で
fail）。装置は単体ではなく共起で章を再現する — 「数える」だけなら偶然でも、
「赤い日 + 数える + 待つ」が揃えば第一夜のなぞり。

人手承認の対象ではない（コーパスから再生成できる派生物）。リポジトリ内の
JSON に置いて差分をレビューする: `creative/device_catalogs/{source_id}.json`。
"""

import json
import re
from pathlib import Path

from .. import config, llm
from . import repo

# v2: central を一章1件に絞り、超過は justification 必須にした。
# v1 は 56装置中38件(68%)が central になり、「中心=即fail」が効きすぎた
CATALOG_VERSION = "v2"
PROMPT_VERSION = "v2"

ROLE_CENTRAL = "central"
ROLE_INCIDENTAL = "incidental"
ROLES = (ROLE_CENTRAL, ROLE_INCIDENTAL)

# 付随装置は単体では章の再現と見なさない。同一章から**2つ以上**そろって
# 初めて「その章のなぞり」と判定する(仕様相談 2026-07-30)
INCIDENTAL_CO_OCCURRENCE_THRESHOLD = 2

CATALOG_DIR = Path(__file__).resolve().parent / "device_catalogs"

SYSTEM = """あなたは小説の構成を分解する編集者である。
作品の各章が「何をやったか」を、続編を書く者が**繰り返してはいけない仕掛け**
として列挙する。あらすじの要約ではなく、装置(仕掛け)の名指しをする。
JSONのみを出力する。"""

PROMPT = """以下は『{work_title}』の「{chapter_title}」の全文である。
この章が使っている装置を列挙せよ。

## 本文(チャンクIDつき)
{chunks}

## 装置とは
その章を「その章たらしめている」仕掛け。たとえば以下の水準を指す:
- 中心となる異常（何が一つだけ異常か）
- 反復とその形（何を数えるか、何を繰り返すか）
- 語り手と他の登場人物の関係の型（誰が何を知っているか）
- 結末の型（何がどう反転して終わるか）

作風の一般的特徴（一人称「自分」、写生的な距離、色彩の使い方など）は
**装置ではない**。それらは作品を横断するので、ここには書かない。

## role の付け方（ここが最重要）
`central` は「この章をこの章たらしめている装置」を **必ず1つだけ** 選ぶ。
重要な要素を数え上げるのではなく、**順位を付けて頂点を1つ決める**こと。
これを欠いたら別の話になる、という一つを選ぶ。

2つ目を `central` にすることは、原則として認めない。どうしても必要な場合だけ、
`justification` に「**どちらを欠いても章が別物になる**」ことを一文で示す。
示せないものは `central` にしない（`incidental` に回す）。

`incidental`: その章にあるが、他の章や他作品にもありうる装置。

## 指示
- `evidence_chunk_ids` は上に与えたチャンクIDから選ぶ（最低1件）
- 装置ごとに、続編で再現されたら困る理由が分かる説明を付ける
- この章に無いものを書かない。3〜6件程度（うち central は原則1件）

## 出力形式(JSONのみ)
{{
  "devices": [
    {{
      "device_id": "英小文字スネークケースの短い識別子",
      "name": "装置の名前(短い名詞句)",
      "role": "central | incidental",
      "description": "何をする装置か。続編でなぞるとどう見えるか",
      "justification": "centralが2つ以上のときのみ。無ければ省略",
      "evidence_chunk_ids": ["チャンクID", "..."]
    }}
  ]
}}"""


def catalog_path(source_id: str) -> Path:
    """カタログの置き場所。source_id ごとに1ファイル(他作品・他作家へ横展開)。"""
    return CATALOG_DIR / f"{source_id}.json"


def fetch_chapters(source_id: str, *, client=None) -> list[dict]:
    """章ごとに本文とチャンクIDをまとめる(chunk_id 順)。"""
    c = client or repo.db.client()
    chunks = (
        c.table("source_chunks")
        .select("chunk_id, chapter_title, text")
        .eq("source_id", source_id)
        .eq("status", "active")
        .order("chunk_id")
        .execute()
        .data
        or []
    )
    chapters: list[dict] = []
    by_title: dict[str, dict] = {}
    for ch in chunks:
        title = ch.get("chapter_title") or "(章なし)"
        entry = by_title.get(title)
        if entry is None:
            entry = {"chapter_title": title, "chunks": []}
            by_title[title] = entry
            chapters.append(entry)
        entry["chunks"].append(ch)
    return chapters


def _format_chunks(chunks: list[dict]) -> str:
    return "\n\n".join(f"[{ch['chunk_id']}] {ch['text']}" for ch in chunks)


def _slug(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", (value or "").strip().lower()).strip("_")
    return slug or fallback


def absorb_devices(response: dict, *, valid_chunk_ids: set[str], chapter_title: str) -> list[dict]:
    """1章ぶんの応答を装置リストにする。

    ⚠️ その章に無いチャンクIDを根拠に挙げてきたら**その装置ごと捨てる**。
    judge が fail を出したとき「どの夜のどこと衝突したか」を管理画面に出す
    ため、根拠は必ず実在させる(思想カードの Index 外チャンク除去と同じ規律)。
    """
    devices: list[dict] = []
    seen: set[str] = set()
    for i, raw in enumerate((response or {}).get("devices") or []):
        name = (raw.get("name") or "").strip()
        if not name:
            continue
        role = raw.get("role")
        if role not in ROLES:
            role = ROLE_INCIDENTAL  # 不明な役割は弱い側へ倒す(除外を効かせすぎない)
        evidence = [
            cid for cid in (raw.get("evidence_chunk_ids") or []) if cid in valid_chunk_ids
        ]
        if not evidence:
            continue
        device_id = _slug(raw.get("device_id"), f"device_{i:02d}")
        if device_id in seen:
            device_id = f"{device_id}_{i:02d}"
        seen.add(device_id)
        devices.append({
            "device_id": device_id,
            "name": name,
            "role": role,
            "description": (raw.get("description") or "").strip(),
            "justification": (raw.get("justification") or "").strip(),
            "chapter_title": chapter_title,
            "evidence_chunk_ids": evidence,
        })
    return _enforce_single_central(devices)


def _enforce_single_central(devices: list[dict]) -> list[dict]:
    """central は一章1件。超過するなら理由が要る。

    上限を「1〜2件」と書くとモデルは上限まで埋める（v1 で中心が68%に膨らんだ
    のと同じ圧力が2件側に働く）。理由の無い2件目は `incidental` へ降ろし、
    降格は**降ろす方向のみ**にする — central が0件でも繰り上げない
    （無いものを作らない。0件の章はカタログの meta で分かるようにする）。
    """
    centrals = [d for d in devices if d["role"] == ROLE_CENTRAL]
    if len(centrals) <= 1:
        return devices
    # 先頭はモデルが立てた頂点として必ず残す（全部落として0件にしない）。
    # 2件目以降は理由が無ければ降ろす
    for d in centrals[1:]:
        if not d["justification"]:
            d["role"] = ROLE_INCIDENTAL
            d["demoted_from_central"] = True
    return devices


def generate_catalog(
    source_id: str, *, work_title: str = "", client=None, call_json=None
) -> dict:
    """章ごとに1回ずつ呼んでカタログを組み立てる。

    全章を1プロンプトに詰めない — 思想カード生成で「先頭の資料からしか
    出ない」実測があり、同じ失敗をここで繰り返さないため。
    """
    call = call_json or llm.call_json
    chapters = fetch_chapters(source_id, client=client)
    if not chapters:
        raise repo.CreativeInvariantError(
            f"章が取得できません(source_id={source_id})。原典を取り込んでください。"
        )

    out_chapters = []
    total = 0
    for chapter in chapters:
        valid = {ch["chunk_id"] for ch in chapter["chunks"]}
        response = call(
            agent_name="creative_device_catalog",
            model=config.MODEL_CREATIVE_MAIN,
            system=SYSTEM,
            prompt=PROMPT.format(
                work_title=work_title or source_id,
                chapter_title=chapter["chapter_title"],
                chunks=_format_chunks(chapter["chunks"]),
            ),
            input_ref=f"device_catalog:{source_id}:{chapter['chapter_title']}",
            max_tokens=4096,
        )
        devices = absorb_devices(
            response, valid_chunk_ids=valid, chapter_title=chapter["chapter_title"]
        )
        total += len(devices)
        out_chapters.append({
            "chapter_title": chapter["chapter_title"],
            "devices": devices,
        })

    return {
        # どのプロンプト・どのモデルで作った派生物かを残す(agent_runs と同じ発想)
        "meta": {
            "source_id": source_id,
            "work_title": work_title or source_id,
            "catalog_version": CATALOG_VERSION,
            "prompt_version": PROMPT_VERSION,
            "model_id": config.MODEL_CREATIVE_MAIN,
            "chapters": len(out_chapters),
            "devices": total,
            "central_devices": sum(
                1 for ch in out_chapters for d in ch["devices"]
                if d["role"] == ROLE_CENTRAL
            ),
            # 中心が立たなかった章は判定が効かない。人が気づけるよう残す
            "chapters_without_central": [
                ch["chapter_title"] for ch in out_chapters
                if not any(d["role"] == ROLE_CENTRAL for d in ch["devices"])
            ],
        },
        "chapters": out_chapters,
    }


def save_catalog(catalog: dict) -> Path:
    path = catalog_path(catalog["meta"]["source_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def load_catalog(source_id: str) -> dict | None:
    path = catalog_path(source_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def iter_devices(catalog: dict):
    for chapter in catalog.get("chapters") or []:
        yield from chapter.get("devices") or []


def central_devices(catalog: dict) -> list[dict]:
    """ハード除外(2)と即fail(3)の対象。"""
    return [d for d in iter_devices(catalog) if d["role"] == ROLE_CENTRAL]


DETECT_SYSTEM = """あなたは草稿が原作の装置を再現しているかを判定する検査員である。
語句の一致ではなく、**仕掛けとして同じことをしているか**を見る。
再現していない装置を挙げてはならない。JSONのみを出力する。"""

DETECT_PROMPT = """## 検査対象（続編の草稿・構成案）
{draft}

## 原作『{work_title}』の中心装置（全{count}件）
{devices}

## 判定
検査対象が再現している装置を**すべて**挙げよ。無ければ空配列でよい。

- 語句が違っても、仕掛けとして同じことをしていれば再現である
  （例: 装置が「赤い日の出没を数えて数えきれなくなる」で、草稿が
   「流れる灯籠を数えて数えきれなくなる」なら、対象が違っても再現）
- 主題や雰囲気が似ているだけ、同じ語が出てくるだけでは再現ではない
- 短い掌編が多くの章の中心装置を同時に再現することは稀である。
  迷うものは挙げない

## 出力形式(JSONのみ)
{{
  "reproduced": [
    {{
      "device_id": "上の一覧の device_id",
      "quote": "検査対象からの**そのままの引用**（該当箇所。10〜60字程度）",
      "reason": "その引用がその装置とどう対応するかを一文で"
    }}
  ]
}}"""


def _normalized(text: str) -> str:
    return "".join((text or "").split())


def detect_devices(draft: str, catalog: dict, *, call_json=None, model=None) -> list[dict]:
    """草稿・構成案に現れている装置を検出する（多層防御の最終段）。

    カード選別は**注入前**の検査で、漏れの経路（カードの意外な具体化・brief の
    誘導・テンプレート・注入原典）を事前に全部数え上げられることを前提にする。
    実測でこの前提は一度破れた（由来が第四夜の反復句カードが第一夜の計数へ
    収束し、チャンク重なりが無いため一度も照合されなかった）。ここはどの経路
    から入っても最後に草稿へ現れる装置を捕まえる。

    ⚠️ **装置ごとに独立した二値判定をしてはならない。** 10装置に個別の yes/no を
    かけると、1回あたりの偽陽性率が試行回数で膨らむ（実測: 1200字の掌編が6章の
    中心装置を同時に再現していると判定された）。多重比較の解消は「試行を1回に
    する」ことからではなく、**判定を独立でなく同時比較にする**ことから来る。
    ここでは全装置を一度に見せて該当を列挙させる。

    ⚠️ ただし screening 側（カード判定）の強制選択とは形が違う。カードは単一の
    アトラクタへ収束するので**単一選択**が正しいが、テキストは複数の装置を同時に
    含みうる（実測: 最初の生成は第一夜+第三夜+第六夜を含んだ）。ここで単一選択に
    すると今度は偽陰性側へ壊れる。**複数可・空可**の列挙にする。

    根拠引用は必須。judge の幻覚を機械側で落とすため、引用が検査対象に実在する
    かを照合し、実在しない検出は破棄する。
    """
    call = call_json or llm.call_json
    centrals = central_devices(catalog)
    if not centrals:
        return []
    by_id = {d["device_id"]: d for d in centrals}
    listing = "\n".join(
        f"- device_id: {d['device_id']} / {d.get('chapter_title')}「{d.get('name')}」"
        f"\n  {d.get('description') or ''}"
        for d in centrals
    )
    result = call(
        agent_name="creative_device_detect",
        model=model or config.MODEL_CREATIVE_MAIN,
        system=DETECT_SYSTEM,
        prompt=DETECT_PROMPT.format(
            draft=draft,
            work_title=catalog.get("meta", {}).get("work_title") or "",
            count=len(centrals),
            devices=listing,
        ),
        input_ref="device_detect",
        # 全装置を同時に見せる設計なので、該当が複数出ると引用+理由で長くなる。
        # 2048 では実運用で打ち切られた（切り詰めは llm 側が明示的に検出する）
        max_tokens=8192,
    )

    haystack = _normalized(draft)
    matched = []
    for hit in result.get("reproduced") or []:
        device = by_id.get(hit.get("device_id"))
        if not device:
            continue
        quote = (hit.get("quote") or "").strip()
        # 幻覚した引用の検出は破棄する（機械で落とせる偽陽性）
        if not quote or _normalized(quote) not in haystack:
            continue
        matched.append({
            **device,
            "detect_quote": quote,
            "detect_reason": (hit.get("reason") or "").strip(),
        })
    return matched


def outline_text(outline: dict) -> str:
    """outline(JSON)を検査にかけられる平文にする。"""
    keys = ("intro", "anomaly", "repetition_and_change", "turn", "ending", "unexplained")
    return "\n".join(f"{k}: {outline.get(k) or ''}" for k in keys)


def verdict_for_matches(matches: list[dict]) -> dict:
    """検出された装置から判定を出す（(2)と(3)で共有する唯一のルール）。

    - 中心装置が1つでも再現されていれば fail
    - 付随装置は**同一章から2つ以上**そろったら fail
      （単体の付随装置は偶然ありうる。束で出たときだけ章の再現と見なす）

    matches は装置dict（`chapter_title` / `role` を持つ）のリスト。
    """
    central = [m for m in matches if m.get("role") == ROLE_CENTRAL]
    by_chapter: dict[str, list[dict]] = {}
    for m in matches:
        if m.get("role") == ROLE_INCIDENTAL:
            by_chapter.setdefault(m.get("chapter_title") or "", []).append(m)
    co_occurring = {
        title: group
        for title, group in by_chapter.items()
        if len(group) >= INCIDENTAL_CO_OCCURRENCE_THRESHOLD
    }

    reasons = []
    for m in central:
        reasons.append(
            f"{m.get('chapter_title')}の中心装置「{m.get('name')}」を再現している"
            f"(根拠: {', '.join(m.get('evidence_chunk_ids') or [])})"
        )
    for title, group in co_occurring.items():
        names = "・".join(m.get("name") or "" for m in group)
        reasons.append(f"{title}の付随装置が{len(group)}つ共起している({names})")

    return {
        "passed": not central and not co_occurring,
        "central_hits": central,
        "co_occurring_chapters": co_occurring,
        "reasons": reasons,
    }
