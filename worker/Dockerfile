# worker (Python ジョブランナー) — Cloud Run / GCE 用コンテナ
# 常駐ポーリングプロセス。Cloud Runに載せる場合は $PORT でヘルスサーバが自動起動する
# (src/main.py)。環境変数: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / ANTHROPIC_API_KEY /
# OPENAI_API_KEY(.envファイルは不要。environを直接読む)

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# 依存を先に解決してレイヤーキャッシュを効かせる
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src

# 非rootで実行。-m でホーム(/home/worker)を作成しないと uv がキャッシュ
# ($HOME/.cache/uv)を作れず起動時に exit(2) する(Cloud Runのworkerユーザーで顕在化)。
ENV UV_CACHE_DIR=/home/worker/.cache/uv
RUN groupadd -r worker && useradd -r -m -g worker worker && chown -R worker:worker /app
USER worker

CMD ["uv", "run", "--no-sync", "python", "-m", "src.main"]
