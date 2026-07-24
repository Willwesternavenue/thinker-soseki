"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { saveReview, setVersionStatus } from "./actions";

const ROLES = [
  ["editor", "編集者"],
  ["author", "本人"],
  ["researcher", "研究者"],
  ["system_evaluator", "システム評価"],
] as const;

const VERDICTS = [
  ["approved", "承認"],
  ["approved_with_changes", "条件付き承認"],
  ["rejected", "却下"],
  ["uncertain", "保留"],
] as const;

const SCOPES = [
  ["meaning", "意味"],
  ["reasoning", "理由・導出"],
  ["boundary", "適用範囲"],
  ["historical_validity", "過去の思想として"],
  ["current_validity", "現在の思想として"],
  ["wording", "言い回し"],
] as const;

const STATUSES = ["draft", "reviewing", "approved", "rejected", "deprecated"] as const;

export function ReviewForm({
  ruleVersionId,
  currentStatus,
}: {
  ruleVersionId: string;
  currentStatus: string;
}) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [message, setMessage] = useState<string | null>(null);

  function handleReview(formData: FormData) {
    startTransition(async () => {
      const result = await saveReview(ruleVersionId, {
        reviewerRole: formData.get("reviewer_role") as string,
        verdict: formData.get("verdict") as string,
        reviewScope: formData.get("review_scope") as string,
        note: (formData.get("note") as string) ?? "",
      });
      setMessage(result.error ?? "レビューを記録しました");
      router.refresh();
    });
  }

  function handleStatus(formData: FormData) {
    startTransition(async () => {
      const result = await setVersionStatus(
        ruleVersionId,
        formData.get("status") as string
      );
      setMessage(result.error ?? "statusを変更しました");
      router.refresh();
    });
  }

  return (
    <div className="space-y-3">
      <form action={handleReview} className="flex flex-wrap items-end gap-3 text-xs">
        <label className="flex flex-col gap-1">
          <span className="text-stone-500">立場</span>
          <select name="reviewer_role" className="rounded border border-stone-300 bg-white px-2 py-1.5">
            {ROLES.map(([v, label]) => (
              <option key={v} value={v}>{label}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-stone-500">評価対象(スコープ)</span>
          <select name="review_scope" className="rounded border border-stone-300 bg-white px-2 py-1.5">
            {SCOPES.map(([v, label]) => (
              <option key={v} value={v}>{label}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-stone-500">判定</span>
          <select name="verdict" className="rounded border border-stone-300 bg-white px-2 py-1.5">
            {VERDICTS.map(([v, label]) => (
              <option key={v} value={v}>{label}</option>
            ))}
          </select>
        </label>
        <label className="flex min-w-64 flex-1 flex-col gap-1">
          <span className="text-stone-500">コメント</span>
          <input
            name="note"
            placeholder="例: 結論は良いが発火条件が広い"
            className="rounded border border-stone-300 bg-white px-2 py-1.5"
          />
        </label>
        <button
          disabled={isPending}
          className="rounded bg-blue-700 px-3 py-1.5 font-medium text-white disabled:opacity-50"
        >
          レビュー記録
        </button>
      </form>
      <form action={handleStatus} className="flex items-end gap-3 text-xs">
        <label className="flex flex-col gap-1">
          <span className="text-stone-500">バージョンstatus(現在: {currentStatus})</span>
          <select name="status" defaultValue={currentStatus} className="rounded border border-stone-300 bg-white px-2 py-1.5">
            {STATUSES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>
        <button
          disabled={isPending}
          className="rounded bg-stone-700 px-3 py-1.5 font-medium text-white disabled:opacity-50"
        >
          status変更
        </button>
        <span className="text-stone-500">
          ※ approvedの規則は、assistモード(環境変数 L3_MODE=assist)で発火時に回答へ注入される。
          shadowモード(既定)では記録のみ
        </span>
        {message && <span className="text-amber-700">{message}</span>}
      </form>
    </div>
  );
}
