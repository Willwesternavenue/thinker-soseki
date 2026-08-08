/**
 * ワーカーの生死判定と、それをどう見せるかの決定(純関数)。
 *
 * ⚠️ `/admin/jobs` は同等の生死判定を jobs-client.tsx 内に自前で持っている
 * (`ALIVE_THRESHOLD_SEC = 30`)。動いている画面へ波及させない判断をしたため、
 * **意図的に二重になっている**。しきい値を変えるときは両方直すこと。
 * 設計: docs/superpowers/specs/2026-08-08-worker-presence-design.md
 */

/** worker/src/main.py の HEARTBEAT_INTERVAL_SEC = 10 の3周期 */
export const WORKER_ALIVE_THRESHOLD_SEC = 30;

/** 画面に出す起動コマンド(ボタンを出せない環境ではこれを案内する) */
export const WORKER_START_COMMAND = "cd worker && uv run python -m src.main";

export type WorkerHeartbeat = {
  status: string;
  current_job_id: string | null;
  last_seen_at: string;
};

export type WorkerPresence = "absent" | "running" | "queued" | "idle";

export type WorkerStatusView = {
  tone: "warn" | "info";
  title: string;
  body: string;
  showStartButton: boolean;
  showCommand: boolean;
} | null;

/**
 * @param myJobId 待っているジョブ。渡さない画面では running/queued を区別しない
 */
export function workerPresence(
  heartbeat: WorkerHeartbeat | null,
  nowMs: number,
  myJobId?: string | null
): WorkerPresence {
  if (!heartbeat?.last_seen_at) return "absent";
  const ageSec = (nowMs - new Date(heartbeat.last_seen_at).getTime()) / 1000;
  // status ではなく最終更新の古さで見る(落ちた行は最後の値のまま残るため)
  if (ageSec >= WORKER_ALIVE_THRESHOLD_SEC) return "absent";
  if (!myJobId || !heartbeat.current_job_id) return "idle";
  return heartbeat.current_job_id === myJobId ? "running" : "queued";
}

/**
 * 起動ボタンを描画してよいか。
 *
 * 判定に SUPABASE_URL を使うのは、「これから起動するワーカーが、いま画面が
 * 見ているDBと同じDBを見るか」と条件が一致するため。NODE_ENV では本番ビルドを
 * ローカルで動かしたときにずれる。
 */
export function canStartWorkerHere(supabaseUrl: string): boolean {
  try {
    const host = new URL(supabaseUrl).hostname;
    return host === "localhost" || host === "127.0.0.1";
  } catch {
    return false;
  }
}

/** 表示の分岐。コンポーネントはこの結果を描くだけにする(検証を純関数に寄せる)。 */
export function workerStatusView(
  presence: WorkerPresence,
  canStart: boolean
): WorkerStatusView {
  if (presence === "queued") {
    return {
      tone: "info",
      title: "Workerは他の生成を処理中です",
      body: "順番が来るまでお待ちください。",
      showStartButton: false,
      showCommand: false,
    };
  }
  if (presence !== "absent") return null;
  return {
    tone: "warn",
    title: "Workerが動いていません",
    body: "依頼は保存されていますが、処理するWorkerが起動していないため始まりません。",
    showStartButton: canStart,
    showCommand: true,
  };
}
