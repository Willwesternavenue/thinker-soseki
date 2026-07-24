"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

type EvalQuestion = {
  id: string;
  question: string;
  expected_thought_id: string | null;
};

type RunResult = {
  id: string;
  question: string;
  routing_method?: string;
  selected?: string[];
  expected: string | null;
  routingOk: boolean | null;
  guardPassed?: boolean;
  fallback?: boolean;
  error?: string;
};

/** 評価セット実行(仕様14章)。1問ずつAPIを叩き、ルーティング精度を即時表示する。 */
export function EvalRunner({ questions }: { questions: EvalQuestion[] }) {
  const router = useRouter();
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<RunResult[]>([]);
  const [progress, setProgress] = useState(0);

  async function run() {
    setRunning(true);
    setResults([]);
    setProgress(0);
    for (const [index, q] of questions.entries()) {
      try {
        const response = await fetch("/api/admin/eval", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: q.question, questionId: q.id }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error);
        const selected: string[] = data.trace?.selected_thought_ids ?? [];
        setResults((prev) => [
          ...prev,
          {
            id: q.id,
            question: q.question,
            routing_method: data.trace?.routing_method,
            selected,
            expected: q.expected_thought_id,
            routingOk: q.expected_thought_id
              ? selected.includes(q.expected_thought_id)
              : null,
            guardPassed: data.trace?.guard_result?.passed,
            fallback: data.trace?.fallback_card_used,
          },
        ]);
      } catch (e) {
        setResults((prev) => [
          ...prev,
          {
            id: q.id,
            question: q.question,
            expected: q.expected_thought_id,
            routingOk: null,
            error: (e as Error).message,
          },
        ]);
      }
      setProgress(index + 1);
    }
    setRunning(false);
    router.refresh();
  }

  const routingChecked = results.filter((r) => r.routingOk !== null);
  const routingCorrect = routingChecked.filter((r) => r.routingOk).length;
  const guardFailed = results.filter((r) => r.guardPassed === false).length;

  return (
    <section className="space-y-3 rounded-lg border border-stone-200 bg-white p-4">
      <div className="flex items-center gap-4">
        <h2 className="font-semibold">評価セット実行({questions.length}問)</h2>
        <button
          onClick={run}
          disabled={running}
          className="rounded bg-blue-700 px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {running ? `実行中... ${progress}/${questions.length}` : "実行"}
        </button>
        {results.length > 0 && (
          <span className="text-sm text-stone-600">
            ルーティング精度: {routingCorrect}/{routingChecked.length} / Guard失敗:{" "}
            {guardFailed}件
          </span>
        )}
      </div>
      {results.length > 0 && (
        <table className="w-full text-xs">
          <thead className="text-left text-stone-500">
            <tr>
              <th className="py-1">質問</th>
              <th>routing</th>
              <th>selected</th>
              <th>判定</th>
            </tr>
          </thead>
          <tbody>
            {results.map((r) => (
              <tr key={r.id} className="border-t border-stone-200 align-top">
                <td className="max-w-xs truncate py-1">{r.question}</td>
                <td>
                  {r.routing_method}
                  {r.fallback && " (fallback)"}
                </td>
                <td className="font-mono">{r.selected?.join(", ")}</td>
                <td>
                  {r.error ? (
                    <span className="text-red-700">{r.error}</span>
                  ) : r.routingOk === null ? (
                    "-"
                  ) : r.routingOk ? (
                    <span className="text-green-700">OK</span>
                  ) : (
                    <span className="text-red-700">
                      NG(期待: {r.expected})
                    </span>
                  )}
                  {r.guardPassed === false && (
                    <span className="ml-1 text-amber-700">Guard失敗</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
