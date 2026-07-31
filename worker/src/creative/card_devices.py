"""カードが「装置」か「作風」かを移植テストで判定する(続編生成の除外規則)。

続編を書かせると、章固有の装置を運ぶカードが原作のメドレーを作る。ではどの
カードが装置を運んでいるのか — これを「特定章の蒸留か」と直接聞くと判定が
曖昧になる。代わりに**移植テスト**で聞く:

  このカードを、別の作家の別の作品の続編生成に投入したとき、指示として成立するか。

成立する（「距離のある描写」「一人称固定」）= 作風。
成立しない（「数えさせて時間の長さを体感させる」「子供に大人以上の知識」）= 装置。
カードが具体的な像を名指ししているか、書き方の構えだけを述べているか、という
線とほぼ一致し、判定根拠を書かせれば人手で監査できる。

⚠️ 装置カタログの根拠チャンクとの重なりでは判定できない。実測で、中心装置を
38件から10件へ絞っても除外集合は**1枚も変わらなかった**（16枚中10枚）。原作が
小さいとどのカードの根拠チャンクも何かの装置と重なるため、チャンク重なりは
「そのカードが原作由来か」しか測っていない。

判定結果はカード本体（人手承認済みの資産）へ書かない。再判定のたびに承認済み
データを書き換えないよう、`creative/card_classifications/{profile_id}.json` に置く。
"""

import json
import re
from pathlib import Path

from .. import config, llm
from . import repo

CLASSIFICATION_VERSION = "v1"
PROMPT_VERSION = "v1"

PORTABLE = "portable"        # 作風。続編でも使ってよい
DEVICE_BOUND = "device_bound"  # 装置。続編では原則除外
VERDICTS = (PORTABLE, DEVICE_BOUND)

CLASSIFICATION_DIR = Path(__file__).resolve().parent / "card_classifications"

# device_catalog の role 値（循環importを避けるため文字列で持つ）
ROLE_CENTRAL_VALUE = "central"

SYSTEM = """あなたは創作指示の汎用性を判定する編集者である。
与えられた指示が、別の作家の別の作品にも通用する「書き方の構え」なのか、
特定の作品の像を運ぶ「装置」なのかだけを判定する。JSONのみを出力する。"""

PROMPT = """以下は、ある作家の作風から蒸留された創作カードである。

## カード
種別: {card_type}
題: {title}
要約: {summary}
書き方の例: {patterns}

## 判定（移植テスト）
このカードを、**別の作家の別の作品**の続編を書かせる指示として投入したとき、
指示として成立するか。

- `portable`（作風）: 成立する。視点・距離・締めの態度など、**書き方の構え**
  だけを述べている。どの作品にも適用できる
- `device_bound`（装置）: 成立しない。特定の作品の像（登場人物・事物・出来事・
  数える対象など）を名指ししている。別作品の続編でこれを実行すると、元の作品の
  借用になる

目安: カードが具体的な像を名指ししているか、構えだけを述べているか。
迷ったら `device_bound` に倒す（作風を1枚落とす方が、装置を1枚通すより安い）。

## 出力形式(JSONのみ)
{{
  "verdict": "portable | device_bound",
  "reason": "別の作家の続編へ入れたらどうなるかを一文で",
  "named_images": ["カードが名指ししている具体的な像", "..."]
}}"""


# ── 二段フィルタ（本命の判定） ──
#
# 移植テスト単独では、実測で 7/7 再現した2枚（「数えさせる」「子供に大人以上の
# 知識」）を取り逃がした。原因は**カードを文脈から切り離して問うた**こと。
# 危険性は指示の抽象度ではなく、夢十夜的な文脈に置いたとき最も確からしい
# 具体化が特定の章へ収束することにある。問うべきは移植可能性ではなく再収束性。
#
#   段1: 根拠チャンクが中心装置と重なるカードを候補に出す（再現率を確保）
#   段2: その中心装置を**並べて見せて**、従ったとき再現するかを問う（精度を回復）
PARAPHRASE = "paraphrase"          # 装置そのものの言い換え
GENERALIZATION = "generalization"  # 装置の一成分の一般化。抽象だが同じ像へ収束する
UNRELATED = "unrelated"            # 従っても再現しない
RECONVERGENCE_VERDICTS = (PARAPHRASE, GENERALIZATION, UNRELATED)
# 除外するのは言い換えと一般化の両方。「数えさせる」は第一夜中心の言い換えでは
# なく一成分の一般化なので、言い換えだけを見ると no 側へ倒れる
EXCLUDING_VERDICTS = (PARAPHRASE, GENERALIZATION)

RECONVERGENCE_SYSTEM = """あなたは創作指示が原作のどの装置へ収束するかを判定する編集者である。
指示の抽象度ではなく、**その指示に従った結果どの像に落ちるか**だけを見る。
JSONのみを出力する。"""

# 段2は**1枚につき1回**の強制選択にする。装置ごとに独立した二値判定をかけると
# 偽陽性が試行回数で膨らむ（実測: 16枚×10装置の総当たりで 11/16 が除外され、
# 夢十夜由来ですらない批評カードまで第一夜の計数に収束すると判定された）。
#
# ⚠️ 検出層（device_catalog.detect_devices）の同時比較とは形が違う。カードは
# 単一のアトラクタへ収束するのでここは**単一選択**が正しいが、テキストは複数の
# 装置を同時に含みうるので向こうは複数可の列挙にする。同じ「同時比較」でも
# 出力の基数が違うことを取り違えると、どちらかが偽陰性側へ壊れる。
CHOICE_SYSTEM = """あなたは創作指示が原作のどの装置へ収束するかを判定する編集者である。
指示の抽象度ではなく、**その指示に従った結果どの像に落ちるか**だけを見る。
収束先が無ければ none を選ぶ。JSONのみを出力する。"""

CHOICE_PROMPT = """## カード（続編生成に投入される指示）
題: {title}
要約: {summary}
書き方の例: {patterns}

## 原作『{work_title}』の中心装置（全{count}件）
{devices}

## 判定
このカードの指示に『{work_title}』の続編という文脈で従ったとき、
**最も再現しやすい中心装置を1つだけ**選べ。どれも再現しないなら none。

選んだ場合は、その関係を答える:
- `paraphrase`: 装置そのものの言い換え
- `generalization`: 装置の一成分の一般化。指示は抽象的だが、この文脈では
  最も確からしい具体化がこの装置になる

⚠️ 抽象度で判定しない。**収束先**で判定する。指示が一般的な書き方に見えても、
この文脈で最も自然な具体化がその装置なら `generalization` である。
逆に、収束先が思い当たらないものを無理に選ばない。

## 出力形式(JSONのみ)
{{
  "device_id": "上の一覧の device_id。無ければ null",
  "verdict": "paraphrase | generalization | unrelated",
  "reason": "この指示に従うと何が起きるかを一文で"
}}"""


def judge_reconvergence_choice(
    card: dict, devices: list[dict], *, work_title: str, call_json=None
) -> dict:
    """1枚につき1回の強制選択。最も収束する中心装置を1つ、無ければ none。"""
    call = call_json or llm.call_json
    by_id = {d["device_id"]: d for d in devices}
    listing = "\n".join(
        f"- device_id: {d['device_id']} / {d.get('chapter_title')}「{d.get('name')}」"
        f"\n  {d.get('description') or ''}"
        for d in devices
    )
    result = call(
        agent_name="creative_card_choice",
        model=config.MODEL_CREATIVE_MAIN,
        system=CHOICE_SYSTEM,
        prompt=CHOICE_PROMPT.format(
            title=card.get("title") or "",
            summary=card.get("summary") or card.get("description") or "(なし)",
            patterns=_patterns(card),
            work_title=work_title,
            count=len(devices),
            devices=listing,
        ),
        input_ref=f"card_choice:{card['card_id']}",
        max_tokens=1024,
    )
    device = by_id.get(result.get("device_id"))
    verdict = result.get("verdict")
    if verdict not in RECONVERGENCE_VERDICTS:
        verdict = UNRELATED if device is None else GENERALIZATION
    if device is None:
        verdict = UNRELATED
    return {
        "device_id": device["device_id"] if device else None,
        "chapter_title": device.get("chapter_title") if device else None,
        "device_name": device.get("name") if device else None,
        "verdict": verdict,
        "reason": (result.get("reason") or "").strip(),
    }


RECONVERGENCE_PROMPT = """## カード（続編生成に投入される指示）
題: {title}
要約: {summary}
書き方の例: {patterns}

## 原作『{work_title}』の中心装置（{chapter_title}）
名: {device_name}
説明: {device_description}

## 判定
このカードの指示に、『{work_title}』の続編という文脈で従ったとき、
この中心装置（またはその主要成分）を再現する可能性が高いか。

- `paraphrase`: 装置そのものの言い換え
- `generalization`: 装置の一成分の一般化。指示は抽象的だが、この文脈では
  最も確からしい具体化がこの装置になる
- `unrelated`: この装置とは無関係。従っても再現しない

⚠️ 抽象度で判定しない。**収束先**で判定する。指示が一般的な書き方に見えても、
『{work_title}』の続編という文脈で最も自然な具体化がこの装置なら
`generalization` である。

## 出力形式(JSONのみ)
{{
  "verdict": "paraphrase | generalization | unrelated",
  "reason": "この指示に従うと何が起きるかを一文で"
}}"""


def classification_path(profile_id: str) -> Path:
    return CLASSIFICATION_DIR / f"{profile_id}.json"


def screening_path(profile_id: str) -> Path:
    return CLASSIFICATION_DIR / f"{profile_id}.screening.json"


def _patterns(card: dict) -> str:
    values = card.get("positive_patterns") or []
    return "; ".join(str(v) for v in values) or "(なし)"


def classify_card(card: dict, *, call_json=None) -> dict:
    """1枚を移植テストにかける。"""
    call = call_json or llm.call_json
    result = call(
        agent_name="creative_card_transplant",
        model=config.MODEL_CREATIVE_MAIN,
        system=SYSTEM,
        prompt=PROMPT.format(
            card_type=card.get("card_type") or "",
            title=card.get("title") or "",
            summary=card.get("summary") or card.get("description") or "(なし)",
            patterns=_patterns(card),
        ),
        input_ref=f"card_transplant:{card['card_id']}",
        max_tokens=1024,
    )
    verdict = result.get("verdict")
    if verdict not in VERDICTS:
        # 不明な判定は装置側へ倒す(作風を1枚落とす方が、装置を1枚通すより安い)
        verdict = DEVICE_BOUND
    return {
        "card_id": card["card_id"],
        "card_type": card.get("card_type"),
        "title": card.get("title"),
        "verdict": verdict,
        "reason": (result.get("reason") or "").strip(),
        "named_images": [str(v) for v in (result.get("named_images") or [])],
    }


def classify_profile_cards(profile_id: str, *, client=None, call_json=None) -> dict:
    """承認済みカードを1枚ずつ判定する。"""
    c = client or repo.db.client()
    cards = (
        c.table("creative_cards")
        .select("card_id, card_type, title, summary, description, positive_patterns")
        .eq("profile_id", profile_id)
        .eq("status", "approved")
        .order("card_id")
        .execute()
        .data
        or []
    )
    rows = [classify_card(card, call_json=call_json) for card in cards]
    return {
        "meta": {
            "profile_id": profile_id,
            "classification_version": CLASSIFICATION_VERSION,
            "prompt_version": PROMPT_VERSION,
            "model_id": config.MODEL_CREATIVE_MAIN,
            "cards": len(rows),
            "device_bound": sum(1 for r in rows if r["verdict"] == DEVICE_BOUND),
        },
        "cards": rows,
    }


def save_classification(classification: dict) -> Path:
    path = classification_path(classification["meta"]["profile_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(classification, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def load_classification_for_generation(profile_id: str) -> dict | None:
    """生成が読む判定。二段フィルタ(screening)を正とし、無ければ移植テストへ落ちる。"""
    path = screening_path(profile_id)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return load_classification(profile_id)


def load_classification(profile_id: str) -> dict | None:
    path = classification_path(profile_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ── 極性による除外対象外（規則）──
#
# 装置とは像・出来事という**正の内容**である。否定形・欠如形の指示は、従っても
# 何も生成しないので、定義上その paraphrase にも generalization にもなり得ない。
# judge は「未解決で終える → 第二夜の時計の型」と収束先を広く取りすぎたが、
# 第二夜の装置は時計という期限装置であって、宙吊りそのものではない。
#
# 極性は構文では判定できない。「外的な事件**でなく**認識の急変で締める」は否定語を
# 含むが要求形、「説明を**与えず**に宙吊りのまま終える」は文末が肯定形。judge に問う。
REQUIRES_CONTENT = "requires_content"  # 像・出来事の生成を要求する → 装置になりうる
FORBIDS_CONTENT = "forbids_content"    # 生成を禁止・抑制する → 装置を運べない
POLARITIES = (REQUIRES_CONTENT, FORBIDS_CONTENT)

POLARITY_EXEMPT_REASON = (
    "否定形・欠如形の指示は装置(正の内容)を運べないため装置判定の対象外。"
    "prohibition 側の検査系統で見る"
)

POLARITY_SYSTEM = """あなたは創作指示の極性だけを判定する編集者である。
その指示が「何かを書かせる」ものか「何かを書かせない」ものかだけを見る。
JSONのみを出力する。"""

POLARITY_PROMPT = """## 指示
題: {title}
要約: {summary}
書き方の例: {patterns}

## 判定
この指示に従ったとき、**新しい像や出来事が本文に生成されるか**。

- `requires_content`: 生成される。人物・事物・出来事・行為を書かせる指示
  （例:「結末を認識の急変で締める」→ 気づきの一文が生成される）
- `forbids_content`: 生成されない。説明・解決・特定の書き方を**与えない**ことを
  求める指示（例:「説明を与えずに終える」→ 何も足さないことを求めている）

⚠️ 文中に否定語があるかで判定しない。「AでなくBで締める」は B を書かせるので
`requires_content`。「説明を与えずに終える」は文末が肯定形でも
`forbids_content`。

## 出力形式(JSONのみ)
{{
  "polarity": "requires_content | forbids_content",
  "reason": "この指示に従うと何が本文に増えるか（増えないか）を一文で"
}}"""


def judge_polarity(card: dict, *, call_json=None) -> dict:
    """指示の極性を判定する。装置判定にかける前の門。"""
    call = call_json or llm.call_json
    result = call(
        agent_name="creative_card_polarity",
        model=config.MODEL_CREATIVE_MAIN,
        system=POLARITY_SYSTEM,
        prompt=POLARITY_PROMPT.format(
            title=card.get("title") or "",
            summary=card.get("summary") or card.get("description") or "(なし)",
            patterns=_patterns(card),
        ),
        input_ref=f"card_polarity:{card['card_id']}",
        max_tokens=512,
    )
    polarity = result.get("polarity")
    if polarity not in POLARITIES:
        # 不明なら装置判定にかける側へ倒す(門を素通りさせない)
        polarity = REQUIRES_CONTENT
    return {"polarity": polarity, "reason": (result.get("reason") or "").strip()}


def all_central_devices(catalog: dict) -> list[dict]:
    """段1: 全中心装置を候補にする（総当たり）。

    ⚠️ かつては根拠チャンクの重なりで候補を絞っていたが、これは**由来の章**しか
    教えない。カードの収束先は由来章とは別の章にもあり得る — 実測で、由来が
    第四夜の「呪文のような反復句」カードが第一夜の計数装置へ収束し、重なりが
    無いために一度も突き合わされず素通りした。段1は構成上の再現率1にして、
    精度は極性の門と段2 judge に守らせる。
    """
    return [
        d
        for chapter in catalog.get("chapters") or []
        for d in chapter.get("devices") or []
        if d.get("role") == ROLE_CENTRAL_VALUE
    ]


def origin_chapters(card: dict, catalog: dict) -> list[str]:
    """根拠チャンクが重なる章（判定の注記。候補の絞り込みには使わない）。"""
    evidence = set(card.get("evidence_chunk_ids") or [])
    if not evidence:
        return []
    found = []
    for chapter in catalog.get("chapters") or []:
        chunks = {
            cid
            for d in chapter.get("devices") or []
            for cid in d.get("evidence_chunk_ids") or []
        }
        if evidence & chunks:
            found.append(chapter["chapter_title"])
    return found


def judge_reconvergence(
    card: dict, device: dict, *, work_title: str, call_json=None
) -> dict:
    """段2: カードと中心装置を並べ、従ったとき再現するかを問う。

    段1が総当たりになったので判定数は カード×中心装置。軽量モデルで回す。
    """
    call = call_json or llm.call_json
    result = call(
        agent_name="creative_card_reconvergence",
        model=config.MODEL_CREATIVE_LIGHT,
        system=RECONVERGENCE_SYSTEM,
        prompt=RECONVERGENCE_PROMPT.format(
            title=card.get("title") or "",
            summary=card.get("summary") or card.get("description") or "(なし)",
            patterns=_patterns(card),
            work_title=work_title,
            chapter_title=device.get("chapter_title") or "",
            device_name=device.get("name") or "",
            device_description=device.get("description") or "",
        ),
        input_ref=f"card_reconvergence:{card['card_id']}:{device['device_id']}",
        max_tokens=1024,
    )
    verdict = result.get("verdict")
    if verdict not in RECONVERGENCE_VERDICTS:
        # 不明な判定は除外側へ倒す(作風を1枚落とす方が、装置を1枚通すより安い)
        verdict = GENERALIZATION
    return {
        "device_id": device["device_id"],
        "chapter_title": device.get("chapter_title"),
        "device_name": device.get("name"),
        "verdict": verdict,
        "reason": (result.get("reason") or "").strip(),
    }


def screen_cards(
    profile_id: str, catalog: dict, *, work_title: str = "", client=None, call_json=None
) -> dict:
    """二段フィルタを承認済みカード全体にかける。"""
    c = client or repo.db.client()
    cards = (
        c.table("creative_cards")
        .select("card_id, card_type, title, summary, description,"
                " positive_patterns, evidence_chunk_ids")
        .eq("profile_id", profile_id)
        .eq("status", "approved")
        .order("card_id")
        .execute()
        .data
        or []
    )
    title = work_title or catalog.get("meta", {}).get("work_title") or ""

    centrals = all_central_devices(catalog)
    rows = []
    for card in cards:
        candidates = centrals
        row = {
            "card_id": card["card_id"],
            "card_type": card.get("card_type"),
            "title": card.get("title"),
            "verdict": PORTABLE,  # 除外規則(resolve_exclusions)が読む形に揃える
            "reason": "",
            "named_images": [],
            "origin_chapters": origin_chapters(card, catalog),
            "candidates": [d["device_id"] for d in candidates],
            "judgments": [],
        }
        if not candidates:
            rows.append(row)
            continue

        # 門: 否定形・欠如形の指示は装置を運べないので装置判定にかけない
        polarity = judge_polarity(card, call_json=call_json)
        row["polarity"] = polarity
        if polarity["polarity"] == FORBIDS_CONTENT:
            row["exempt_rule"] = POLARITY_EXEMPT_REASON
            rows.append(row)
            continue

        judgment = judge_reconvergence_choice(
            card, candidates, work_title=title, call_json=call_json
        )
        hits = [judgment] if judgment["verdict"] in EXCLUDING_VERDICTS else []
        row["judgments"] = [judgment]
        row["verdict"] = DEVICE_BOUND if hits else PORTABLE
        row["reason"] = hits[0]["reason"] if hits else ""
        rows.append(row)

    return {
        "meta": {
            "profile_id": profile_id,
            "work_title": title,
            "classification_version": CLASSIFICATION_VERSION,
            "prompt_version": PROMPT_VERSION,
            "model_id": config.MODEL_CREATIVE_MAIN,
            "catalog_version": catalog.get("meta", {}).get("catalog_version"),
            "cards": len(rows),
            "candidates": sum(1 for r in rows if r["candidates"]),
            "device_bound": sum(1 for r in rows if r["verdict"] == DEVICE_BOUND),
            "polarity_exempt": sum(1 for r in rows if r.get("exempt_rule")),
        },
        "cards": rows,
    }


def save_screening(screening: dict) -> Path:
    path = screening_path(screening["meta"]["profile_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(screening, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


_QUOTED = re.compile(r"[「『]([^」』]+)[」』]")


def constraint_requests_card(constraint: str, row: dict) -> bool:
    """brief の制約がこのカードを明示的に要求しているか。

    続編というタスクは非対称な借用で、枠の装置（冒頭の定型句など）は意図的に
    継承し、内側の装置（数える太陽・全知の子供）は禁じる。依頼者が制約として
    名指ししたものは、装置であっても除外を免除する。

    判定は「」『』で括られた語句の一致を基本にする（依頼文と題の語尾は揺れるが、
    定型句そのものは揺れない）。監査できるよう、機械的で説明可能な規則に留める。
    """
    text = f"{row.get('title') or ''} {row.get('reason') or ''} " + " ".join(
        row.get("named_images") or []
    )
    quoted = _QUOTED.findall(constraint or "")
    if any(q and q in text for q in quoted):
        return True
    # 括弧が無い場合は、制約文そのものが題に含まれるときだけ一致とみなす
    bare = (constraint or "").strip()
    return bool(bare) and bare in text


CONSTRAINT_MATCH_SYSTEM = """あなたは依頼文の制約とカードの内容が同じことを
求めているかだけを判定する編集者である。JSONのみを出力する。"""

CONSTRAINT_MATCH_PROMPT = """## 依頼の制約
{constraint}

## カード
{title}

## 判定
この制約は、このカードが述べていることを**明示的に要求している**か。
言い回しが違っても、求めている内容が同じなら true。

## 出力形式(JSONのみ)
{{"requested": true または false, "reason": "一文で"}}"""


def judge_constraint_match(constraint: str, row: dict, *, call_json=None) -> bool:
    """字面が違っても、制約とカードが同じことを求めているかを見る。

    「こんな夢を見た」のような定型句は字面一致で拾えるが、「説明で締めず情景で
    閉じる」と「説明を与えずに宙吊りのまま終える」のような**同義の言い換え**は
    拾えない。免除条項を意味照合まで広げる（軽量モデルで足りる）。
    """
    call = call_json or llm.call_json
    result = call(
        agent_name="creative_constraint_match",
        model=config.MODEL_CREATIVE_LIGHT,
        system=CONSTRAINT_MATCH_SYSTEM,
        prompt=CONSTRAINT_MATCH_PROMPT.format(
            constraint=constraint, title=row.get("title") or ""
        ),
        input_ref=f"constraint_match:{row.get('card_id')}",
        max_tokens=512,
    )
    return bool(result.get("requested"))


def resolve_exclusions(classification: dict, *, brief_constraints=None, call_json=None) -> dict:
    """続編生成で除外するカードを決める。

    device_bound は原則ハード除外。ただし brief の constraints が明示要求して
    いるものは免除する（例:「こんな夢を見た」で始める）。
    """
    constraints = list(brief_constraints or [])
    excluded, exempted, kept = [], [], []
    for row in classification.get("cards") or []:
        if row["verdict"] != DEVICE_BOUND:
            kept.append(row)
            continue
        matched = next(
            (c for c in constraints if constraint_requests_card(c, row)), None
        )
        # 字面で拾えなければ意味照合へ（免除の取りこぼしを減らす）
        if matched is None and call_json is not None:
            matched = next(
                (c for c in constraints
                 if judge_constraint_match(c, row, call_json=call_json)),
                None,
            )
        if matched:
            exempted.append({**row, "exempted_by": matched})
        else:
            excluded.append(row)
    return {
        "excluded_card_ids": [r["card_id"] for r in excluded],
        "excluded": excluded,
        "exempted": exempted,
        "kept": kept,
    }
