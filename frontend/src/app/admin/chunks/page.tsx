import { createClient } from "@/lib/supabase/server";
import { ImportanceSelect } from "./importance-select";

export const dynamic = "force-dynamic";

export default async function ChunksPage({
  searchParams,
}: {
  searchParams: Promise<{ source?: string }>;
}) {
  const { source } = await searchParams;
  const supabase = await createClient();

  const { data: sources } = await supabase
    .from("sources")
    .select("source_id, title")
    .order("source_id");

  let query = supabase
    .from("source_chunks")
    .select(
      "chunk_id, source_id, chapter_title, section_title, source_page, char_start, char_end, chunk_type, verbatim, text, chunk_distillations(distillation_id, summary, importance, candidate_thought_ids, heavy_json)"
    )
    .eq("status", "active")
    .order("chunk_id")
    .limit(200);
  if (source) query = query.eq("source_id", source);
  const { data: chunks } = await query;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">チャンク一覧</h1>
        <form className="flex items-center gap-2 text-sm">
          <select
            name="source"
            defaultValue={source ?? ""}
            className="rounded border border-stone-300 bg-white px-2 py-1"
          >
            <option value="">すべての原典</option>
            {(sources ?? []).map((s) => (
              <option key={s.source_id} value={s.source_id}>
                {s.source_id}: {s.title}
              </option>
            ))}
          </select>
          <button className="rounded border border-stone-300 px-3 py-1 hover:bg-stone-100">
            絞り込み
          </button>
        </form>
      </div>

      <div className="space-y-3">
        {(chunks ?? []).map((chunk) => {
          const dist = Array.isArray(chunk.chunk_distillations)
            ? chunk.chunk_distillations[0]
            : chunk.chunk_distillations;
          return (
            <details
              key={chunk.chunk_id}
              className="rounded-lg border border-stone-200 bg-white"
            >
              <summary className="flex cursor-pointer items-center gap-3 px-4 py-2 text-sm">
                <span className="font-mono text-xs text-stone-600">
                  {chunk.chunk_id}
                </span>
                <span className="flex-1 truncate text-stone-700">
                  {dist?.summary ?? chunk.text.slice(0, 60)}
                </span>
                {chunk.verbatim && (
                  <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-xs text-emerald-800">
                    verbatim
                  </span>
                )}
                {dist && (
                  <ImportanceSelect
                    distillationId={dist.distillation_id}
                    value={dist.importance}
                  />
                )}
              </summary>
              <div className="space-y-3 border-t border-stone-200 px-4 py-3 text-sm">
                <p className="text-xs text-stone-500">
                  {chunk.chapter_title ?? "-"} / {chunk.section_title ?? "-"} /
                  ページ: {chunk.source_page ?? "-"} / 文字位置: {chunk.char_start}–
                  {chunk.char_end} / 種別: {chunk.chunk_type}
                </p>
                {dist && (
                  <p className="text-xs text-stone-600">
                    思想候補: {dist.candidate_thought_ids?.join(", ") || "なし"}
                    {dist.heavy_json && (
                      <span className="ml-2 rounded bg-purple-100 px-1.5 py-0.5 text-purple-800">
                        重蒸留済み
                      </span>
                    )}
                  </p>
                )}
                <pre className="max-h-64 overflow-y-auto whitespace-pre-wrap rounded bg-stone-100 p-3 text-xs leading-relaxed text-stone-700">
                  {chunk.text}
                </pre>
              </div>
            </details>
          );
        })}
        {!chunks?.length && (
          <p className="py-8 text-center text-stone-500">チャンクがありません</p>
        )}
      </div>
    </div>
  );
}
