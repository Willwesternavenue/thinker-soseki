"""Claude API呼び出しの共通処理。JSON出力と agent_runs へのコスト記録。"""

import json
import re

from anthropic import Anthropic

from . import config, db

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
    """コードフェンス付き・前置き付きのJSON出力も許容してパースする。"""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    return json.loads(text)
