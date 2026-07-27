"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { STEPS, failureMessage, stepProgress } from "@/app/creative/creative";
import {
  fetchCreativeGenerations,
  rerunCreativeGeneration,
  type GenerationRow,
  type HeartbeatRow,
} from "./actions";
import {
  STATE_LABELS,
  classifyJob,
  formatElapsed,
  isWorkerAlive,
  type JobState,
} from "./monitoring";

const POLL_MS = 3000;

const FILTERS: [string, string][] = [
  ["all", "すべて"],
  ["pending", "待機"],
  ["running", "処理中"],
  ["succeeded", "完了"],
  ["failed", "失敗"],
];

export function GenerationsClient({
  initialRows,
  initialHeartbeat,
  initialProfiles,
  initialError,
}: {
  initialRows: GenerationRow[];
  initialHeartbeat: HeartbeatRow | null;
  initialProfiles: Record<string, string>;
  initialError: string | null;
}) {
  const [rows, setRows] = useState<GenerationRow[]>(initialRows);
  const [heartbeat, setHeartbeat] = useState<HeartbeatRow | null>(initialHeartbeat);
  const [profiles, setProfiles] = useState<Record<string, string>>(initialProfiles);
  const [now, setNow] = useState(() => Date.now());
  const [filter, setFilter] = useState("all");
  const [error, setError] = useState<string | null>(initialError);

  const refresh = useCallback(async () => {
    const result = await fetchCreativeGenerations();
    if (result.error) return setError(result.error);
    setError(null);
    setRows(result.generations ?? []);
    setHeartbeat(result.heartbeat ?? null);
    setProfiles(result.profiles ?? {});
    setNow(Date.now());
  }, []);

  useEffect(() => {
    const poll = setInterval(refresh, POLL_MS);
    // 経過時間の表示のため1秒ごとに時計だけ進める
    const clock = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      clearInterval(poll);
      clearInterval(clock);
    };
  }, [refresh]);

  const workerAlive = isWorkerAlive(heartbeat?.last_seen_at, now);
  const ctx = { workerAlive, workerJobId: heartbeat?.current_job_id ?? null };

  const counts = useMemo(() => {
    const m: Record<string, number> = { all: rows.length };
    for (const r of rows) m[r.status] = (m[r.status] ?? 0) + 1;
    return m;
  }, [rows]);

  const filtered = filter === "all" ? rows : rows.filter((r) => r.status === filter);
  const hasWaiting = rows.some((r) => r.status === "pending" || r.status === "running");

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-xl font-bold">創作生成ジョブ</h1>
          <p className="mt-1 text-sm text-stone-600">
            創作は<strong className="text-stone-800">失敗しても本文を保存しません</strong>。
            失敗の原因は Creative Trace と Guard にだけ残ります。
          </p>
        </div>
        <span className="text-xs text-stone-400">数秒ごとに自動更新</span>
      </div>

      <WorkerBanner alive={workerAlive} heartbeat={heartbeat} hasWaiting={hasWaiting} now={now} />

      {error && (
        <p className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800">{error}</p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setFilter(key)}
            className={`rounded-full px-3 py-1 text-xs ${
              filter === key
                ? "bg-blue-700 text-white"
                : "bg-stone-100 text-stone-600 hover:bg-stone-200"
            }`}
          >
            {label} <span className="opacity-70">{counts[key] ?? 0}</span>
          </button>
        ))}
      </div>

      <div className="overflow-x-auto rounded-lg border border-stone-200">
        <table className="w-full text-sm">
          <thead className="bg-white text-left text-stone-600">
            <tr>
              <th className="px-4 py-2">依頼</th>
              <th className="px-4 py-2">状態</th>
              <th className="px-4 py-2 w-1/4">進捗</th>
              <th className="px-4 py-2">経過</th>
              <th className="px-4 py-2" />
            </tr>
          </thead>
          <tbody>
            {filtered.map((row) => (
              <Row
                key={row.job_id}
                row={row}
                state={classifyJob(row, ctx)}
                profileName={profiles[row.profile_id] ?? row.profile_id}
                now={now}
                onRerun={refresh}
              />
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-stone-500">
                  {rows.length ? "該当なし" : "生成ジョブがまだありません"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Row({
  row,
  state,
  profileName,
  now,
  onRerun,
}: {
  row: GenerationRow;
  state: JobState;
  profileName: string;
  now: number;
  onRerun: () => void;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const elapsed = (now - new Date(row.updated_at).getTime()) / 1000;
  const motif = (row.brief_raw?.motif as string | undefined) ?? "(モチーフ未指定)";
  const progress = stepProgress(row.current_step);

  async function handleRerun() {
    setPending(true);
    setError(null);
    const result = await rerunCreativeGeneration(row.job_id);
    setPending(false);
    if (result.error) return setError(result.error);
    onRerun();
  }

  return (
    <tr className="border-t border-stone-200 align-top">
      <td className="px-4 py-2">
        <div className="text-stone-800">{row.display_title ?? motif}</div>
        <div className="text-xs text-stone-500">{profileName}</div>
        <Link
          href={`/admin/creative-generations/${row.job_id}`}
          className="font-mono text-[11px] text-blue-700 underline"
        >
          {row.job_id.slice(0, 8)}
        </Link>
      </td>
      <td className="px-4 py-2">
        <StateBadge state={state} />
        {state === "failed" && (
          <p className="mt-1 max-w-xs text-xs text-red-700">
            {failureMessage(row.error_message).title}
          </p>
        )}
        {error && <p className="mt-1 max-w-xs text-xs text-red-700">{error}</p>}
      </td>
      <td className="px-4 py-2">
        {state === "active" ? (
          <div className="space-y-1">
            <div className="text-xs text-stone-600">
              {progress.label}（{progress.index}/{progress.total}）
            </div>
            <div className="h-1.5 overflow-hidden rounded bg-stone-100">
              <div
                className="h-full bg-blue-600 transition-[width] duration-500"
                style={{ width: `${(progress.index / STEPS.length) * 100}%` }}
              />
            </div>
          </div>
        ) : state === "stalled" ? (
          <span className="text-xs text-amber-700">
            {progress.label} で中断（Workerが処理していません）
          </span>
        ) : (
          <span className="text-xs text-stone-400">—</span>
        )}
      </td>
      <td className="px-4 py-2 text-xs text-stone-500">
        {state === "active" ? `${formatElapsed(elapsed)}経過` : `${formatElapsed(elapsed)}前`}
      </td>
      <td className="px-4 py-2">
        {(state === "failed" || state === "stalled") && (
          <button
            type="button"
            onClick={handleRerun}
            disabled={pending}
            className="rounded border border-stone-300 px-2 py-1 text-xs hover:bg-stone-100 disabled:opacity-50"
          >
            {pending ? "…" : "再実行"}
          </button>
        )}
      </td>
    </tr>
  );
}

function StateBadge({ state }: { state: JobState }) {
  const color: Record<JobState, string> = {
    active: "bg-blue-100 text-blue-800",
    stalled: "bg-amber-100 text-amber-800",
    queued: "bg-stone-100 text-stone-700",
    waiting_worker: "bg-amber-100 text-amber-800",
    succeeded: "bg-green-100 text-green-800",
    failed: "bg-red-100 text-red-800",
  };
  return (
    <span className={`rounded px-2 py-0.5 text-xs whitespace-nowrap ${color[state]}`}>
      {STATE_LABELS[state]}
    </span>
  );
}

function WorkerBanner({
  alive,
  heartbeat,
  hasWaiting,
  now,
}: {
  alive: boolean;
  heartbeat: HeartbeatRow | null;
  hasWaiting: boolean;
  now: number;
}) {
  if (alive) {
    return (
      <div className="flex items-center gap-3 rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm">
        <span className="relative flex h-2.5 w-2.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-500 opacity-75" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-green-600" />
        </span>
        <span className="font-medium text-green-800">Worker稼働中</span>
        <span className="text-green-700">
          {heartbeat?.status === "processing" ? "処理中" : "待機中"}
        </span>
      </div>
    );
  }

  const lastSeen = heartbeat?.last_seen_at
    ? `最終確認 ${formatElapsed((now - new Date(heartbeat.last_seen_at).getTime()) / 1000)}前`
    : "一度も起動されていません";
  return (
    <div className="space-y-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
      <div className="flex items-center gap-3 text-sm">
        <span className="inline-flex h-2.5 w-2.5 rounded-full bg-stone-400" />
        <span className="font-medium text-amber-800">Workerが停止しています</span>
        <span className="text-xs text-amber-700">（{lastSeen}）</span>
      </div>
      <p className="text-xs leading-relaxed text-amber-700">
        {hasWaiting
          ? "処理待ちの生成がありますが、処理するWorkerが動いていません。"
          : "生成を処理するにはWorkerを起動してください。"}
        ターミナルで次を実行すると起動します:
        <code className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 font-mono">
          cd worker &amp;&amp; uv run python -m src.main
        </code>
      </p>
    </div>
  );
}
