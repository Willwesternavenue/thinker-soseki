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


# ── 開発DBを消さないための安全装置 ──
#
# clean_corpus は実DBのコーパス層を全消しする。接続先は SUPABASE_URL から来るので、
# 秘匿キーを source した状態で pytest を叩くと**開発DBがそのまま対象になる**。
# 2026-07-30 の作業ではこれで4回消し、そのたびに199MBの退避から復元した。
#
# 対策は2段。
#   1. 専用の接続情報(SUPABASE_TEST_URL / SUPABASE_TEST_SECRET)があればそちらを使う
#   2. 無ければ、消す前に「本物のコーパスではないか」を確認して止める
#
# 2の判定は行数で行う。テスト用スタックは空か、テストが作った数十行しかない。
# 本物は source_chunks が1万行を超える(2026-07-30 時点で 10,152)。
WIPE_SAFETY_MAX_CHUNKS = 500
ALLOW_WIPE_ENV = "SOSEKI_ALLOW_DESTRUCTIVE_TESTS"


def _test_connection() -> tuple[str, str] | None:
    """テスト専用スタックの接続情報。設定されていれば最優先で使う。"""
    url = os.environ.get("SUPABASE_TEST_URL")
    key = os.environ.get("SUPABASE_TEST_SECRET")
    return (url, key) if url and key else None


def _local_connection() -> tuple[str, str] | None:
    """ローカルスタックの (API URL, secret key)。取得できなければ None。"""
    dedicated = _test_connection()
    if dedicated:
        return dedicated
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


def _assert_safe_to_wipe(client) -> None:
    """本物のコーパスを消しにいっていないかを確かめる。

    ⚠️ この確認を外す前に、必ず退避を取ること（引き継ぎの退避手順を参照）。
    テスト専用スタックを使うのが本筋で、`SOSEKI_ALLOW_DESTRUCTIVE_TESTS=1` は
    退避済みだと分かっている場合の逃げ道。
    """
    if os.environ.get(ALLOW_WIPE_ENV) == "1":
        return
    if _test_connection():
        return  # 専用スタックを明示的に指している
    try:
        rows = (
            client.table("source_chunks")
            .select("chunk_id")
            .limit(WIPE_SAFETY_MAX_CHUNKS + 1)
            .execute()
            .data
            or []
        )
    except Exception:  # noqa: BLE001 - 数えられないなら安全側で止めない
        return
    if len(rows) > WIPE_SAFETY_MAX_CHUNKS:
        pytest.skip(
            f"接続先に source_chunks が{WIPE_SAFETY_MAX_CHUNKS}行以上あり、"
            "本物のコーパスに見えるため破壊的テストを行いません。"
            "テスト専用スタックを SUPABASE_TEST_URL / SUPABASE_TEST_SECRET で"
            f"指すか、退避を取った上で {ALLOW_WIPE_ENV}=1 を設定してください。"
        )


@pytest.fixture
def clean_corpus(client):
    """コーパス層のテーブルを前後で空にする。

    canonical_works / work_editions は person 単位で作られないテストもあるため、
    profile フィクスチャの後片付けではカバーできない。取り込みは冪等なので
    テスト間で残骸が残ると件数の検証が崩れる。
    """
    _assert_safe_to_wipe(client)

    def _wipe():
        # FKの順に消す。sources は work_editions を参照し、
        # source_chunks は sources の cascade で一緒に消える。
        client.table("canonical_work_review_queue").delete().neq(
            "queue_id", "00000000-0000-0000-0000-000000000000").execute()
        client.table("aozora_manifest_entries").delete().neq("entry_id", "").execute()
        # コーパス由来の創作カードも消す(C-T6 が evidence_chunk_ids で紐づくため、
        # 残っていると「既存カードはスキップ」の判定でテストが干渉する)。
        # creative_generations → creative_profiles の順に消す(FKのため)。
        client.table("creative_generations").delete().neq("profile_id", "").execute()
        client.table("creative_cards").delete().neq("card_id", "").execute()
        client.table("creative_profiles").delete().neq("profile_id", "").execute()
        # コーパス由来の思想カード(C-T6)も消す。残っていると「既存カードはスキップ」
        # の判定に引っ掛かり、次のテストで1枚も作られなくなる。
        # judgment_rule_evidence → judgment_rules → thought_cards の順(FKのため)。
        client.table("judgment_rule_evidence").delete().neq("rule_id", "").execute()
        client.table("judgment_rule_versions").delete().neq("rule_id", "").execute()
        client.table("judgment_rules").delete().neq("rule_id", "").execute()
        client.table("thought_evidence_links").delete().neq("link_id", "").execute()
        # 代表質問はカードを参照する。先に消さないと thought_cards の削除がFKで落ちる
        client.table("thought_questions").delete().neq("question_id", "").execute()
        client.table("thought_cards").delete().neq("card_id", "").execute()
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
