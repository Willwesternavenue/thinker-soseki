"use client";

import { useMemo, useState, useTransition } from "react";
import type { GlossaryRow } from "@/lib/transcripts/glossary";
import {
  addGlossaryTerm,
  deleteGlossaryTerm,
  updateGlossaryTerm,
} from "./actions";

const PER_PAGE = 100;

/** セル直接編集。blur/Enterで保存、Escで取消。 */
function EditableCell({
  id,
  field,
  value,
  placeholder,
  mono,
}: {
  id: string;
  field: "content" | "reading" | "note";
  value: string | null;
  placeholder?: string;
  mono?: boolean;
}) {
  const [val, setVal] = useState(value ?? "");
  const [, startTransition] = useTransition();
  const dirty = val !== (value ?? "");

  function commit() {
    if (!dirty) return;
    startTransition(() => {
      updateGlossaryTerm(id, field, val);
    });
  }

  return (
    <input
      value={val}
      onChange={(e) => setVal(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") setVal(value ?? "");
      }}
      placeholder={placeholder}
      className={`w-full rounded border px-2 py-1 text-sm ${
        dirty ? "border-blue-400 bg-blue-50" : "border-transparent bg-transparent"
      } hover:border-stone-300 focus:border-blue-400 focus:bg-white ${
        mono ? "font-medium" : ""
      }`}
    />
  );
}

export function GlossaryClient({
  terms,
  rules,
}: {
  terms: GlossaryRow[];
  rules: GlossaryRow[];
}) {
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [kind, setKind] = useState<"term" | "rule">("term");
  const [isPending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return terms;
    return terms.filter((t) =>
      [t.content, t.reading, t.note]
        .filter(Boolean)
        .some((s) => s!.toLowerCase().includes(q))
    );
  }, [terms, query]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PER_PAGE));
  const clampedPage = Math.min(page, pageCount - 1);
  const pageRows = filtered.slice(
    clampedPage * PER_PAGE,
    clampedPage * PER_PAGE + PER_PAGE
  );

  function handleAdd(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const formData = new FormData(form);
    startTransition(async () => {
      const result = await addGlossaryTerm(formData);
      if (result.error) setError(result.error);
      else {
        setError(null);
        form.reset();
      }
    });
  }

  function handleDelete(id: string) {
    startTransition(() => {
      deleteGlossaryTerm(id);
    });
  }

  const inputCls =
    "rounded border border-stone-300 bg-white px-3 py-2 text-sm";

  return (
    <div className="space-y-6">
      {/* 追加フォーム */}
      <form
        onSubmit={handleAdd}
        className="flex flex-wrap items-start gap-2 rounded border border-stone-200 bg-white p-4"
      >
        <select
          name="kind"
          value={kind}
          onChange={(e) => setKind(e.target.value as "term" | "rule")}
          className={inputCls}
        >
          <option value="term">用語</option>
          <option value="rule">使い分けルール</option>
        </select>
        <input
          name="content"
          required
          placeholder={kind === "term" ? "表記(例: 南方熊楠)" : "ルール文"}
          className={`${inputCls} min-w-[16rem] flex-1`}
        />
        {kind === "term" && (
          <>
            <input name="reading" placeholder="読み(みなかたくまぐす)" className={`${inputCls} w-52`} />
            <input name="note" placeholder="備考(AIコメント)" className={`${inputCls} w-64`} />
          </>
        )}
        <button
          type="submit"
          disabled={isPending}
          className="rounded bg-blue-700 px-4 py-2 text-sm text-white hover:bg-blue-800 disabled:opacity-50"
        >
          追加
        </button>
        {error && <p className="w-full text-sm text-red-600">{error}</p>}
      </form>

      {/* 使い分けルール */}
      {rules.length > 0 && (
        <section>
          <h2 className="mb-2 text-sm font-bold text-stone-700">
            使い分けルール({rules.length})
          </h2>
          <ul className="space-y-2">
            {rules.map((r) => (
              <li
                key={r.id}
                className="flex items-start gap-2 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm"
              >
                <span className="flex-1">{r.content}</span>
                <button
                  onClick={() => handleDelete(r.id)}
                  disabled={isPending}
                  className="text-xs text-stone-400 hover:text-red-600"
                >
                  削除
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* 検索 + 件数 */}
      <div className="flex items-center justify-between gap-4">
        <input
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setPage(0);
          }}
          placeholder="表記・読み・備考で検索"
          className={`${inputCls} w-72`}
        />
        <span className="text-sm text-stone-500">
          {filtered.length} 語
          {query && ` / 全${terms.length}`}
        </span>
      </div>

      {/* 対訳テーブル */}
      <div className="overflow-hidden rounded border border-stone-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-stone-50 text-left text-stone-500">
            <tr>
              <th className="w-1/4 px-3 py-2">表記</th>
              <th className="w-1/4 px-3 py-2">読み</th>
              <th className="px-3 py-2">備考(AIコメント)</th>
              <th className="w-12 px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {pageRows.map((t) => (
              <tr key={t.id} className="border-t border-stone-100">
                <td className="px-2 py-1">
                  <EditableCell id={t.id} field="content" value={t.content} mono />
                </td>
                <td className="px-2 py-1">
                  <EditableCell
                    id={t.id}
                    field="reading"
                    value={t.reading}
                    placeholder="—"
                  />
                </td>
                <td className="px-2 py-1">
                  <EditableCell
                    id={t.id}
                    field="note"
                    value={t.note}
                    placeholder="—"
                  />
                </td>
                <td className="px-2 py-1 text-center">
                  <button
                    onClick={() => handleDelete(t.id)}
                    disabled={isPending}
                    className="text-stone-300 hover:text-red-600"
                    title="削除"
                  >
                    ✕
                  </button>
                </td>
              </tr>
            ))}
            {pageRows.length === 0 && (
              <tr>
                <td colSpan={4} className="px-3 py-8 text-center text-stone-400">
                  該当なし
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* ページネーション */}
      {pageCount > 1 && (
        <div className="flex items-center justify-center gap-2 text-sm">
          <button
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={clampedPage === 0}
            className="rounded border border-stone-300 px-3 py-1 hover:bg-stone-50 disabled:opacity-40"
          >
            前へ
          </button>
          <span className="text-stone-500">
            {clampedPage + 1} / {pageCount}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
            disabled={clampedPage >= pageCount - 1}
            className="rounded border border-stone-300 px-3 py-1 hover:bg-stone-50 disabled:opacity-40"
          >
            次へ
          </button>
        </div>
      )}
    </div>
  );
}
