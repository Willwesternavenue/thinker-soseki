# frontend (Next.js 16) — コンテナ実行用(素のCloud Runで動かすフォールバック手段。
# 通常デプロイは Firebase App Hosting: apphosting.yaml / docs/FIREBASE_MIGRATION.md 参照)。
# 秘匿キーは実行時にSecret Managerから取得するため、ビルド引数は不要。

FROM node:24-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:24-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:24-slim AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
# 非rootで実行
RUN groupadd -r nextjs && useradd -r -g nextjs nextjs
COPY --from=builder --chown=nextjs:nextjs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nextjs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nextjs /app/public ./public
USER nextjs
# Cloud Run は $PORT を注入する。standaloneサーバーは PORT/HOSTNAME を尊重する
ENV HOSTNAME=0.0.0.0
EXPOSE 8080
CMD ["node", "server.js"]
