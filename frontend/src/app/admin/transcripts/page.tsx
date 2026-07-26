import Link from "next/link";
import { createClient } from "@/lib/supabase/server";

const STATUS_LABEL: Record<string, [string, string]> = {
  processing: ["整形中", "bg-amber-100 text-amber-800"],
  review: ["レビュー待ち", "bg-blue-100 text-blue-800"],
  ingested: ["取り込み済み", "bg-green-100 text-green-800"],
};

export default async function TranscriptsPage() {
  const supabase = await createClient();
  const { data: drafts } = await supabase
    .from("transcript_drafts")
    .select("draft_id, title, status, source_id, turns, created_at")
    .order("created_at", { ascending: false });

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <h1 className="text-xl font-bold">スクリプト整形</h1>
        <Link
          href="/admin/transcripts/new"
          className="rounded bg-blue-700 px-4 py-2 text-sm text-white hover:bg-blue-800"
        >
          新規整形
        </Link>
      </div>
      <p className="mb-6 text-sm text-stone-500">
        YouTube生書き起こしを貼り付け → 話者切り分け・誤変換修正 → 原典として取り込む。
      </p>
      <div className="overflow-hidden rounded border border-stone-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-stone-50 text-left text-stone-500">
            <tr>
              <th className="px-4 py-2">タイトル</th>
              <th className="px-4 py-2">状態</th>
              <th className="px-4 py-2">ターン数</th>
              <th className="px-4 py-2">原典</th>
            </tr>
          </thead>
          <tbody>
            {(drafts ?? []).map((d) => {
              const [label, cls] =
                STATUS_LABEL[d.status] ?? [d.status, "bg-stone-100"];
              return (
                <tr key={d.draft_id} className="border-t border-stone-100">
                  <td className="px-4 py-2">
                    <Link
                      href={`/admin/transcripts/${d.draft_id}`}
                      className="text-blue-700 hover:underline"
                    >
                      {d.title}
                    </Link>
                  </td>
                  <td className="px-4 py-2">
                    <span className={`rounded px-2 py-0.5 text-xs ${cls}`}>
                      {label}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-stone-500">
                    {Array.isArray(d.turns) ? d.turns.length : 0}
                  </td>
                  <td className="px-4 py-2 text-stone-500">
                    {d.source_id ?? "—"}
                  </td>
                </tr>
              );
            })}
            {(drafts ?? []).length === 0 && (
              <tr>
                <td
                  colSpan={4}
                  className="px-4 py-8 text-center text-stone-400"
                >
                  下書きはまだありません
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
