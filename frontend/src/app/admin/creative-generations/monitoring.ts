/**
 * 創作生成ジョブ監視(T5 admin)の純粋ロジック。
 *
 * 既存の `/admin/jobs`(取り込みジョブ)と同じ考え方を、創作ジョブの形に合わせたもの。
 * `status` の見た目をそのまま信じないのが要点で、running のまま取り残された
 * ジョブ(Worker が落ちた・別ジョブへ移った)を区別する。
 */

/** heartbeat がこの秒数以内なら Worker は生きているとみなす。 */
export const ALIVE_THRESHOLD_SEC = 30;

export function isWorkerAlive(lastSeenAt: string | null | undefined, now: number): boolean {
  if (!lastSeenAt) return false;
  return (now - new Date(lastSeenAt).getTime()) / 1000 < ALIVE_THRESHOLD_SEC;
}

export type JobState =
  | "active"
  | "stalled"
  | "queued"
  | "waiting_worker"
  | "succeeded"
  | "failed";

/**
 * ジョブの実態を判定する。
 *
 * `running` の表示だけでは「今まさに処理中」か「取り残された」かが分からない。
 * Worker は単一プロセス前提なので、Worker がそのジョブを見ていない running は
 * すべて孤児(次回起動時に `_reclaim_orphaned_jobs` が pending へ戻す)。
 * 経過時間の表示も当てにならないため、ここで分けておく。
 */
export function classifyJob(
  job: { job_id: string; status: string },
  ctx: { workerAlive: boolean; workerJobId: string | null }
): JobState {
  if (job.status === "succeeded") return "succeeded";
  if (job.status === "failed") return "failed";
  if (job.status === "running") {
    return ctx.workerAlive && ctx.workerJobId === job.job_id ? "active" : "stalled";
  }
  return ctx.workerAlive ? "queued" : "waiting_worker";
}

export const STATE_LABELS: Record<JobState, string> = {
  active: "処理中",
  stalled: "中断（要再実行）",
  queued: "順番待ち",
  waiting_worker: "Worker待ち",
  succeeded: "完了",
  failed: "失敗",
};

export function formatElapsed(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  if (s < 60) return `${s}秒`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem ? `${m}分${rem}秒` : `${m}分`;
}

export type GuardSummary = {
  passed: boolean | null;
  lcsLen: number | null;
  lcsText: string | null;
  ngramRatio: number | null;
  violations: string[];
};

/**
 * Guard 結果を一覧に出せる形へ畳む。
 *
 * 失敗ジョブでも trace は必ず書かれる(仕様§15.2)が、Step5 に届く前に落ちた場合は
 * guard_results が空になる。「判定していない」と「通った」を混同しないよう、
 * 未判定は null にする。
 */
export function summarizeGuard(
  guardResults: Record<string, unknown> | null | undefined
): GuardSummary {
  const g = guardResults ?? {};
  const similarity = (g.similarity ?? {}) as Record<string, unknown>;
  return {
    passed: typeof g.passed === "boolean" ? g.passed : null,
    lcsLen: typeof similarity.lcs_len === "number" ? similarity.lcs_len : null,
    lcsText: typeof similarity.lcs_text === "string" ? similarity.lcs_text : null,
    ngramRatio: typeof similarity.ngram_ratio === "number" ? similarity.ngram_ratio : null,
    violations: Array.isArray(g.violations) ? (g.violations as string[]) : [],
  };
}

export type RerunPayload = {
  profile_id: string;
  brief_raw: unknown;
  generation_settings: unknown;
  idempotency_key: string;
  created_by: string;
};

/**
 * 再実行するジョブの中身。**必ず新しい idempotency_key を振る**。
 *
 * 使い回すと `creative_generations.idempotency_key` の一意制約に当たり、
 * 元のジョブが返ってくるだけで再実行が黙って何もしない状態になる。
 * 前回の結果(status / current_step / final_text / error_message)は引き継がない。
 */
export function buildRerunPayload(
  job: {
    profile_id: string;
    brief_raw: unknown;
    generation_settings: unknown;
  },
  newIdempotencyKey: string,
  actorId: string
): RerunPayload {
  return {
    profile_id: job.profile_id,
    brief_raw: job.brief_raw,
    generation_settings: job.generation_settings,
    idempotency_key: newIdempotencyKey,
    // 再実行を指示した人を記録する(元の依頼者ではない)
    created_by: actorId,
  };
}
