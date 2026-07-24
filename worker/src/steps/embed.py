"""Embedding生成(仕様6.4)。OpenAI text-embedding-3-small(1536次元)。"""

from openai import OpenAI

from .. import config

BATCH_SIZE = 64
# embeddingモデルの入力上限対策(概ね8kトークン。日本語は1文字≒1トークン想定で安全側に切る)
MAX_INPUT_CHARS = 6000

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _client


def embed_texts(texts: list[str]) -> list[list[float]]:
    """テキスト群をバッチでembeddingする。順序は入力と対応する。"""
    results: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = [t[:MAX_INPUT_CHARS] if t else " " for t in texts[i : i + BATCH_SIZE]]
        response = _get_client().embeddings.create(
            model=config.EMBEDDING_MODEL, input=batch
        )
        results.extend([d.embedding for d in response.data])
    return results
