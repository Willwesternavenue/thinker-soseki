"use client";

import { useMemo, useState, useTransition } from "react";
import { Pagination, paginate } from "@/components/pagination";
import { setSourceEnabled } from "./actions";

const PER_PAGE = 100;

export type SourceRow = {
  source_id: string;
  title: string | null;
  source_type: string;
  priority: string;
  status: string;
  source_url?: string | null;
  // チャンクが無効化(status='disabled')されている = 検索・カード生成の対象外
  disabled?: boolean;
};

/** source_id の接頭辞(BOOK/VIDEO/DOC…)を種別キーとして取り出す。 */
function idPrefix(sourceId: string): string {
  return sourceId.split("_")[0] || "その他";
}

export function SourcesTable({ sources }: { sources: SourceRow[] }) {
  const [query, setQuery] = useState("");
  const [prefix, setPrefix] = useState<string>("ALL");
  const [page, setPage] = useState(0);

  // ID接頭辞ごとの件数(種別タブ)
  const prefixCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const s of sources) {
      const p = idPrefix(s.source_id);
      m.set(p, (m.get(p) ?? 0) + 1);
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [sources]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return sources.filter((s) => {
      if (prefix !== "ALL" && idPrefix(s.source_id) !== prefix) return false;
      if (!q) return true;
      return [s.source_id, s.title, s.source_type]
        .filter(Boolean)
        .some((v) => v!.toLowerCase().includes(q));
    });
  }, [sources, query, prefix]);

  const { pageItems, pageCount, clampedPage } = paginate(
    filtered,
    page,
    PER_PAGE
  );

  function chip(key: string, label: string, count: number) {
    const active = prefix === key;
    return (
      <button
        key={key}
        onClick={() => {
          setPrefix(key);
          setPage(0);
        }}
        className={`rounded-full px-3 py-1 text-xs ${
          active
            ? "bg-blue-700 text-white"
            : "bg-stone-100 text-stone-600 hover:bg-stone-200"
        }`}
      >
        {label} <span className="opacity-70">{count}</span>
      </button>
    );
  }

  return (
    <div className="space-y-4">
      {/* 種別タブ + 検索 */}
      <div className="flex flex-wrap items-center gap-2">
        {chip("ALL", "すべて", sources.length)}
        {prefixCounts.map(([p, c]) => chip(p, p, c))}
        <span className="flex-1" />
        <input
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setPage(0);
          }}
          placeholder="ID・タイトルで検索"
          className="rounded border border-stone-300 bg-white px-3 py-2 text-sm"
        />
      </div>

      <div className="flex items-center justify-between text-sm text-stone-500">
        <span>
          {filtered.length} 件
          {(query || prefix !== "ALL") && ` / 全${sources.length}`}
        </span>
      </div>

      <div className="overflow-x-auto rounded-lg border border-stone-200">
        <table className="w-full text-sm">
          <thead className="bg-white text-left text-stone-600">
            <tr>
              <th className="px-4 py-2">ID</th>
              <th className="px-4 py-2">タイトル</th>
              <th className="px-4 py-2">種別</th>
              <th className="px-4 py-2">優先度</th>
              <th className="px-4 py-2">状態</th>
              <th className="px-4 py-2">操作</th>
            </tr>
          </thead>
          <tbody>
            {pageItems.map((s) => (
              <tr
                key={s.source_id}
                className={`border-t border-stone-200 ${
                  s.disabled ? "bg-stone-50 text-stone-400" : ""
                }`}
              >
                <td className="px-4 py-2 font-mono text-xs">{s.source_id}</td>
                <td className="px-4 py-2">
                  {s.source_url ? (
                    <a
                      href={s.source_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-blue-700 hover:underline"
                    >
                      {s.title}
                    </a>
                  ) : (
                    s.title
                  )}
                </td>
                <td className="px-4 py-2">{s.source_type}</td>
                <td className="px-4 py-2">{s.priority}</td>
                <td className="px-4 py-2">
                  {s.disabled ? (
                    <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">
                      無効
                    </span>
                  ) : (
                    <StatusBadge status={s.status} />
                  )}
                </td>
                <td className="px-4 py-2">
                  <ToggleButton source={s} />
                </td>
              </tr>
            ))}
            {!pageItems.length && (
              <tr>
                <td colSpan={6} className="px-4 py-6 text-center text-stone-500">
                  該当なし
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <Pagination page={clampedPage} pageCount={pageCount} onPage={setPage} />
    </div>
  );
}

/**
 * 原典の無効化/再有効化。無効化するとチャンクが検索・カード生成の対象から外れる
 * (原典の行とファイルは残るので再有効化で戻せる)。
 */
function ToggleButton({ source }: { source: SourceRow }) {
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const disabled = !!source.disabled;

  function onClick() {
    const message = disabled
      ? `「${source.title ?? source.source_id}」を再有効化しますか?\n検索とカード生成の対象に戻ります。`
      : `「${source.title ?? source.source_id}」を無効化しますか?\n検索とカード生成の対象から外れます(原典は残るので元に戻せます)。`;
    if (!confirm(message)) return;
    setError(null);
    startTransition(async () => {
      const result = await setSourceEnabled(source.source_id, disabled);
      if (result?.error) setError(result.error);
    });
  }

  return (
    <div>
      <button
        onClick={onClick}
        disabled={pending}
        className={`rounded border px-2 py-0.5 text-xs disabled:opacity-50 ${
          disabled
            ? "border-stone-300 text-stone-600 hover:bg-stone-100"
            : "border-red-300 text-red-700 hover:bg-red-50"
        }`}
      >
        {pending ? "処理中…" : disabled ? "再有効化" : "無効化"}
      </button>
      {error && <p className="mt-1 text-xs text-red-700">{error}</p>}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const color =
    status === "distilled"
      ? "bg-green-100 text-green-800"
      : status === "raw"
        ? "bg-stone-200 text-stone-600"
        : "bg-blue-100 text-blue-800";
  return (
    <span className={`rounded px-2 py-0.5 text-xs ${color}`}>{status}</span>
  );
}
