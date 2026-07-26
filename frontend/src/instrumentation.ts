/**
 * Next.jsサーバー起動時フック。秘匿キーをSecret Managerからprocess.envへ注入する。
 * ビルド(prerender)時はスキップし、リクエストを受けるサーバーでのみ実行する。
 */
export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  if (process.env.NEXT_PHASE === "phase-production-build") return;
  const { loadSecrets } = await import("@/lib/secrets");
  await loadSecrets();
}
