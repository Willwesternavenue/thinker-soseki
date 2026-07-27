"""Claude API呼び出しの共通処理。JSON出力と agent_runs へのコスト記録。"""

import json
import re

from anthropic import Anthropic

from . import config, db

class LLMResponseTruncated(RuntimeError):
    """応答が max_tokens で打ち切られた。

    切れたJSONは「Unterminated string」という原因の分かりにくいエラーになるため、
    パースを試みる前にここで明確に落とす。
    """


def ensure_not_truncated(stop_reason: str | None, *, agent_name: str, max_tokens: int) -> None:
    """打ち切られた応答をそのまま使わない。"""
    if stop_reason == "max_tokens":
        raise LLMResponseTruncated(
            f"応答が max_tokens({max_tokens})で打ち切られました(agent={agent_name})。"
            "出力が長い処理では max_tokens を上げること。"
        )


_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(
            api_key=config.ANTHROPIC_API_KEY,
            max_retries=config.LLM_MAX_RETRIES,
        )
    return _client


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    prices = config.MODEL_PRICES.get(model, {"input": 0, "output": 0})
    return (input_tokens * prices["input"] + output_tokens * prices["output"]) / 1_000_000


def call_json(
    *,
    agent_name: str,
    model: str,
    system: str,
    prompt: str,
    input_ref: str,
    job_id: str | None = None,
    max_tokens: int = 2048,
) -> dict:
    """ClaudeにJSONを生成させ、結果とコストを agent_runs に記録して返す。"""
    client = _get_client()
    try:
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        ensure_not_truncated(
            getattr(message, "stop_reason", None),
            agent_name=agent_name,
            max_tokens=max_tokens,
        )
        text = "".join(b.text for b in message.content if b.type == "text")
        result = _parse_json(text)
        cost = _estimate_cost(
            model, message.usage.input_tokens, message.usage.output_tokens
        )
        db.log_agent_run(
            job_id=job_id,
            agent_name=agent_name,
            model=model,
            input_ref=input_ref,
            output_json=result,
            status="success",
            cost=cost,
        )
        return result
    except Exception as exc:
        db.log_agent_run(
            job_id=job_id,
            agent_name=agent_name,
            model=model,
            input_ref=input_ref,
            output_json={"error": str(exc)},
            status="error",
            cost=0,
        )
        raise


def _parse_json(text: str) -> dict:
    """コードフェンス付き・前置き付きのJSON出力も許容してパースする。

    ⚠️ `strict=False` にしている。長文(小説の本文など)を生成させると、
    LLMがJSON文字列の中へ生の改行やタブをそのまま出すことがあり、標準の
    パースでは "Invalid control character" で落ちる。JSONの仕様上は不正だが
    実運用では頻繁に起きるため許容する。緩めるのは制御文字の扱いだけで、
    正しいJSONの解釈は変わらない。
    """
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return json.loads(fence.group(1), strict=False)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1], strict=False)
    return json.loads(text, strict=False)
