import Link from "next/link";
import { createClient } from "@/lib/supabase/server";
import { STATUS_LABELS } from "./profile";

export const dynamic = "force-dynamic";

export default async function CreativeProfilesPage() {
  const supabase = createClient();

  const { data: profiles } = await supabase
    .from("creative_profiles")
    .select("profile_id, name, slug, person_id, status, orthography_policy, updated_at")
    .order("name");

  // 承認済みカード数を並べる。0枚のプロファイルは active にできないため
  // （運用中なのに生成できない状態を一覧で見つけられるようにする）
  const { data: cards } = await supabase
    .from("creative_cards")
    .select("profile_id, status");
  const approvedCount = new Map<string, number>();
  for (const c of cards ?? []) {
    if (c.status !== "approved") continue;
    const key = c.profile_id as string;
    approvedCount.set(key, (approvedCount.get(key) ?? 0) + 1);
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">創作プロファイル</h1>
          <p className="mt-1 text-sm text-stone-600">
            作家×作品群ごとの生成条件（正書法・免責文・表示題名・Guard閾値）。
            <strong className="text-stone-800">運用中のプロファイルだけが創作画面に出ます。</strong>
          </p>
        </div>
        <Link
          href="/admin/creative-profiles/new"
          className="rounded bg-stone-800 px-4 py-2 text-sm text-white"
        >
          新規作成
        </Link>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-stone-300 text-left text-stone-600">
              <th className="p-2">状態</th>
              <th className="p-2">名前</th>
              <th className="p-2">人物</th>
              <th className="p-2">正書法</th>
              <th className="p-2">承認済みカード</th>
            </tr>
          </thead>
          <tbody>
            {(profiles ?? []).map((p) => {
              const approved = approvedCount.get(p.profile_id as string) ?? 0;
              return (
                <tr key={p.profile_id} className="border-b border-stone-100 align-top">
                  <td className="p-2 whitespace-nowrap">
                    <StatusBadge status={p.status as string} />
                  </td>
                  <td className="p-2">
                    <Link
                      href={`/admin/creative-profiles/${p.profile_id}`}
                      className="text-blue-700 underline hover:text-blue-900"
                    >
                      {p.name}
                    </Link>
                    <div className="text-xs text-stone-500">{p.slug}</div>
                  </td>
                  <td className="p-2 whitespace-nowrap text-stone-600">{p.person_id}</td>
                  <td className="p-2 whitespace-nowrap text-stone-600">
                    {p.orthography_policy}
                  </td>
                  <td className="p-2 whitespace-nowrap">
                    {approved} 枚
                    {p.status === "active" && approved === 0 && (
                      <span className="ml-2 text-amber-700">← 生成できません</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {(profiles ?? []).length === 0 && (
          <p className="p-4 text-sm text-stone-500">
            プロファイルがありません。「新規作成」から追加してください。
          </p>
        )}
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const color =
    status === "active"
      ? "bg-green-100 text-green-800"
      : status === "archived"
        ? "bg-stone-200 text-stone-600"
        : "bg-amber-100 text-amber-800";
  return (
    <span className={`rounded px-2 py-0.5 text-xs ${color}`} title={STATUS_LABELS[status]}>
      {status}
    </span>
  );
}
