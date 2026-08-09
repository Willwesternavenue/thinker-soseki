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

/**
 * 起動したワーカーの出力を落とす先(worker/ からの相対)。
 *
 * ⚠️ 起動失敗の理由はここにしか残らない。ワーカーは起動直後に落ちうるが、
 * 画面には30秒後の「起動できませんでした」しか出ない。
 */
export const WORKER_START_LOG = ".worker-start.log";

/**
 * `uv` 実行ファイルの探し先。PATH を先に、既知のインストール先を後ろに置く。
 *
 * ⚠️ **dev server の PATH は、あなたがターミナルで使っている PATH とは限らない。**
 * uv の公式インストーラは `~/.local/bin` に置くが、そこを PATH へ足すのは
 * シェルのプロファイル(.zshrc 等)であって、そこを経由せずに起動された Node には
 * 引き継がれない。2026-08-08、起動ボタンがまさにこれで失敗した
 * (`spawn("uv", ...)` が ENOENT。理由は握りつぶされ、画面には
 * 「起動できませんでした」としか出なかった)。
 */
export function uvCandidatePaths(
  pathEnv: string | undefined,
  homeDir: string
): string[] {
  const fromPath = (pathEnv ?? "")
    .split(":")
    .filter(Boolean)
    .map((dir) => `${dir}/uv`);
  const wellKnown = [
    `${homeDir}/.local/bin/uv`, // uv 公式インストーラの既定
    "/opt/homebrew/bin/uv", // Homebrew (Apple Silicon)
    "/usr/local/bin/uv", // Homebrew (Intel)
  ];
  return [...new Set([...fromPath, ...wellKnown])];
}

/**
 * ワーカーへ渡してはいけない環境変数。
 *
 * ⚠️ `env: process.env` をそのまま渡すと、**Next.js の値がワーカーの意味に
 * 化ける**。`PORT` がその例で、ワーカーは `PORT` があると Cloud Run 上だと
 * 判断してヘルスサーバを立てる(`worker/src/main.py` の `__main__`)。
 * dev server の 5555 を渡すと `Address already in use` で即死する
 * (2026-08-08 の実測。起動ボタンが動かなかった真の原因)。
 */
export const WORKER_ENV_BLOCKLIST = ["PORT"] as const;

/**
 * 親の環境から、ワーカーに渡してはいけないものを落とす。
 * 入力の型をそのまま返す(`process.env` を渡しても `ProcessEnv` のまま)。
 */
export function workerChildEnv<T extends Record<string, string | undefined>>(
  parentEnv: T
): T {
  const child: T = { ...parentEnv };
  for (const key of WORKER_ENV_BLOCKLIST) {
    delete (child as Record<string, string | undefined>)[key];
  }
  return child;
}

export type WorkerHeartbeat = {
  /** 判定には使わない。落ちた行は最後の値のまま残るため(下の workerPresence 参照) */
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

export type WorkerStatusContext = {
  /** 接続先がローカルで、この画面から起こせるプロセスと同じDBを見るか */
  canStart: boolean;
  /** 起動する権限があるか。無いまま押すと startWorker が例外を投げる(lib/auth.ts) */
  isAdmin: boolean;
  /** 処理を待っているジョブがあるか。無ければ「依頼は保存されています」は嘘になる */
  hasPendingJob: boolean;
};

/** 表示の分岐。コンポーネントはこの結果を描くだけにする(検証を純関数に寄せる)。 */
export function workerStatusView(
  presence: WorkerPresence,
  { canStart, isAdmin, hasPendingJob }: WorkerStatusContext
): WorkerStatusView {
  if (presence === "queued") {
    return {
      tone: "info",
      // 同じプロセスが ingestion_jobs / distillation_jobs も回す(worker/src/main.py)
      // ため、待たされている原因は創作の生成とは限らない
      title: "Workerは他の処理を実行中です",
      body: "順番が来るまでお待ちください。",
      showStartButton: false,
      showCommand: false,
    };
  }
  if (presence !== "absent") return null;
  const body = hasPendingJob
    ? "依頼は保存されていますが、処理するWorkerが起動していないため始まりません。"
    : "いま依頼しても、処理するWorkerが起動していないため始まりません。";
  return {
    tone: "warn",
    title: "Workerが動いていません",
    // 起動手段を持たない人には手順ではなく連絡先を出す(押せないボタンを
    // 出さないのと同じ理由。設計 §2)
    body: isAdmin ? body : `${body}管理者に連絡してください。`,
    showStartButton: canStart && isAdmin,
    showCommand: isAdmin,
  };
}

/**
 * 起動を押したあとの状態。押した時刻を渡し、ハートビートが出るまでを "starting"、
 * 閾値を過ぎても出ないままなら "failed" とする。
 *
 * "starting" の間ボタンを無効にして二重起動の窓を狭める(設計 §5.2)。spawn の
 * 失敗は例外で来ないため、失敗はここでしか気づけない(設計 §6)。
 */
export function workerStartOutcome(
  presence: WorkerPresence,
  startedAt: number | null,
  nowMs: number
): "starting" | "failed" | null {
  if (startedAt == null) return null;
  if (presence !== "absent") return null; // ハートビートが出た = 起動できた
  return nowMs - startedAt > WORKER_ALIVE_THRESHOLD_SEC * 1000 ? "failed" : "starting";
}

/**
 * 起動待ちを続けるか。ハートビートが出た時点で降ろす。
 * 降ろさないと、起動に成功した数分後にワーカーが落ちたとき
 * 「起動できませんでした」という誤った原因を出してしまう。
 */
export function nextStartWatch(
  startedAt: number | null,
  presence: WorkerPresence
): number | null {
  if (startedAt == null) return null;
  return presence === "absent" ? startedAt : null;
}
