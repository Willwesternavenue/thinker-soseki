import Link from "next/link";
import { createClient } from "@/lib/supabase/server";
import { StatusBadge } from "./status-badge";
import { DistillButton } from "./distill-button";

export const dynamic = "force-dynamic";

const STATUSES = ["draft", "reviewing", "approved", "rejected", "deprecated"] as const;

export default async function CardsPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  const { status } = await searchParams;
  const supabase = await createClient();

  let query = supabase
    .from("thought_cards")
    .select("card_id, thought_id, title, importance, status, version, core_claim, updated_at")
    .order("updated_at", { ascending: false });
  if (status) query = query.eq("status", status);
  const { data: cards } = await query;

  return (
    <div className="space-y-6">
      <DistillButton />
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">思想カード</h1>
        <div className="flex gap-2 text-sm">
          <Link
            href="/admin/cards"
            className={`rounded px-2 py-1 ${!status ? "bg-stone-300" : "hover:bg-stone-100"}`}
          >
            全部
          </Link>
          {STATUSES.map((s) => (
            <Link
              key={s}
              href={`/admin/cards?status=${s}`}
              className={`rounded px-2 py-1 ${status === s ? "bg-stone-300" : "hover:bg-stone-100"}`}
            >
              {s}
            </Link>
          ))}
        </div>
      </div>
      <p className="text-xs text-stone-500">
        レビュー: draftカードを開き、内容を確認・編集して approved にすると本番回答に使われます。
      </p>
      <div className="space-y-2">
        {(cards ?? []).map((card) => (
          <Link
            key={card.card_id}
            href={`/admin/cards/${card.card_id}`}
            className="block rounded-lg border border-stone-200 bg-white px-4 py-3 hover:border-stone-400"
          >
            <div className="flex items-center gap-3">
              <StatusBadge status={card.status} />
              <span className="font-semibold">{card.title}</span>
              <span className="font-mono text-xs text-stone-500">
                {card.thought_id}
              </span>
              <span className="ml-auto text-xs text-stone-500">
                {card.importance} / v{card.version}
              </span>
            </div>
            {card.core_claim && (
              <p className="mt-1 truncate text-sm text-stone-600">
                {card.core_claim}
              </p>
            )}
          </Link>
        ))}
        {!cards?.length && (
          <p className="py-8 text-center text-stone-500">
            カードがありません(worker: uv run python -m src.distill cards で生成)
          </p>
        )}
      </div>
    </div>
  );
}
