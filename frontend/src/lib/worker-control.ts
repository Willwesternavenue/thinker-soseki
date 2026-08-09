"use server";

import { spawn } from "node:child_process";
import { accessSync, constants } from "node:fs";
import os from "node:os";
import path from "node:path";
import { requireAdmin } from "@/lib/auth";
import { SUPABASE_URL } from "@/lib/const";
import { createAdminClient } from "@/lib/supabase/admin";
import {
  canStartWorkerHere,
  uvCandidatePaths,
  workerPresence,
  WORKER_START_COMMAND,
  type WorkerHeartbeat,
} from "@/lib/worker-presence";

/**
 * ワーカーをこのマシンで起動する。ローカル接続のときだけ動く。
 *
 * ⚠️ 二重起動の窓が残る(設計 §5.2)。`claim_next_generation` は非排他
 * (単一worker前提)なので、2つ起動すると同じジョブを二重処理する。
 * ここでの生存確認から、起動したプロセスが最初のハートビートを書くまで
 * 最大10秒あり、その間の連打は防げない。塞ぐには worker 側の advisory lock が
 * 要るが、今回は採らないと決めている。
 */
export async function startWorker(): Promise<{ started?: boolean; error?: string }> {
  await requireAdmin();

  if (!canStartWorkerHere(SUPABASE_URL)) {
    return { error: "接続先がローカルではないため、この画面からは起動できません" };
  }

  const supabase = createAdminClient();
  const { data, error } = await supabase
    .from("worker_heartbeats")
    .select("status, current_job_id, last_seen_at")
    .eq("worker_name", "ingestion")
    .maybeSingle();
  // 取得失敗を「不在」と扱わない(設計 §6)。data は取得失敗時も null になり、
  // workerPresence(null, ...) は "absent" を返すため、ここで弾かないと
  // 稼働中でも起動してしまい二重起動(=同じジョブの二重処理)につながる。
  if (error) return { error: error.message };
  if (workerPresence((data as WorkerHeartbeat) ?? null, Date.now()) !== "absent") {
    return { started: false }; // 既に動いている。何もしない
  }

  // ⚠️ `spawn("uv", ...)` と名前で呼ばない。dev server の PATH は、あなたが
  // ターミナルで使っている PATH とは限らない(2026-08-08 の実測: ~/.local/bin が
  // 無く ENOENT)。しかも spawn の失敗は例外ではなく error イベントで来るので、
  // 名前で呼ぶと「理由の分からない失敗」になる。先に実体を解決して、
  // 見つからないなら**その場で理由を返す**。
  const uv = uvCandidatePaths(process.env.PATH, os.homedir()).find((candidate) => {
    try {
      accessSync(candidate, constants.X_OK);
      return true;
    } catch {
      return false;
    }
  });
  if (!uv) {
    return {
      error: `uv が見つかりませんでした。ターミナルで次を実行してください: ${WORKER_START_COMMAND}`,
    };
  }

  // process.cwd() は frontend/。worker は隣にある
  const cwd = path.join(process.cwd(), "..", "worker");
  const child = spawn(uv, ["run", "python", "-m", "src.main"], {
    cwd,
    detached: true,
    stdio: "ignore",
    env: process.env,
  });
  // ここまで来ての失敗(worker 側の依存解決など)は error イベントで来る。
  // 画面へは返せない(既に return した後)ので、せめてサーバーログに残す。
  // 無言にすると、この機能が問題にしている「静かな劣化」と同じ形になる
  child.on("error", (err) => {
    console.error("ワーカーの起動に失敗:", err);
  });
  child.unref();

  return { started: true };
}
