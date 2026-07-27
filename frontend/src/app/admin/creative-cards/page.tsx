import Link from "next/link";
import { createClient } from "@/lib/supabase/server";
import { StatusBadge } from "../cards/status-badge";
import { evidenceTypeLabel } from "./approval";

export const dynamic = "force-dynamic";

const STATUSES = ["draft", "reviewing", "approved", "rejected", "deprecated"] as const;

export default async function CreativeCardsPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; type?: string }>;
}) {
  const { status, type } = await searchParams;
  const supabase = await createClient();

  let query = supabase
    .from("creative_cards")
    .select(
      "card_id, profile_id, card_type, title, summary, evidence_type, evidence_chunk_ids, status, updated_at"
    )
    .order("card_type")
    .order("title");
  if (status) query = query.eq("status", status);
  if (type) query = query.eq("card_type", type);
  const { data: cards } = await query;

  const { data: profiles } = await supabase
    .from("creative_profiles")
    .select("profile_id, name, status");
  const profileName = new Map(
    (profiles ?? []).map((p) => [p.profile_id, p.name as string])
  );

  const approved = (cards ?? []).filter((c) => c.status === "approved").length;
  const types = [...new Set((cards ?? []).map((c) => c.card_type as string))].sort();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold">創作カード</h1>
        <p className="mt-1 text-sm text-stone-600">
          新作を書くときに参照する創作上の操作。
          <strong className="text-stone-800">承認済みのカードだけが生成に使われます。</strong>
          思想カード（本人の思想）とは別のデータです。
        </p>
      </div>

      <div className="rounded border border-stone-200 bg-stone-50 p-3 text-sm">
        全 {cards?.length ?? 0} 件 / 承認済み <strong>{approved}</strong> 件
        {approved === 0 && (
          <span className="ml-2 text-amber-700">
            ← 承認が0件のため、まだ生成できません（承認済みカード必須の不変条件）
          </span>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-stone-500">状態:</span>
        <FilterLink href="/admin/creative-cards" active={!status && !type} label="全部" />
        {STATUSES.map((s) => (
          <FilterLink
            key={s}
            href={`/admin/creative-cards?status=${s}`}
            active={status === s}
            label={s}
          />
        ))}
      </div>
      {types.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-stone-500">種別:</span>
          {types.map((t) => (
            <FilterLink
              key={t}
              href={`/admin/creative-cards?type=${t}`}
              active={type === t}
              label={t}
            />
          ))}
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-stone-300 text-left text-stone-600">
              <th className="p-2">状態</th>
              <th className="p-2">種別</th>
              <th className="p-2">主張</th>
              <th className="p-2">根拠の種類</th>
              <th className="p-2">根拠</th>
              <th className="p-2">プロファイル</th>
            </tr>
          </thead>
          <tbody>
            {(cards ?? []).map((card) => (
              <tr key={card.card_id} className="border-b border-stone-100 align-top">
                <td className="p-2">
                  <StatusBadge status={card.status as string} />
                </td>
                <td className="p-2 whitespace-nowrap text-stone-600">{card.card_type}</td>
                <td className="p-2">
                  <Link
                    href={`/admin/creative-cards/${card.card_id}`}
                    className="text-blue-700 underline hover:text-blue-900"
                  >
                    {card.title}
                  </Link>
                  {card.summary && (
                    <div className="mt-1 text-xs text-stone-500">{card.summary}</div>
                  )}
                </td>
                <td className="p-2 whitespace-nowrap text-xs text-stone-600">
                  {evidenceTypeLabel(card.evidence_type as string | null)}
                </td>
                <td className="p-2 whitespace-nowrap text-stone-600">
                  {(card.evidence_chunk_ids as string[] | null)?.length ?? 0} 件
                </td>
                <td className="p-2 whitespace-nowrap text-stone-600">
                  {profileName.get(card.profile_id as string) ?? card.profile_id}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {(cards ?? []).length === 0 && (
          <p className="p-4 text-sm text-stone-500">
            該当するカードがありません。
            候補の生成は worker の
            <code className="mx-1 rounded bg-stone-100 px-1">
              uv run python -m src.aozora.cli gen-cards
            </code>
            で行います。
          </p>
        )}
      </div>
    </div>
  );
}

function FilterLink({
  href,
  active,
  label,
}: {
  href: string;
  active: boolean;
  label: string;
}) {
  return (
    <Link
      href={href}
      className={`rounded px-2 py-1 ${active ? "bg-stone-300" : "hover:bg-stone-100"}`}
    >
      {label}
    </Link>
  );
}
