"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import {
  addQuestion,
  deleteQuestion,
  saveQuestion,
  testSearch,
  type TestHit,
} from "./actions";
import { StatusBadge } from "../cards/status-badge";

const INTENTS = [
  "definition",
  "misunderstanding",
  "comparison",
  "daily_advice",
  "application",
  "critical_question",
  "example_request",
  "relationship_question",
];

type Question = {
  question_id: string;
  question: string;
  target_thought_id: string;
  intent: string;
  answer_direction: string | null;
  status: string;
};

export function QuestionManager({
  questions,
  thoughtIds,
}: {
  questions: Question[];
  thoughtIds: string[];
}) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [message, setMessage] = useState<string | null>(null);
  const [testQuery, setTestQuery] = useState("");
  const [testHits, setTestHits] = useState<TestHit[] | null>(null);

  function run(fn: () => Promise<{ error?: string }>, ok: string) {
    startTransition(async () => {
      setMessage(null);
      const result = await fn();
      setMessage(result.error ?? ok);
      router.refresh();
    });
  }

  return (
    <div className="space-y-8">
      {/* テスト検索 */}
      <section className="space-y-3 rounded-lg border border-stone-200 bg-white p-4">
        <h2 className="font-semibold">テスト検索(ルーティング確認)</h2>
        <div className="flex gap-2">
          <input
            value={testQuery}
            onChange={(e) => setTestQuery(e.target.value)}
            placeholder="例: 絶対負はネガティブ思考ですか?"
            className="flex-1 rounded border border-stone-300 bg-white px-3 py-1.5 text-sm"
          />
          <button
            disabled={isPending || !testQuery}
            onClick={() =>
              startTransition(async () => {
                const result = await testSearch(testQuery);
                if (result.error) setMessage(result.error);
                setTestHits(result.hits ?? []);
              })
            }
            className="rounded bg-blue-700 px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            検索
          </button>
        </div>
        {testHits && (
          <table className="w-full text-xs">
            <thead className="text-left text-stone-500">
              <tr>
                <th className="py-1">類似度</th>
                <th>thought_id</th>
                <th>質問</th>
              </tr>
            </thead>
            <tbody>
              {testHits.map((hit) => (
                <tr key={hit.question_id} className="border-t border-stone-200">
                  <td className="py-1">{hit.similarity.toFixed(3)}</td>
                  <td className="font-mono">{hit.target_thought_id}</td>
                  <td>{hit.question}</td>
                </tr>
              ))}
              {!testHits.length && (
                <tr>
                  <td colSpan={3} className="py-2 text-stone-500">
                    ヒットなし(フォールバック経路になります)
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </section>

      {/* 追加フォーム */}
      <section className="space-y-3 rounded-lg border border-stone-200 bg-white p-4">
        <h2 className="font-semibold">質問を追加</h2>
        <form
          action={(fd) =>
            run(
              () =>
                addQuestion({
                  question: fd.get("question") as string,
                  target_thought_id: fd.get("target_thought_id") as string,
                  intent: fd.get("intent") as string,
                  answer_direction: fd.get("answer_direction") as string,
                }),
              "追加しました"
            )
          }
          className="grid grid-cols-2 gap-3 text-sm"
        >
          <input
            name="question"
            required
            placeholder="質問文"
            className="col-span-2 rounded border border-stone-300 bg-white px-2 py-1.5"
          />
          <select
            name="target_thought_id"
            required
            className="rounded border border-stone-300 bg-white px-2 py-1.5"
          >
            {thoughtIds.map((tid) => (
              <option key={tid} value={tid}>
                {tid}
              </option>
            ))}
          </select>
          <select
            name="intent"
            className="rounded border border-stone-300 bg-white px-2 py-1.5"
          >
            {INTENTS.map((i) => (
              <option key={i} value={i}>
                {i}
              </option>
            ))}
          </select>
          <input
            name="answer_direction"
            placeholder="回答方向(任意)"
            className="col-span-2 rounded border border-stone-300 bg-white px-2 py-1.5"
          />
          <button
            disabled={isPending}
            className="col-span-2 w-fit rounded bg-blue-700 px-4 py-1.5 font-medium text-white disabled:opacity-50"
          >
            追加
          </button>
        </form>
      </section>

      {message && <p className="text-sm text-amber-700">{message}</p>}

      {/* 一覧・編集 */}
      <section className="space-y-2">
        <h2 className="font-semibold">一覧({questions.length}件)</h2>
        {questions.map((q) => (
          <details
            key={q.question_id}
            className="rounded-lg border border-stone-200 bg-white"
          >
            <summary className="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm">
              <StatusBadge status={q.status} />
              <span className="rounded bg-stone-200 px-1.5 py-0.5 text-xs">
                {q.intent}
              </span>
              <span className="flex-1 truncate">{q.question}</span>
              <span className="font-mono text-xs text-stone-500">
                {q.target_thought_id}
              </span>
            </summary>
            <form
              action={(fd) =>
                run(
                  () =>
                    saveQuestion(q.question_id, {
                      question: fd.get("question") as string,
                      target_thought_id: fd.get("target_thought_id") as string,
                      intent: fd.get("intent") as string,
                      answer_direction: fd.get("answer_direction") as string,
                      status: fd.get("status") as string,
                    }),
                  "保存しました"
                )
              }
              className="grid grid-cols-2 gap-3 border-t border-stone-200 px-3 py-3 text-sm"
            >
              <input
                name="question"
                defaultValue={q.question}
                className="col-span-2 rounded border border-stone-300 bg-white px-2 py-1.5"
              />
              <select
                name="target_thought_id"
                defaultValue={q.target_thought_id}
                className="rounded border border-stone-300 bg-white px-2 py-1.5"
              >
                {thoughtIds.map((tid) => (
                  <option key={tid} value={tid}>
                    {tid}
                  </option>
                ))}
              </select>
              <select
                name="intent"
                defaultValue={q.intent}
                className="rounded border border-stone-300 bg-white px-2 py-1.5"
              >
                {INTENTS.map((i) => (
                  <option key={i} value={i}>
                    {i}
                  </option>
                ))}
              </select>
              <input
                name="answer_direction"
                defaultValue={q.answer_direction ?? ""}
                placeholder="回答方向"
                className="col-span-2 rounded border border-stone-300 bg-white px-2 py-1.5"
              />
              <div className="col-span-2 flex items-center gap-3">
                <select
                  name="status"
                  defaultValue={q.status}
                  className="rounded border border-stone-300 bg-white px-2 py-1.5"
                >
                  <option value="active">active</option>
                  <option value="draft">draft</option>
                  <option value="inactive">inactive</option>
                </select>
                <button
                  disabled={isPending}
                  className="rounded bg-blue-700 px-4 py-1.5 font-medium text-white disabled:opacity-50"
                >
                  保存
                </button>
                <button
                  type="button"
                  disabled={isPending}
                  onClick={() =>
                    run(() => deleteQuestion(q.question_id), "削除しました")
                  }
                  className="rounded border border-red-300 px-3 py-1.5 text-red-700 hover:bg-stone-100 disabled:opacity-50"
                >
                  削除
                </button>
              </div>
            </form>
          </details>
        ))}
      </section>
    </div>
  );
}
