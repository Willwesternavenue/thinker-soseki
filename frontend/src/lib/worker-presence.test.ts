import { describe, expect, it } from "vitest";
import {
  WORKER_ALIVE_THRESHOLD_SEC,
  canStartWorkerHere,
  uvCandidatePaths,
  nextStartWatch,
  workerPresence,
  workerStartOutcome,
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

describe("uvCandidatePaths(uv の探し先)", () => {
  it("PATH の各要素に uv を付けて候補にする", () => {
    const got = uvCandidatePaths("/usr/bin:/opt/homebrew/bin", "/Users/x");
    expect(got.slice(0, 2)).toEqual(["/usr/bin/uv", "/opt/homebrew/bin/uv"]);
  });

  it("PATH に無くても既知のインストール先を候補に含める", () => {
    // 2026-08-08 実測: dev server の PATH に ~/.local/bin が無く起動できなかった。
    // uv 公式インストーラの既定はここで、PATH へ足すのはシェルのプロファイル
    const got = uvCandidatePaths("/usr/bin", "/Users/x");
    expect(got).toContain("/Users/x/.local/bin/uv");
  });

  it("PATH が未設定でも候補を返す(落ちない)", () => {
    expect(uvCandidatePaths(undefined, "/Users/x").length).toBeGreaterThan(0);
  });

  it("同じ場所を二度探さない", () => {
    const got = uvCandidatePaths("/opt/homebrew/bin:/opt/homebrew/bin", "/Users/x");
    expect(got.filter((p) => p === "/opt/homebrew/bin/uv")).toHaveLength(1);
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

/** 既定は「ローカルの管理者が、依頼を待っている」状態 */
const view = (
  presence: Parameters<typeof workerStatusView>[0],
  over: Partial<Parameters<typeof workerStatusView>[1]> = {}
) =>
  workerStatusView(presence, {
    canStart: true,
    isAdmin: true,
    hasPendingJob: true,
    ...over,
  });

describe("workerStatusView(何を表示するか)", () => {
  it("処理中・待機中は何も出さない", () => {
    expect(view("running")).toBeNull();
    expect(view("idle")).toBeNull();
  });

  it("不在なら警告を出す", () => {
    expect(view("absent")?.tone).toBe("warn");
  });

  it("ローカルの管理者にだけ起動ボタンを出す", () => {
    expect(view("absent")?.showStartButton).toBe(true);
    // クラウド接続では押しても手元のプロセスは起こせない
    expect(view("absent", { canStart: false })?.showStartButton).toBe(false);
    // 非管理者が押すと startWorker が例外を投げる(lib/auth.ts の requireAdmin)
    expect(view("absent", { isAdmin: false })?.showStartButton).toBe(false);
  });

  it("起動コマンドは管理者にだけ出す(実行できない人に手順を出さない)", () => {
    expect(view("absent")?.showCommand).toBe(true);
    // 本番構成(frontend はクラウド / worker は手元)でも管理者には手順が要る
    expect(view("absent", { canStart: false })?.showCommand).toBe(true);
    const tester = view("absent", { canStart: false, isAdmin: false });
    expect(tester?.showCommand).toBe(false);
    expect(tester?.body).toContain("管理者");
  });

  it("待っているジョブが無ければ「保存されている」と言わない", () => {
    expect(view("absent", { hasPendingJob: false })?.body).not.toContain("保存");
    expect(view("absent")?.body).toContain("保存");
  });

  it("順番待ちは警告ではなく案内で、起動ボタンもコマンドも出さない", () => {
    const v = view("queued");
    expect(v?.tone).toBe("info");
    expect(v?.showStartButton).toBe(false);
    expect(v?.showCommand).toBe(false);
    // 同じプロセスが取り込み・蒸留も回すため「他の生成」とは限らない
    expect(v?.title).not.toContain("生成");
  });
});

const THRESHOLD_MS = WORKER_ALIVE_THRESHOLD_SEC * 1000;

describe("workerStartOutcome(起動を押したあとの状態)", () => {
  it("押していなければ何も言わない", () => {
    expect(workerStartOutcome("absent", null, NOW)).toBeNull();
  });

  it("閾値の内側は起動中(この間ボタンを無効にして二重起動を減らす)", () => {
    expect(workerStartOutcome("absent", NOW - THRESHOLD_MS, NOW)).toBe("starting");
  });

  it("閾値を過ぎてもハートビートが出なければ失敗", () => {
    expect(workerStartOutcome("absent", NOW - THRESHOLD_MS - 1, NOW)).toBe("failed");
  });

  it("ハートビートが出ていれば成功(閾値を過ぎていても失敗と言わない)", () => {
    expect(workerStartOutcome("idle", NOW - THRESHOLD_MS - 1, NOW)).toBeNull();
    expect(workerStartOutcome("running", NOW - THRESHOLD_MS - 1, NOW)).toBeNull();
  });
});

describe("nextStartWatch(起動待ちをいつ降ろすか)", () => {
  it("不在のままなら待ち続ける", () => {
    expect(nextStartWatch(NOW, "absent")).toBe(NOW);
  });

  it("ハートビートが出たら降ろす", () => {
    expect(nextStartWatch(NOW, "idle")).toBeNull();
  });

  it("押していなければ何もしない", () => {
    expect(nextStartWatch(null, "absent")).toBeNull();
  });

  it("起動成功の数分後に落ちても「起動できませんでした」にはならない", () => {
    // 押した → まだ不在 → ハートビートが出た(成功) → 数分後に落ちた
    let watch: number | null = NOW;
    watch = nextStartWatch(watch, workerPresence(null, NOW));
    expect(workerStartOutcome("absent", watch, NOW + 1_000)).toBe("starting");

    watch = nextStartWatch(watch, "idle");
    const laterCrash = NOW + 300_000;
    expect(workerStartOutcome("absent", nextStartWatch(watch, "absent"), laterCrash)).toBeNull();
  });
});
