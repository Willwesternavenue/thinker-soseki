import { describe, expect, it } from "vitest";
import {
  buildRerunPayload,
  classifyJob,
  formatElapsed,
  isWorkerAlive,
  summarizeGuard,
} from "./monitoring";

const NOW = new Date("2026-07-27T12:00:00Z").getTime();
const ago = (sec: number) => new Date(NOW - sec * 1000).toISOString();

describe("isWorkerAlive(Workerが動いているか)", () => {
  it("直近のheartbeatがあれば稼働中", () => {
    expect(isWorkerAlive(ago(5), NOW)).toBe(true);
  });

  it("しきい値を超えたら停止とみなす", () => {
    expect(isWorkerAlive(ago(120), NOW)).toBe(false);
  });

  it("一度も起動されていなければ停止", () => {
    expect(isWorkerAlive(null, NOW)).toBe(false);
  });
});

describe("classifyJob(ジョブの実態)", () => {
  const running = { job_id: "j1", status: "running", updated_at: ago(10) };

  it("Workerがそのジョブを処理していれば active", () => {
    expect(classifyJob(running, { workerAlive: true, workerJobId: "j1" })).toBe("active");
  });

  it("running なのにWorkerが別のジョブを見ていたら stalled", () => {
    expect(classifyJob(running, { workerAlive: true, workerJobId: "j2" })).toBe("stalled");
  });

  it("running なのにWorkerが停止していたら stalled(経過時間が当てにならない)", () => {
    expect(classifyJob(running, { workerAlive: false, workerJobId: null })).toBe("stalled");
  });

  it("pending はWorkerが生きていれば順番待ち", () => {
    const pending = { job_id: "j1", status: "pending", updated_at: ago(3) };
    expect(classifyJob(pending, { workerAlive: true, workerJobId: null })).toBe("queued");
    expect(classifyJob(pending, { workerAlive: false, workerJobId: null })).toBe(
      "waiting_worker"
    );
  });

  it("終了したジョブはWorkerの状態に左右されない", () => {
    const done = { job_id: "j1", status: "succeeded", updated_at: ago(999) };
    expect(classifyJob(done, { workerAlive: false, workerJobId: null })).toBe("succeeded");
    expect(classifyJob({ ...done, status: "failed" }, { workerAlive: true, workerJobId: "j1" }))
      .toBe("failed");
  });
});

describe("formatElapsed(経過時間)", () => {
  it("分と秒で読める形にする", () => {
    expect(formatElapsed(45)).toBe("45秒");
    expect(formatElapsed(60)).toBe("1分");
    expect(formatElapsed(125)).toBe("2分5秒");
  });

  it("負の値でも壊れない", () => {
    expect(formatElapsed(-3)).toBe("0秒");
  });
});

describe("summarizeGuard(Guard結果の要約)", () => {
  const guard = {
    passed: true,
    similarity: { passed: true, lcs_len: 7, lcs_text: "こんな夢を見た", ngram_ratio: 0 },
    violations: [],
  };

  it("一覧に出せる形に畳む", () => {
    expect(summarizeGuard(guard)).toEqual({
      passed: true,
      lcsLen: 7,
      lcsText: "こんな夢を見た",
      ngramRatio: 0,
      violations: [],
    });
  });

  it("違反の理由を落とさない", () => {
    const failed = {
      ...guard,
      passed: false,
      violations: ["原典との連続一致が長すぎます(32字)"],
    };
    expect(summarizeGuard(failed).violations).toEqual([
      "原典との連続一致が長すぎます(32字)",
    ]);
  });

  it("trace が無いジョブでも落ちない", () => {
    expect(summarizeGuard(null)).toEqual({
      passed: null,
      lcsLen: null,
      lcsText: null,
      ngramRatio: null,
      violations: [],
    });
    expect(summarizeGuard({}).passed).toBeNull();
  });
});

describe("buildRerunPayload(再実行)", () => {
  const job = {
    job_id: "old-job",
    profile_id: "cp_yume_juya",
    brief_raw: { motif: "鏡" },
    generation_settings: { use_cards: true },
    idempotency_key: "old-key",
    status: "failed",
    current_step: "guard",
    final_text: null,
    error_message: "guard_exhausted: ...",
  };

  it("依頼内容と設定をそのまま引き継ぐ", () => {
    const payload = buildRerunPayload(job, "new-key", "admin-uid");
    expect(payload.profile_id).toBe("cp_yume_juya");
    expect(payload.brief_raw).toEqual({ motif: "鏡" });
    expect(payload.generation_settings).toEqual({ use_cards: true });
  });

  it("idempotency_key を必ず新しくする", () => {
    // 使い回すと一意制約に当たって元のジョブが返り、再実行が黙って何もしない
    expect(buildRerunPayload(job, "new-key", "admin-uid").idempotency_key).toBe("new-key");
  });

  it("前回の結果は引き継がない", () => {
    const payload = buildRerunPayload(job, "new-key", "admin-uid");
    expect(payload).not.toHaveProperty("job_id");
    expect(payload).not.toHaveProperty("status");
    expect(payload).not.toHaveProperty("current_step");
    expect(payload).not.toHaveProperty("final_text");
    expect(payload).not.toHaveProperty("error_message");
  });

  it("再実行した人を記録する(元の依頼者ではなく)", () => {
    expect(buildRerunPayload(job, "new-key", "admin-uid").created_by).toBe("admin-uid");
  });
});
