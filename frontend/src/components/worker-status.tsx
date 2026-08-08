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
