import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  spawned: [] as string[],
  heartbeat: null as Record<string, unknown> | null,
  supabaseUrl: "http://127.0.0.1:55421",
}));

vi.mock("node:child_process", () => ({
  spawn: (cmd: string) => {
    h.spawned.push(cmd);
    return { on: () => {}, unref: () => {} };
  },
}));
vi.mock("@/lib/auth", () => ({ requireAdmin: async () => {} }));
vi.mock("@/lib/const", () => ({
  get SUPABASE_URL() {
    return h.supabaseUrl;
  },
}));
vi.mock("@/lib/supabase/admin", () => ({
  createAdminClient: () => ({
    from: () => ({
      select() {
        return this;
      },
      eq() {
        return this;
      },
      maybeSingle: async () => ({ data: h.heartbeat }),
    }),
  }),
}));

const { startWorker } = await import("./worker-control");

describe("startWorker(ローカル限定の起動)", () => {
  beforeEach(() => {
    h.spawned.length = 0;
    h.heartbeat = null;
    h.supabaseUrl = "http://127.0.0.1:55421";
  });

  it("接続先がクラウドなら起動しない(手元のプロセスは起こせない)", async () => {
    h.supabaseUrl = "https://thinker-soseki.supabase.co";
    const result = await startWorker();
    expect(h.spawned).toEqual([]);
    expect(result.error).toBeTruthy();
  });

  it("既に動いていれば起動しない(二重起動は同じジョブを二重処理する)", async () => {
    h.heartbeat = {
      status: "idle",
      current_job_id: null,
      last_seen_at: new Date().toISOString(),
    };
    const result = await startWorker();
    expect(h.spawned).toEqual([]);
    expect(result.started).toBe(false);
  });

  it("不在かつローカルなら1回だけ起動する", async () => {
    const result = await startWorker();
    expect(h.spawned).toEqual(["uv"]);
    expect(result.started).toBe(true);
  });
});
