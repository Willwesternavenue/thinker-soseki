"use client";

import { useState, useTransition } from "react";
import { createDraft } from "../actions";

const PRIORITIES = [
  ["core", "core(中核)"],
  ["important", "important(重要)"],
  ["support", "support(補助)"],
  ["style", "style(語り口)"],
  ["archive", "archive(保管)"],
] as const;

export function NewForm() {
  const [isPending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const formData = new FormData(e.currentTarget);
    startTransition(async () => {
      const result = await createDraft(formData);
      if (result?.error) setError(result.error);
    });
  }

  const inputCls =
    "w-full rounded border border-stone-300 bg-white px-3 py-2 text-sm";

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label className="mb-1 block text-sm text-stone-600">
          動画タイトル(必須)
        </label>
        <input name="title" required className={inputCls} />
      </div>
      <div>
        <label className="mb-1 block text-sm text-stone-600">動画URL</label>
        <input
          name="video_url"
          placeholder="https://www.youtube.com/watch?v=…"
          className={inputCls}
        />
      </div>
      <div>
        <label className="mb-1 block text-sm text-stone-600">
          補足ヒント(任意。例:「今回は菌と腸内細菌がテーマ」)
        </label>
        <input name="hint" className={inputCls} />
      </div>
      <div>
        <label className="mb-1 block text-sm text-stone-600">重要度</label>
        <select name="priority" defaultValue="core" className={inputCls}>
          {PRIORITIES.map(([v, label]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="mb-1 block text-sm text-stone-600">
          生スクリプト貼り付け(必須)
        </label>
        <textarea
          name="raw_text"
          required
          rows={16}
          placeholder="YouTubeの文字起こしを全文コピペ(タイムスタンプ付きのままでOK)"
          className={`${inputCls} font-mono`}
        />
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <button
        type="submit"
        disabled={isPending}
        className="rounded bg-blue-700 px-4 py-2 text-sm text-white hover:bg-blue-800 disabled:opacity-50"
      >
        {isPending ? "作成中…" : "下書きを作成して整形へ"}
      </button>
    </form>
  );
}
