"""Creative Output Guard(T1設計書 §5 / 仕様§8)。

既存の Output Guard(思想モード)とは別レイヤ。既存側には手を入れない。
閾値は creative_profiles.default_generation_settings.guard から読む
(コードへ直書きしない。仕様§8.1)。
"""

import re
import unicodedata

from .. import config, llm
from . import prompts

# 設定が無いときの既定値(T1設計書 §5)。profile側の設定が常に優先される。
DEFAULT_GUARD_SETTINGS = {
    "ngram_n": 10,
    "lcs_threshold": 20,
    "ngram_overlap_ratio_max": 0.05,
    "max_regenerations": 2,
}

# 真作と誤認させる定型句(仕様§8.2)。機械検査で確実に弾く。
MISATTRIBUTION_PATTERNS = (
    "未発表",
    "発見された",
    "真作",
    "本人が書いた",
    "本人の作品",
    "遺稿",
)

# 空白・約物。生成文と原典で同一の正規化をかけてから比較する(仕様§5.1-1)
_NOISE_RE = re.compile(r"[\s、。，．・「」『』（）()\[\]〔〕【】…―ー−—’'\"“”！？!?：:；;]")


def normalize_text(text: str) -> str:
    """比較用の正規化。NFKC → 空白・約物除去。"""
    return _NOISE_RE.sub("", unicodedata.normalize("NFKC", text or ""))


def longest_common_substring(a: str, b: str) -> str:
    """最長共通部分文字列。長い連続一致(=転載)の検出に使う。

    日本語は空白区切りが使えないため、単語ではなく文字単位で見る(仕様§8.1)。
    """
    if not a or not b:
        return ""
    # 直前行のみ保持するDP(原典全文と比較するためメモリを抑える)
    prev = [0] * (len(b) + 1)
    best_len = 0
    best_end = 0
    for i, ch_a in enumerate(a, start=1):
        cur = [0] * (len(b) + 1)
        for j, ch_b in enumerate(b, start=1):
            if ch_a == ch_b:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best_len:
                    best_len = cur[j]
                    best_end = i
        prev = cur
    return a[best_end - best_len : best_end]


def _ngrams(text: str, n: int) -> set[str]:
    if n <= 0 or len(text) < n:
        return set()
    return {text[i : i + n] for i in range(len(text) - n + 1)}


def ngram_overlap_ratio(draft: str, source_texts: list[str], *, n: int) -> float:
    """生成文のn-gramのうち、原典側にも存在する比率。

    定型句の偶然一致を許容しつつ、広範囲の転写を検出する(仕様§8.1)。
    """
    draft_grams = _ngrams(normalize_text(draft), n)
    if not draft_grams:
        return 0.0
    source_grams: set[str] = set()
    for text in source_texts:
        source_grams |= _ngrams(normalize_text(text), n)
    return len(draft_grams & source_grams) / len(draft_grams)


def check_similarity(draft: str, sources: list[dict], settings: dict) -> dict:
    """原文類似検査(機械検査。仕様§8.1)。

    sources は {"chunk_id", "text"} のリスト。違反時は該当箇所と
    対応する chunk_id を記録して管理者が確認できるようにする。
    """
    lcs_threshold = settings.get(
        "lcs_threshold", DEFAULT_GUARD_SETTINGS["lcs_threshold"]
    )
    n = settings.get("ngram_n", DEFAULT_GUARD_SETTINGS["ngram_n"])
    ratio_max = settings.get(
        "ngram_overlap_ratio_max", DEFAULT_GUARD_SETTINGS["ngram_overlap_ratio_max"]
    )

    norm_draft = normalize_text(draft)
    best_text = ""
    matched_chunk_ids: list[str] = []
    for chunk in sources:
        common = longest_common_substring(norm_draft, normalize_text(chunk["text"]))
        if len(common) > len(best_text):
            best_text = common
        if len(common) >= lcs_threshold:
            matched_chunk_ids.append(chunk["chunk_id"])

    ratio = ngram_overlap_ratio(draft, [c["text"] for c in sources], n=n)
    lcs_violation = len(best_text) >= lcs_threshold
    ratio_violation = ratio > ratio_max

    return {
        "passed": not (lcs_violation or ratio_violation),
        "lcs_len": len(best_text),
        "lcs_text": best_text[:200],
        "ngram_ratio": round(ratio, 4),
        "matched_chunk_ids": matched_chunk_ids,
        "thresholds": {
            "lcs_threshold": lcs_threshold,
            "ngram_n": n,
            "ngram_overlap_ratio_max": ratio_max,
        },
    }


def check_misattribution(draft: str) -> dict:
    """真作と誤認させる表現の検査(仕様§8.2)。"""
    normalized = normalize_text(draft)
    matched = [p for p in MISATTRIBUTION_PATTERNS if normalize_text(p) in normalized]
    return {"passed": not matched, "matched": matched}


def check_prohibitions(draft: str, cards: list[dict], *, job_id=None, call_json=None) -> dict:
    """承認済み prohibition カードへの違反をLLMで判定する(仕様§8.3)。

    prohibition 以外のカードは判定対象にしない。0枚ならLLMを呼ばない。
    """
    prohibitions = [c for c in cards if c.get("card_type") == "prohibition"]
    if not prohibitions:
        return {"passed": True, "violations": [], "checked_card_ids": []}

    call = call_json or llm.call_json
    listed = "\n".join(
        f"- [{c['card_id']}] {c['title']}"
        + (f": {c.get('description')}" if c.get("description") else "")
        for c in prohibitions
    )
    result = call(
        agent_name="creative_guard_judge",
        model=config.MODEL_CREATIVE_LIGHT,
        system=prompts.GUARD_JUDGE_SYSTEM,
        prompt=prompts.GUARD_JUDGE_PROMPT.format(prohibitions=listed, draft=draft),
        # agent_runs.job_id は ingestion_jobs へのFKのため渡さない(T1設計書 §3.1.1)
        input_ref=f"creative_generation:{job_id}",
    )
    violations = result.get("violations") or []
    return {
        "passed": not violations,
        "violations": violations,
        "checked_card_ids": [c["card_id"] for c in prohibitions],
    }


def run_guard(
    draft: str, *, sources: list[dict], cards: list[dict], settings: dict,
    job_id=None, call_json=None,
) -> dict:
    """Guard全体を実行する(機械検査 → LLM judge。仕様§8)。

    戻り値の violations は再生成プロンプトへそのまま渡せる日本語の理由リスト。
    作家固有の創作制約(similarity/prohibitions)とシステム安全規則
    (misattribution)は別キーに分けて保存する(仕様§8.4)。
    """
    similarity = check_similarity(draft, sources, settings)
    misattribution = check_misattribution(draft)
    prohibitions = check_prohibitions(draft, cards, job_id=job_id, call_json=call_json)

    violations: list[str] = []
    if not similarity["passed"]:
        violations.append(
            f"原文との類似が閾値を超えている(最長一致{similarity['lcs_len']}文字"
            f"「{similarity['lcs_text'][:40]}」/ n-gram重複{similarity['ngram_ratio']})。"
            "原典の表現をなぞらず、自分の言葉で書き直すこと。"
        )
    if not misattribution["passed"]:
        violations.append(
            f"原作者本人の作品と誤認させる表現がある({'、'.join(misattribution['matched'])})。"
            "本文から取り除くこと。"
        )
    for v in prohibitions["violations"]:
        violations.append(f"禁止事項違反({v.get('card_id')}): {v.get('reason')}")

    return {
        "passed": not violations,
        "similarity": similarity,
        "misattribution": misattribution,
        "prohibitions": prohibitions,
        "violations": violations,
    }
