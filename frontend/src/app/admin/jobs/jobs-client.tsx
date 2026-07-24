"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchJobsData,
  type HeartbeatData as Heartbeat,
  type JobRowData as Job,
} from "./actions";
import { Pagination, paginate } from "@/components/pagination";
import { RerunButton } from "./rerun-button";

const PER_PAGE = 100;
const STATUS_FILTERS = [
  ["all", "すべて"],
  ["pending", "待機"],
  ["running", "処理中"],
  ["succeeded", "完了"],
  ["failed", "失敗"],
] as const;

// 処理ステップの並びと日本語ラベル(進捗表示に使う)
const STEPS = [
  ["extract", "テキスト抽出"],
  ["clean", "整形・話者正規化"],
  ["chunk", "チャンク分割"],
  ["embed", "検索インデックス"],
  ["distill_light", "要約(軽蒸留)"],
] as const;
const STEP_LABEL = Object.fromEntries(STEPS) as Record<string, string>;

// heartbeatがこの秒数以内なら「稼働中」とみなす
const ALIVE_THRESHOLD_SEC = 30;
const POLL_MS = 2500;

function jobTitle(job: Job): string | null {
  const s = Array.isArray(job.sources) ? job.sources[0] : job.sources;
  return s?.title ?? null;
}

export function JobsClient() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [heartbeat, setHeartbeat] = useState<Heartbeat | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [loaded, setLoaded] = useState(false);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [page, setPage] = useState(0);

  const refresh = useCallback(async () => {
    const { jobs, heartbeat } = await fetchJobsData();
    setJobs(jobs ?? []);
    setHeartbeat(heartbeat ?? null);
    setNow(Date.now());
    setLoaded(true);
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, POLL_MS);
    // 経過時間表示のため1秒ごとに時計だけ進める
    const clock = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      clearInterval(interval);
      clearInterval(clock);
    };
  }, [refresh]);

  const workerAlive =
    heartbeat != null &&
    (now - new Date(heartbeat.last_seen_at).getTime()) / 1000 < ALIVE_THRESHOLD_SEC;
  const hasPending = jobs.some(
    (j) => j.status === "pending" || j.status === "running"
  );

  const statusCounts = useMemo(() => {
    const m: Record<string, number> = { all: jobs.length };
    for (const j of jobs) m[j.status] = (m[j.status] ?? 0) + 1;
    return m;
  }, [jobs]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return jobs.filter((j) => {
      if (statusFilter !== "all" && j.status !== statusFilter) return false;
      if (!q) return true;
      return [j.source_id, jobTitle(j)]
        .filter(Boolean)
        .some((v) => v!.toLowerCase().includes(q));
    });
  }, [jobs, query, statusFilter]);

  const { pageItems, pageCount, clampedPage } = paginate(
    filtered,
    page,
    PER_PAGE
  );

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">蒸留ジョブ</h1>
        <span className="text-xs text-stone-400">数秒ごとに自動更新</span>
      </div>

      <WorkerBanner
        alive={workerAlive}
        heartbeat={heartbeat}
        hasPending={hasPending}
        now={now}
      />

      {/* 状態タブ + 検索 */}
      <div className="flex flex-wrap items-center gap-2">
        {STATUS_FILTERS.map(([key, label]) => (
          <button
            key={key}
            onClick={() => {
              setStatusFilter(key);
              setPage(0);
            }}
            className={`rounded-full px-3 py-1 text-xs ${
              statusFilter === key
                ? "bg-blue-700 text-white"
                : "bg-stone-100 text-stone-600 hover:bg-stone-200"
            }`}
          >
            {label} <span className="opacity-70">{statusCounts[key] ?? 0}</span>
          </button>
        ))}
        <span className="flex-1" />
        <input
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setPage(0);
          }}
          placeholder="原典ID・タイトルで検索"
          className="rounded border border-stone-300 bg-white px-3 py-2 text-sm"
        />
      </div>

      <div className="overflow-x-auto rounded-lg border border-stone-200">
        <table className="w-full text-sm">
          <thead className="bg-white text-left text-stone-600">
            <tr>
              <th className="px-4 py-2">原典</th>
              <th className="px-4 py-2">状態</th>
              <th className="px-4 py-2 w-1/3">進捗</th>
              <th className="px-4 py-2">経過 / 更新</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {pageItems.map((job) => (
              <JobRow
                key={job.job_id}
                job={job}
                now={now}
                workerAlive={workerAlive}
                workerSourceId={
                  heartbeat?.status === "processing"
                    ? heartbeat.current_source_id
                    : null
                }
              />
            ))}
            {loaded && !filtered.length && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-stone-500">
                  {jobs.length ? "該当なし" : "ジョブがまだありません"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <Pagination page={clampedPage} pageCount={pageCount} onPage={setPage} />

      <p className="text-xs text-stone-500">
        目安: 1原典あたり数十秒〜数分。ほとんどの時間は「要約(軽蒸留)」に使われ、
        チャンク数 ÷ 8 × 数秒が目安です(例: 300チャンクの書籍で約2〜3分)。
        動画・短い資料は数十秒で終わります。
      </p>
    </div>
  );
}

function WorkerBanner({
  alive,
  heartbeat,
  hasPending,
  now,
}: {
  alive: boolean;
  heartbeat: Heartbeat | null;
  hasPending: boolean;
  now: number;
}) {
  if (alive) {
    const processing = heartbeat?.status === "processing";
    return (
      <div className="flex items-center gap-3 rounded-lg border border-green-200 bg-green-50 px-4 py-3">
        <span className="relative flex h-2.5 w-2.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-500 opacity-75" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-green-600" />
        </span>
        <div className="text-sm">
          <span className="font-medium text-green-800">Worker稼働中</span>
          <span className="ml-2 text-green-700">
            {processing
              ? `処理中(${heartbeat?.current_source_id ?? ""})`
              : "待機中(新しいジョブを監視しています)"}
          </span>
        </div>
      </div>
    );
  }

  // 未起動 or 停止
  const lastSeen = heartbeat?.last_seen_at
    ? `最終確認 ${fmtElapsed((now - new Date(heartbeat.last_seen_at).getTime()) / 1000)}前`
    : "一度も起動されていません";
  return (
    <div className="space-y-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
      <div className="flex items-center gap-3">
        <span className="inline-flex h-2.5 w-2.5 rounded-full bg-stone-400" />
        <span className="text-sm font-medium text-amber-800">
          Workerが停止しています
        </span>
        <span className="text-xs text-amber-700">({lastSeen})</span>
      </div>
      <p className="text-xs leading-relaxed text-amber-700">
        {hasPending
          ? "未処理のジョブがありますが、処理するWorkerが動いていません。"
          : "ジョブを処理するにはWorkerを起動してください。"}
        ターミナルで次を実行すると起動します:
        <code className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 font-mono">
          cd worker &amp;&amp; uv run python -m src.main
        </code>
      </p>
    </div>
  );
}

function JobRow({
  job,
  now,
  workerAlive,
  workerSourceId,
}: {
  job: Job;
  now: number;
  workerAlive: boolean;
  workerSourceId: string | null;
}) {
  const stepIndex = job.current_step
    ? STEPS.findIndex(([key]) => key === job.current_step)
    : -1;
  const elapsedSec = (now - new Date(job.updated_at).getTime()) / 1000;
  // このジョブが今まさにWorkerに処理されているか
  const isActive =
    job.status === "running" && workerAlive && workerSourceId === job.source_id;
  // running表示なのにWorkerが実処理していない → 取り残し(ゾンビ)。経過時間は当てにならない
  const stalled = job.status === "running" && !isActive;
  const title = jobTitle(job);

  return (
    <tr className="border-t border-stone-200 align-top">
      <td className="px-4 py-2">
        <div className="text-stone-800">{title ?? job.source_id}</div>
        {title && (
          <div className="font-mono text-[11px] text-stone-400">{job.source_id}</div>
        )}
      </td>
      <td className="px-4 py-2">
        <JobStatus status={job.status} stalled={stalled} />
        {job.error_message && (
          <p className="mt-1 max-w-md text-xs text-red-700">{job.error_message}</p>
        )}
      </td>
      <td className="px-4 py-2">
        {isActive && stepIndex >= 0 ? (
          <StepProgress stepIndex={stepIndex} />
        ) : stalled ? (
          <span className="text-xs text-amber-700">中断(要再実行)</span>
        ) : job.status === "succeeded" ? (
          <span className="text-xs text-green-700">完了</span>
        ) : job.status === "pending" ? (
          <span className="text-xs text-stone-500">
            {workerAlive ? "順番待ち" : "Worker待ち"}
          </span>
        ) : (
          <span className="text-xs text-stone-400">—</span>
        )}
      </td>
      <td className="px-4 py-2 text-xs text-stone-500">
        {isActive ? (
          <span className="text-blue-700">{fmtElapsed(elapsedSec)}経過</span>
        ) : (
          new Date(job.updated_at).toLocaleTimeString("ja-JP")
        )}
      </td>
      <td className="px-4 py-2">
        {(job.status === "failed" || stalled) && (
          <RerunButton jobId={job.job_id} />
        )}
      </td>
    </tr>
  );
}

function StepProgress({ stepIndex }: { stepIndex: number }) {
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-stone-200">
          <div
            className="h-full rounded-full bg-blue-600 transition-all duration-500"
            style={{ width: `${((stepIndex + 1) / STEPS.length) * 100}%` }}
          />
        </div>
        <span className="text-xs tabular-nums text-stone-500">
          {stepIndex + 1}/{STEPS.length}
        </span>
      </div>
      <p className="text-xs text-blue-700">{STEP_LABEL[STEPS[stepIndex][0]]}</p>
    </div>
  );
}

function JobStatus({ status, stalled }: { status: string; stalled: boolean }) {
  if (stalled) {
    return (
      <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">
        中断
      </span>
    );
  }
  const map: Record<string, string> = {
    succeeded: "bg-green-100 text-green-800",
    failed: "bg-red-100 text-red-700",
    running: "bg-blue-100 text-blue-800",
    pending: "bg-stone-200 text-stone-600",
  };
  const label: Record<string, string> = {
    succeeded: "完了",
    failed: "失敗",
    running: "処理中",
    pending: "待機",
  };
  return (
    <span className={`rounded px-2 py-0.5 text-xs ${map[status] ?? ""}`}>
      {label[status] ?? status}
    </span>
  );
}

function fmtElapsed(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  if (s < 60) return `${s}秒`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem ? `${m}分${rem}秒` : `${m}分`;
}
