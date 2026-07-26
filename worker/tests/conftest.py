"""創作モードのテスト共通フィクスチャ。

DBはローカルSupabaseの実DBを使う(モックではなく実制約を検証するため)。
接続できない環境では skip する。起動方法は
docs/T1_CREATIVE_MODE_DESIGN.md「ローカル検証環境」を参照。
"""

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

from src.creative import repo

# ── ローカルSupabaseに対する結合テストの準備 ──
# モックではなく実DBで検証する(制約・既定値・トリガの効き方まで確認できるため)。
# ローカルスタックが起動していない環境では skip する。起動方法は
# docs/T1_CREATIVE_MODE_DESIGN.md「ローカル検証環境」を参照。
#
# 接続情報は `supabase status -o json` から実行時に取得する。
# キーをソースへ書かないこと(ローカル専用の値でもGitHubのシークレット検出に掛かる)。
REPO_ROOT = Path(__file__).resolve().parents[2]


def _local_connection() -> tuple[str, str] | None:
    """ローカルスタックの (API URL, secret key)。取得できなければ None。"""
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_LOCAL_SECRET")
    if url and key:
        return url, key
    try:
        out = subprocess.run(
            ["supabase", "status", "-o", "json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # 起動メッセージが混ざるためJSON部分だけを取り出す
    body = out.stdout[out.stdout.find("{") :]
    try:
        status = json.loads(body)
    except json.JSONDecodeError:
        return None
    api, secret = status.get("API_URL"), status.get("SECRET_KEY")
    return (api, secret) if api and secret else None


@pytest.fixture(scope="module")
def client():
    from supabase import create_client

    conn = _local_connection()
    if not conn:
        pytest.skip(
            "ローカルSupabaseの接続情報を取得できないため skip"
            "(`supabase start` するか SUPABASE_URL / SUPABASE_LOCAL_SECRET を設定)"
        )
    try:
        c = create_client(*conn)
        c.table("creative_profiles").select("profile_id").limit(1).execute()
    except Exception as exc:  # noqa: BLE001 - 接続不可の理由をそのまま skip 理由にする
        pytest.skip(f"ローカルSupabaseに接続できないため skip: {exc}")
    return c


@pytest.fixture
def clean_corpus(client):
    """コーパス層のテーブルを前後で空にする。

    canonical_works / work_editions は person 単位で作られないテストもあるため、
    profile フィクスチャの後片付けではカバーできない。取り込みは冪等なので
    テスト間で残骸が残ると件数の検証が崩れる。
    """
    def _wipe():
        # FKの順に消す。sources は work_editions を参照し、
        # source_chunks は sources の cascade で一緒に消える。
        client.table("canonical_work_review_queue").delete().neq(
            "queue_id", "00000000-0000-0000-0000-000000000000").execute()
        client.table("aozora_manifest_entries").delete().neq("entry_id", "").execute()
        client.table("sources").delete().neq("edition_id", "").execute()
        client.table("work_editions").delete().neq("edition_id", "").execute()
        client.table("canonical_works").delete().neq("canonical_work_id", "").execute()

    _wipe()
    yield client
    _wipe()


@pytest.fixture
def profile(client):
    """テスト用の人物・プロファイルを作り、後片付けする。"""
    pid = f"test_{uuid.uuid4().hex[:8]}"
    client.table("personas").upsert(
        {"person_id": pid, "display_name": "テスト人物"}
    ).execute()
    profile_id = f"cp_{uuid.uuid4().hex[:8]}"
    client.table("creative_profiles").insert(
        {
            "profile_id": profile_id,
            "person_id": pid,
            "name": "テストプロファイル",
            "slug": profile_id,
            "orthography_policy": "新字新仮名",
            "disclosure_text": "AIが生成した創作物です。",
            "display_title_format": "{title}（AI創作）",
            "status": "active",
        }
    ).execute()
    yield profile_id
    # FKの順に消す。creative_traces は generations の cascade、
    # source_chunks は sources の cascade で一緒に消える。
    client.table("creative_generations").delete().eq("profile_id", profile_id).execute()
    client.table("creative_cards").delete().eq("profile_id", profile_id).execute()
    client.table("creative_profiles").delete().eq("profile_id", profile_id).execute()
    client.table("sources").delete().eq("person_id", pid).execute()
    client.table("personas").delete().eq("person_id", pid).execute()


def _new_job(client, profile_id, **over):
    row = {
        "profile_id": profile_id,
        "brief_raw": {"motif": "鏡"},
        "generation_settings": {"use_rag": True, "use_cards": True, "rules": "off"},
        "created_by": "test",
    }
    row.update(over)
    return client.table("creative_generations").insert(row).execute().data[0]
