"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import {
  approveCreativeCard,
  rejectCreativeCard,
  unapproveCreativeCard,
} from "../actions";

/**
 * 承認・却下の操作(T3b)。
 *
 * 承認は生成へ直結するため、押す前に根拠原文を読んでもらう前提の配置にする
 * (このフォームは根拠原文の下に置く)。
 */
export function ReviewForm({
  cardId,
  status,
  hasMissingEvidence,
}: {
  cardId: string;
  status: string;
  hasMissingEvidence: boolean;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function run(action: () => Promise<{ error?: string }>) {
    setError(null);
    startTransition(async () => {
      const result = await action();
      if (result.error) setError(result.error);
      else router.refresh();
    });
  }

  return (
    <div className="space-y-3">
      {hasMissingEvidence && (
        <p className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          根拠チャンクの一部が実在しません。このカードは承認できません。
          原典を取り込み直すか、カードを却下してください。
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        {status !== "approved" && (
          <button
            type="button"
            disabled={pending || hasMissingEvidence}
            onClick={() => run(() => approveCreativeCard(cardId))}
            className="rounded bg-green-700 px-4 py-2 text-sm text-white disabled:bg-stone-300"
          >
            承認する（生成に使われるようになります）
          </button>
        )}
        {status === "approved" && (
          <button
            type="button"
            disabled={pending}
            onClick={() => run(() => unapproveCreativeCard(cardId))}
            className="rounded border border-stone-400 px-4 py-2 text-sm disabled:opacity-50"
          >
            承認を取り消す（下書きへ戻す）
          </button>
        )}
        {status !== "rejected" && (
          <button
            type="button"
            disabled={pending}
            onClick={() => run(() => rejectCreativeCard(cardId))}
            className="rounded border border-red-400 px-4 py-2 text-sm text-red-700 disabled:opacity-50"
          >
            却下する
          </button>
        )}
      </div>

      {pending && <p className="text-sm text-stone-500">処理中…</p>}
      {error && (
        <p className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </p>
      )}
    </div>
  );
}
