import { describe, expect, it } from "vitest";
import {
  canStartWorkerHere,
  workerPresence,
  workerStatusView,
  type WorkerHeartbeat,
} from "./worker-presence";

const NOW = new Date("2026-08-08T00:01:00Z").getTime();

const hb = (over: Partial<WorkerHeartbeat> = {}): WorkerHeartbeat => ({
  status: "idle",
  current_job_id: null,
  last_seen_at: new Date(NOW - 5_000).toISOString(),
  ...over,
});

describe("workerPresence(ワーカーの生死)", () => {
  it("ハートビートが無ければ不在", () => {
    expect(workerPresence(null, NOW)).toBe("absent");
  });

  it("30秒以上更新が無ければ不在。status が processing でも信じない", () => {
    // ワーカーが落ちても行は最後の値のまま残る。実測で6日前の
    // status: processing が残っていた
    const dead = hb({
      status: "processing",
      last_seen_at: new Date(NOW - 31_000).toISOString(),
    });
    expect(workerPresence(dead, NOW)).toBe("absent");
  });

  it("29秒前なら生存(境界)", () => {
    const alive = hb({ last_seen_at: new Date(NOW - 29_000).toISOString() });
    expect(workerPresence(alive, NOW)).toBe("idle");
  });

  it("自分のジョブを処理中なら running", () => {
    expect(workerPresence(hb({ current_job_id: "j1" }), NOW, "j1")).toBe("running");
  });

  it("他のジョブを処理中なら queued(順番待ち)", () => {
    expect(workerPresence(hb({ current_job_id: "j2" }), NOW, "j1")).toBe("queued");
  });

  it("待つジョブを渡さない画面では、生存していれば idle", () => {
    expect(workerPresence(hb({ current_job_id: "j2" }), NOW)).toBe("idle");
  });
});

describe("canStartWorkerHere(起動ボタンを出してよいか)", () => {
  it("接続先がローカルなら出す", () => {
    expect(canStartWorkerHere("http://127.0.0.1:55421")).toBe(true);
    expect(canStartWorkerHere("http://localhost:55421")).toBe(true);
  });

  it("クラウドなら出さない(押しても手元のプロセスは起こせない)", () => {
    expect(canStartWorkerHere("https://thinker-soseki.supabase.co")).toBe(false);
  });

  it("URLとして壊れていれば出さない", () => {
    expect(canStartWorkerHere("")).toBe(false);
  });
});

describe("workerStatusView(何を表示するか)", () => {
  it("処理中・待機中は何も出さない", () => {
    expect(workerStatusView("running", true)).toBeNull();
    expect(workerStatusView("idle", true)).toBeNull();
  });

  it("不在なら警告と起動コマンドを出す", () => {
    const v = workerStatusView("absent", false);
    expect(v?.tone).toBe("warn");
    expect(v?.showCommand).toBe(true);
  });

  it("ローカルのときだけ起動ボタンを出す", () => {
    expect(workerStatusView("absent", true)?.showStartButton).toBe(true);
    expect(workerStatusView("absent", false)?.showStartButton).toBe(false);
  });

  it("順番待ちは警告ではなく案内で、起動ボタンもコマンドも出さない", () => {
    const v = workerStatusView("queued", true);
    expect(v?.tone).toBe("info");
    expect(v?.showStartButton).toBe(false);
    expect(v?.showCommand).toBe(false);
  });
});
