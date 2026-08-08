import { beforeEach, describe, expect, it, vi } from "vitest";

type SpawnCall = { cmd: string; args: string[]; options: Record<string, unknown> };

const h = vi.hoisted(() => ({
  spawned: [] as SpawnCall[],
  spawnedChild: null as { on: ReturnType<typeof vi.fn>; unref: ReturnType<typeof vi.fn> } | null,
  heartbeat: null as Record<string, unknown> | null,
  hbError: null as { message: string } | null,
  supabaseUrl: "http://127.0.0.1:55421",
  /** 非nullなら requireAdmin がこのメッセージで例外を投げる(権限不足の再現) */
  adminError: null as string | null,
}));

vi.mock("node:child_process", () => ({
  spawn: (cmd: string, args: string[], options: Record<string, unknown>) => {
    h.spawned.push({ cmd, args, options });
    const child = { on: vi.fn(), unref: vi.fn() };
    h.spawnedChild = child;
    return child;
  },
}));
vi.mock("@/lib/auth", () => ({
  requireAdmin: async () => {
    // 本物は戻り値ではなく例外で拒否する(lib/auth.ts)。呼び出し側の
    // try/catch 漏れを検出したいので、ここでも例外にする
    if (h.adminError) throw new Error(h.adminError);
  },
}));
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
      maybeSingle: async () => ({ data: h.heartbeat, error: h.hbError }),
    }),
  }),
}));

const { startWorker } = await import("./worker-control");

describe("startWorker(ローカル限定の起動)", () => {
  beforeEach(() => {
    h.spawned.length = 0;
    h.spawnedChild = null;
    h.heartbeat = null;
    h.hbError = null;
    h.supabaseUrl = "http://127.0.0.1:55421";
    h.adminError = null;
  });

  it("管理者でなければ起動しない。戻り値ではなく例外で拒否する", async () => {
    h.adminError = "forbidden: admin権限が必要です";
    // 呼び出し側(creative-client)が try/catch を持たないと、ボタンが
    // 「起動中…」のまま固まって理由も出ない
    await expect(startWorker()).rejects.toThrow(/forbidden/);
    expect(h.spawned).toEqual([]);
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
    expect(h.spawned).toHaveLength(1);
    const [call] = h.spawned;
    expect(call.cmd).toBe("uv");
    expect(call.args).toEqual(["run", "python", "-m", "src.main"]);
    // frontend の隣の worker/ で動かさないとモジュールが見つからない
    expect(call.options.cwd).toMatch(/[\\/]worker$/);
    // detached が無いと、Next.js を止めたときにワーカーも道連れで死ぬ
    expect(call.options.detached).toBe(true);
    expect(call.options.stdio).toBe("ignore");
    expect(result.started).toBe(true);
    // spawn失敗はerrorイベントで来るため、拾わないと未処理例外になる
    expect(h.spawnedChild?.on).toHaveBeenCalledWith("error", expect.any(Function));
    // 親プロセス終了後もワーカーが生き続けられるようunrefしておく
    expect(h.spawnedChild?.unref).toHaveBeenCalled();
  });

  it("生存確認の取得に失敗したら起動しない(取得失敗を不在と扱わない)", async () => {
    h.hbError = { message: "network error" };
    const result = await startWorker();
    expect(h.spawned).toEqual([]);
    expect(result.error).toBeTruthy();
    expect(result.started).toBeUndefined();
  });
});
