"""設定値。

.env は廃止した。
- 非秘匿の設定はこのファイルの定数(必要ならシェルの環境変数で一時上書き可)。
- 秘匿キー(service_role / ANTHROPIC / OPENAI)は環境変数 → Google Cloud
  Secret Manager の順で解決する。
  - 本番(Cloud Run): --set-secrets で環境変数として注入するか、実行サービス
    アカウントに roles/secretmanager.secretAccessor を付与しておけばよい。
  - ローカル: gcloud auth application-default login(ADC)で取得できる。
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# GCP / Firebase プロジェクト(Secret Managerの参照先)
GCP_PROJECT_ID = "al-thinker-dev"

# SupabaseプロジェクトURL(公開情報)。ローカルDBで動かす時だけ環境変数で上書き
SUPABASE_URL = os.environ.get(
    "SUPABASE_URL", "https://cnhqrjmvchtqkauynevi.supabase.co"
)

# チャンク化ルールのバージョン。ルール変更時に上げる(仕様6.3)
CHUNKER_VERSION = "v1"

# モデル割当(仕様13.5)
MODEL_LIGHT_DISTILL = "claude-haiku-4-5-20251001"
MODEL_HEAVY_DISTILL = "claude-sonnet-5"
MODEL_CARD_DRAFT = "claude-sonnet-5"
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536

# 蒸留の並列数(仕様13.5: 5〜10で開始)
DISTILL_CONCURRENCY = 8

# Anthropic SDK のリトライ回数。SDKは 429/5xx(529 overloaded_error を含む)を
# 指数バックオフで自動リトライするが、既定の2回では並列蒸留中の一時的な過負荷を
# 吸収しきれず大量に失敗していたため引き上げる。
LLM_MAX_RETRIES = 8

# コスト計算用(USD / 1Mトークン)
MODEL_PRICES = {
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
    "claude-sonnet-5": {"input": 3.0, "output": 15.0},
}

# Secret Manager から取得する秘匿キー(シークレット名=環境変数名)
_SECRET_KEYS = ("SUPABASE_SERVICE_ROLE_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")


def _load_secrets() -> None:
    """環境変数に無い秘匿キーを Secret Manager から取得して注入する。"""
    missing = [k for k in _SECRET_KEYS if not os.environ.get(k)]
    if not missing:
        return
    try:
        # 遅延import: 環境変数が揃っている環境ではSDK不要
        from google.cloud import secretmanager
    except ImportError as e:
        raise RuntimeError(
            "google-cloud-secret-manager が未インストールです。"
            "worker ディレクトリで `uv sync` を実行してください。"
        ) from e
    try:
        # quota projectを明示: 複数プロジェクトを扱う開発機のADCデフォルトに依存しない
        from google.api_core.client_options import ClientOptions

        client = secretmanager.SecretManagerServiceClient(
            client_options=ClientOptions(quota_project_id=GCP_PROJECT_ID)
        )
        for key in missing:
            name = f"projects/{GCP_PROJECT_ID}/secrets/{key}/versions/latest"
            payload = client.access_secret_version(name=name).payload.data
            os.environ[key] = payload.decode("utf-8")
    except Exception as e:
        raise RuntimeError(
            f"Secret Manager からの秘匿キー取得に失敗しました({', '.join(missing)})。"
            "ローカルでは `gcloud auth application-default login` を実行し、"
            f"{GCP_PROJECT_ID} の roles/secretmanager.secretAccessor 権限を確認してください。"
            "本番では実行サービスアカウントに同権限が必要です。"
        ) from e


_load_secrets()

SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
