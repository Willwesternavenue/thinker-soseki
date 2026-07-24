"""Supabaseクライアント(service_role、RLSバイパス。仕様9章)。"""

from functools import lru_cache

from supabase import Client, create_client

from . import config


@lru_cache(maxsize=1)
def client() -> Client:
    if not config.SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY が設定されていません"
            "(Secret Manager へのアクセス権とADCを確認。docs/FIREBASE_MIGRATION.md参照)"
        )
    return create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)


def select_in(
    table: str,
    columns: str,
    column: str,
    values: list,
    *,
    batch: int = 100,
    **eq,
) -> list[dict]:
    """`.in_(column, values)` を分割実行する。

    大きい原典では chunk_id が数百件になり、URLが長すぎて 414(URI Too Large)
    になるため、values をバッチに区切って照会し結果を結合する。
    """
    out: list[dict] = []
    for i in range(0, len(values), batch):
        query = client().table(table).select(columns).in_(column, values[i : i + batch])
        for key, val in eq.items():
            query = query.eq(key, val)
        out.extend(query.execute().data)
    return out


def update_in(table: str, patch: dict, column: str, values: list, *, batch: int = 100) -> None:
    """`.in_(column, values)` の一括UPDATEを分割実行する(414対策)。"""
    for i in range(0, len(values), batch):
        client().table(table).update(patch).in_(column, values[i : i + batch]).execute()


def log_agent_run(
    *,
    job_id: str | None,
    agent_name: str,
    model: str,
    input_ref: str,
    output_json: dict,
    status: str,
    cost: float,
) -> None:
    """LLM呼び出しを agent_runs に記録する(仕様5.15)。"""
    client().table("agent_runs").insert(
        {
            "job_id": job_id,
            "agent_name": agent_name,
            "model": model,
            "input_ref": input_ref,
            "output_json": output_json,
            "status": status,
            "cost": cost,
        }
    ).execute()
