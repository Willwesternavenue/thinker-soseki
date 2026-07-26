import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Cloud Run等のコンテナ実行用に自己完結サーバーを出力(node .next/standalone/server.js)。
  // Vercel上でも無害(Vercelは独自のビルド出力を使う)
  output: "standalone",
};

export default nextConfig;
