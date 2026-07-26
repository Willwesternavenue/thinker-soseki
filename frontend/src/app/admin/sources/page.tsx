import { createClient } from "@/lib/supabase/server";
import { UploadForm } from "./upload-form";
import { SourcesTable, type SourceRow } from "./sources-table";

export const dynamic = "force-dynamic";

export default async function SourcesPage() {
  const supabase = await createClient();
  const { data: sources } = await supabase
    .from("sources")
    .select("source_id, title, source_type, priority, status, source_url, created_at")
    .order("source_id", { ascending: true });

  // 無効化済みの原典(= disabled チャンクを持つもの)。通常ごく少数なので
  // 全チャンクを舐めず disabled 行だけを引く。
  const { data: disabledChunks } = await supabase
    .from("source_chunks")
    .select("source_id")
    .eq("status", "disabled");
  const disabledIds = new Set((disabledChunks ?? []).map((c) => c.source_id));

  const rows = ((sources ?? []) as SourceRow[]).map((s) => ({
    ...s,
    disabled: disabledIds.has(s.source_id),
  }));

  return (
    <div className="space-y-8">
      <h1 className="text-xl font-bold">原典管理</h1>
      <UploadForm />
      <SourcesTable sources={rows} />
    </div>
  );
}
