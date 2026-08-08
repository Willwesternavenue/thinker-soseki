# ワーカー不在の可視化と起動 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/creative` でワーカーが動いていないときに警告を出し、ローカルならその場で起動できるようにする。

**Architecture:** 判定も**表示の分岐も**副作用のない純関数に置き、DBアクセス（server action）・プロセス起動（server action）・描画（client component）を分ける。コンポーネントは純関数が返した内容を描くだけにして、検証を既存の `.test.ts` の形に収める。`/admin/jobs` には触らない。

**Tech Stack:** Next.js（App Router / server actions）、TypeScript、vitest、Supabase（PostgREST）

## Global Constraints

- 設計は `docs/superpowers/specs/2026-08-08-worker-presence-design.md`。判断の理由はそちらにある
- **`frontend/src/app/admin/jobs/` を変更しないこと。** 動いている画面への波及を避けるため、判定の二重化を承知の上で受け入れている
- 不在のしきい値は **30秒**（`worker/src/main.py` の `HEARTBEAT_INTERVAL_SEC = 10` の3周期）
- 生死は `status` 列ではなく **`last_seen_at` の古さ**で判定する。ワーカーが落ちても行は最後の値のまま残る
- ハートビートの**取得に失敗したときは警告を出さない**（フェイルオープン）。「行が無い」＝不在とは区別する
- **テストはすべて `.test.ts`（TSX にしない）。** 既存11ファイルはすべて `.test.ts` で、TSX テストの前例が無い。表示の分岐を純関数へ出すことで前例作りを避ける
- テスト実行は `cd frontend && npx vitest run <path>`（`package.json` に `test` script は無い）

---

### Task 1: 生死判定と表示分岐の純関数

**Files:**
- Create: `frontend/src/lib/worker-presence.ts`
- Test: `frontend/src/lib/worker-presence.test.ts`

**Interfaces:**
- Consumes: なし
- Produces:
  - `WORKER_ALIVE_THRESHOLD_SEC: number`
  - `WORKER_START_COMMAND: string`
  - `type WorkerHeartbeat = { status: string; current_job_id: string | null; last_seen_at: string }`
  - `type WorkerPresence = "absent" | "running" | "queued" | "idle"`
  - `type WorkerStatusView = { tone: "warn" | "info"; title: string; body: string; showStartButton: boolean; showCommand: boolean } | null`
  - `workerPresence(heartbeat: WorkerHeartbeat | null, nowMs: number, myJobId?: string | null): WorkerPresence`
  - `canStartWorkerHere(supabaseUrl: string): boolean`
  - `workerStatusView(presence: WorkerPresence, canStart: boolean): WorkerStatusView`

- [ ] **Step 1: Write the failing test**

`frontend/src/lib/worker-presence.test.ts`:

```ts
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/worker-presence.test.ts`
Expected: FAIL — `Failed to resolve import "./worker-presence"`

- [ ] **Step 3: Write minimal implementation**

`frontend/src/lib/worker-presence.ts`:

```ts
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/lib/worker-presence.test.ts`
Expected: PASS（13 tests）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/worker-presence.ts frontend/src/lib/worker-presence.test.ts
git commit -m "ワーカーの生死判定と表示分岐を純関数として足す"
```

---

### Task 2: ハートビートの取得と、起動可否のページからの受け渡し

**Files:**
- Modify: `frontend/src/app/creative/actions.ts`
- Modify: `frontend/src/app/creative/page.tsx`

**Interfaces:**
- Consumes: `WorkerHeartbeat`、`canStartWorkerHere` (Task 1)
- Produces: `getWorkerHeartbeat(): Promise<{ heartbeat?: WorkerHeartbeat | null; error?: string }>`、`CreativeClient` の新規 prop `canStartWorker: boolean`

- [ ] **Step 1: server action を足す**

`frontend/src/app/creative/actions.ts` の import に追加:

```ts
import type { WorkerHeartbeat } from "@/lib/worker-presence";
```

同ファイルの末尾に追加（先頭に `"use server"` が既にある）:

```ts
/**
 * ワーカーの生存確認(創作画面のポーリング用)。
 *
 * ⚠️ 「行が無い」(= 一度も起動していない)と「取得に失敗した」を区別する。
 * 取得失敗を不在として扱うと誤警告が続き、警告そのものが無視されるようになる。
 */
export async function getWorkerHeartbeat(): Promise<{
  heartbeat?: WorkerHeartbeat | null;
  error?: string;
}> {
  await requireUser();
  const supabase = createClient();
  const { data, error } = await supabase
    .from("worker_heartbeats")
    .select("status, current_job_id, last_seen_at")
    .eq("worker_name", "ingestion")
    .maybeSingle();
  if (error) return { error: error.message };
  return { heartbeat: (data as WorkerHeartbeat) ?? null };
}
```

- [ ] **Step 2: ページから起動可否を渡す**

`frontend/src/app/creative/page.tsx` の import に追加:

```ts
import { SUPABASE_URL } from "@/lib/const";
import { canStartWorkerHere } from "@/lib/worker-presence";
```

`return` を差し替える:

```tsx
  return (
    <CreativeClient
      profiles={(data ?? []) as ProfileOption[]}
      isAdmin={auth.profile.role === "admin"}
      canStartWorker={canStartWorkerHere(SUPABASE_URL)}
    />
  );
```

`SUPABASE_URL` はサーバー側にしか無いため client component では判定できない。ここで解決して props で渡す。

- [ ] **Step 3: 型検査**

Run: `cd frontend && npx tsc --noEmit`
Expected: FAIL — `CreativeClient` に `canStartWorker` が無い旨のエラーのみ（Task 4 で解消する）

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/creative/actions.ts frontend/src/app/creative/page.tsx
git commit -m "創作画面にハートビート取得と起動可否の受け渡しを足す"
```

---

### Task 3: ローカル限定のワーカー起動

**Files:**
- Create: `frontend/src/lib/worker-control.ts`
- Test: `frontend/src/lib/worker-control.test.ts`

**Interfaces:**
- Consumes: `canStartWorkerHere`、`workerPresence`、`WorkerHeartbeat` (Task 1)
- Produces: `startWorker(): Promise<{ started?: boolean; error?: string }>`

- [ ] **Step 1: Write the failing test**

`frontend/src/lib/worker-control.test.ts`:

```ts
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/worker-control.test.ts`
Expected: FAIL — `Failed to resolve import "./worker-control"`

- [ ] **Step 3: Write the implementation**

`frontend/src/lib/worker-control.ts`:

```ts
"use server";

import { spawn } from "node:child_process";
import path from "node:path";
import { requireAdmin } from "@/lib/auth";
import { SUPABASE_URL } from "@/lib/const";
import { createAdminClient } from "@/lib/supabase/admin";
import {
  canStartWorkerHere,
  workerPresence,
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
  const { data } = await supabase
    .from("worker_heartbeats")
    .select("status, current_job_id, last_seen_at")
    .eq("worker_name", "ingestion")
    .maybeSingle();
  if (workerPresence((data as WorkerHeartbeat) ?? null, Date.now()) !== "absent") {
    return { started: false }; // 既に動いている。何もしない
  }

  // process.cwd() は frontend/。worker は隣にある
  const cwd = path.join(process.cwd(), "..", "worker");
  const child = spawn("uv", ["run", "python", "-m", "src.main"], {
    cwd,
    detached: true,
    stdio: "ignore",
    env: process.env,
  });
  // spawn の失敗(uv が見つからない等)は例外ではなく error イベントで来るため
  // try/catch では捕まらない。ここで握り、失敗は「ハートビートが出ない」ことで
  // 画面側が検知する(設計 §6 / Task 4 の起動タイムアウト)
  child.on("error", () => {});
  child.unref();

  return { started: true };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/lib/worker-control.test.ts`
Expected: PASS（3 tests）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/worker-control.ts frontend/src/lib/worker-control.test.ts
git commit -m "ローカル限定でワーカーを起動する server action を足す"
```

---

### Task 4: 表示と組み込み

**Files:**
- Create: `frontend/src/components/worker-status.tsx`
- Modify: `frontend/src/app/creative/creative-client.tsx`

**Interfaces:**
- Consumes: `workerStatusView`、`WORKER_START_COMMAND`、`WorkerPresence` (Task 1)、`getWorkerHeartbeat` (Task 2)、`startWorker` (Task 3)
- Produces: `<WorkerStatus presence canStart starting onStart error />`

表示の分岐は Task 1 の `workerStatusView` で検証済みのため、このコンポーネントに独自テストは置かない（描くだけの部品にする）。

- [ ] **Step 1: コンポーネントを作る**

`frontend/src/components/worker-status.tsx`:

```tsx
"use client";

import {
  WORKER_START_COMMAND,
  workerStatusView,
  type WorkerPresence,
} from "@/lib/worker-presence";

/**
 * ワーカーの状態表示。何を出すかは `workerStatusView`(純関数)が決める。
 * ここは描くだけにして、分岐の検証をテストしやすい側へ寄せている。
 */
export function WorkerStatus({
  presence,
  canStart,
  starting,
  onStart,
  error,
}: {
  presence: WorkerPresence;
  canStart: boolean;
  starting: boolean;
  onStart: () => void;
  error?: string | null;
}) {
  const view = workerStatusView(presence, canStart);
  if (!view) return null;

  const box =
    view.tone === "warn"
      ? "border-amber-200 bg-amber-50"
      : "border-stone-200 bg-stone-50";
  const titleColor = view.tone === "warn" ? "text-amber-800" : "text-stone-800";
  const bodyColor = view.tone === "warn" ? "text-amber-700" : "text-stone-700";

  return (
    <div className={`space-y-2 rounded-lg border px-4 py-3 ${box}`}>
      <p className={`text-sm font-medium ${titleColor}`}>{view.title}</p>
      <p className={`text-xs leading-relaxed ${bodyColor}`}>
        {view.body}
        {view.showCommand && (
          <>
            {" "}ターミナルで次を実行すると起動します:
            <code className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 font-mono">
              {WORKER_START_COMMAND}
            </code>
          </>
        )}
      </p>
      {view.showStartButton && (
        <button
          type="button"
          onClick={onStart}
          disabled={starting}
          className="rounded border border-amber-300 bg-white px-3 py-1 text-sm hover:bg-amber-50 disabled:opacity-40"
        >
          {starting ? "起動中…" : "Workerを起動"}
        </button>
      )}
      {error && <p className="text-xs text-red-700">{error}</p>}
    </div>
  );
}
```

- [ ] **Step 2: 創作画面へ組み込む**

`frontend/src/app/creative/creative-client.tsx` の import に追加:

```ts
import { getWorkerHeartbeat } from "./actions";
import { startWorker } from "@/lib/worker-control";
import { WorkerStatus } from "@/components/worker-status";
import {
  WORKER_ALIVE_THRESHOLD_SEC,
  workerPresence,
  type WorkerHeartbeat,
} from "@/lib/worker-presence";
```

コンポーネントの引数に `canStartWorker` を足す:

```ts
  canStartWorker,
}: {
  profiles: ProfileOption[];
  isAdmin: boolean;
  canStartWorker: boolean;
}) {
```

状態とポーリングを足す（既存の生成ポーリング `useEffect` の直後）:

```ts
  const [heartbeat, setHeartbeat] = useState<WorkerHeartbeat | null>(null);
  // 取得に失敗した回は判定しない(誤って「不在」と断定しないため)
  const [hbUnknown, setHbUnknown] = useState(true);
  const [startError, setStartError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  // 起動を押した時刻。ハートビートが出ないまま閾値を超えたら失敗と見なす
  const [startedAt, setStartedAt] = useState<number | null>(null);

  useEffect(() => {
    let stop = false;
    const tick = async () => {
      const { heartbeat: hb, error } = await getWorkerHeartbeat();
      if (stop) return;
      if (error) {
        setHbUnknown(true);
        return;
      }
      setHbUnknown(false);
      setHeartbeat(hb ?? null);
    };
    tick();
    const timer = setInterval(tick, POLL_MS);
    return () => {
      stop = true;
      clearInterval(timer);
    };
  }, []);

  async function handleStartWorker() {
    setStarting(true);
    setStartError(null);
    const { error } = await startWorker();
    setStarting(false);
    setStartedAt(error ? null : Date.now());
    if (error) setStartError(error);
  }
```

描画に足す（`{running && generation && <Progress ... />}` の直前）:

```tsx
      {!hbUnknown &&
        (() => {
          const presence = workerPresence(heartbeat, Date.now(), generation?.job_id);
          // 起動したのに閾値を過ぎてもハートビートが出ない = 起動に失敗した
          // (spawn の失敗は例外で来ないため、ここでしか気づけない)
          const startFailed =
            presence === "absent" &&
            startedAt != null &&
            Date.now() - startedAt > WORKER_ALIVE_THRESHOLD_SEC * 1000;
          return (
            <WorkerStatus
              presence={presence}
              canStart={canStartWorker}
              starting={starting}
              onStart={handleStartWorker}
              error={
                startFailed
                  ? "起動できませんでした。上のコマンドをターミナルで実行してください"
                  : startError
              }
            />
          );
        })()}
```

- [ ] **Step 3: 全体を確認**

Run: `cd frontend && npx vitest run && npx tsc --noEmit && npx eslint src`
Expected: 全テスト PASS、型エラーなし（Task 2 で出ていた `canStartWorker` のエラーが解消していること）、lint 指摘なし

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/worker-status.tsx frontend/src/app/creative/creative-client.tsx
git commit -m "創作画面にワーカー不在の警告と起動ボタンを出す"
```

---

### Task 5: 手動確認

**Files:** なし（確認のみ）

自動テストで押さえられない部分を実機で確認する（設計 §8）。

- [ ] **Step 1: 不在の警告が出る**

ワーカーを止め、`/creative` を開く。30秒以内に「Workerが動いていません」と起動コマンドが出ること。

- [ ] **Step 2: 起動ボタンが効く**

ボタンを押す。10〜20秒で警告が消えること。DBで確認:

```bash
docker exec supabase_db_thinker-soseki psql -U postgres -d postgres -c "select worker_name, status, now()-last_seen_at as age from worker_heartbeats;"
```

Expected: `age` が数秒以内

- [ ] **Step 3: 順番待ちが出る**

創作を依頼し、別タブで `/creative` をもう一枚開く。2枚目に「Workerは他の生成を処理中です」が出ること。

- [ ] **Step 4: クラウド接続ではボタンが出ない**

`SUPABASE_URL` をクラウドの値にして dev server を起動し、`/creative` を開く。警告は出るが**起動ボタンは出ない**こと。確認後、元の `127.0.0.1` に戻す。

- [ ] **Step 5: 結果を記録**

確認できた項目とできなかった項目を `docs/HANDOFF_2026-07-28.md` に1行で残す。
