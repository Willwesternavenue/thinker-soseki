"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  getLatestDistillationJob,
  startDistillation,
  type DistillationJob as Job,
} from "./distill-actions";

const STEP_LABEL: Record<string, string> = {
  heavy_distill: "重蒸留(重要チャンクの深い分析)",
  source_distill: "原典単位の蒸留",
  card_generation: "思想カード候補の生成",
  question_generation: "質問対応情報の生成",
  done: "完了",
};

export function DistillButton() {
  const router = useRouter();
  const [job, setJob] = useState<Job | null>(null);
  const [starting, setStarting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const { job } = await getLatestDistillationJob();
    setJob(job ?? null);
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [refresh]);

  const active = job?.status === "pending" || job?.status === "running";

  // ジョブ完了を検知したらカード一覧を再取得
  useEffect(() => {
    if (job?.status === "succeeded") router.refresh();
  }, [job?.status, router]);

  async function handleStart() {
    if (
      !confirm(
        "原典から思想カード候補を生成します。重要チャンクの重蒸留(Sonnet)が走るため、量に応じて数分〜十数分かかり、相応のAPIコストが発生します。実行しますか?"
      )
    )
      return;
    setStarting(true);
    setMessage(null);
    const result = await startDistillation();
    setStarting(false);
    if (result.error) setMessage(result.error);
    else {
      setMessage("蒸留ジョブを開始しました。進捗はこの下に表示されます。");
      refresh();
    }
  }

  return (
    <div className="rounded-lg border border-stone-200 bg-white p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold">原典から思想カードを生成</h2>
          <p className="mt-0.5 text-xs text-stone-500">
            投入済みの原典を重蒸留し、思想カード候補と質問対応情報を作ります(draftで生成 →
            下でレビュー・承認)。ターミナル不要。
          </p>
        </div>
        <button
          onClick={handleStart}
          disabled={starting || active}
          className="shrink-0 rounded bg-blue-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {active ? "実行中…" : starting ? "開始中…" : "カードを生成"}
        </button>
      </div>

      {message && <p className="mt-2 text-sm text-amber-700">{message}</p>}

      {job && active && (
        <div className="mt-3 flex items-center gap-2 rounded border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-800">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-blue-500 opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-blue-600" />
          </span>
          {job.status === "pending"
            ? "Worker待ち(処理エンジンが起動しているか「ジョブ」タブで確認)"
            : (STEP_LABEL[job.current_step ?? ""] ?? job.current_step)}
        </div>
      )}
      {job && job.status === "succeeded" && job.result && (
        <p className="mt-3 rounded border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800">
          完了: 重蒸留{job.result.heavy ?? 0}件 / カード{job.result.cards ?? 0}枚 /
          質問{job.result.questions ?? 0}問を生成しました。下の一覧(draft)を承認してください。
        </p>
      )}
      {job && job.status === "failed" && (
        <p className="mt-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          失敗: {job.error_message}
        </p>
      )}
    </div>
  );
}
