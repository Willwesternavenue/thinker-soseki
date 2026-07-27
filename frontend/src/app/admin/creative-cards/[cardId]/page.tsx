import Link from "next/link";
import { notFound } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { StatusBadge } from "../../cards/status-badge";
import { evidenceTypeLabel } from "../approval";
import { ReviewForm } from "./review-form";

export const dynamic = "force-dynamic";

/**
 * 創作カードの承認画面(T3b)。
 *
 * カードの主張と**根拠原文を並べて表示**する。承認は生成へ直結するため、
 * 人が原典と突き合わせてから押せる配置にしている(指示書§9 Pass4)。
 */
export default async function CreativeCardPage({
  params,
}: {
  params: Promise<{ cardId: string }>;
}) {
  const { cardId } = await params;
  const supabase = await createClient();

  const { data: card } = await supabase
    .from("creative_cards")
    .select("*")
    .eq("card_id", cardId)
    .single();
  if (!card) notFound();

  const evidenceIds: string[] = card.evidence_chunk_ids ?? [];
  const { data: chunks } = await supabase
    .from("source_chunks")
    .select("chunk_id, source_id, chapter_title, text, speaker_role")
    .in("chunk_id", evidenceIds.length > 0 ? evidenceIds : ["__none__"]);

  const chunkById = new Map((chunks ?? []).map((c) => [c.chunk_id as string, c]));
  const missing = evidenceIds.filter((id) => !chunkById.has(id));

  const sourceIds = [...new Set((chunks ?? []).map((c) => c.source_id as string))];
  const { data: sources } = await supabase
    .from("sources")
    .select("source_id, title, corpus_role, document_genre")
    .in("source_id", sourceIds.length > 0 ? sourceIds : ["__none__"]);
  const sourceById = new Map((sources ?? []).map((s) => [s.source_id as string, s]));

  const patterns = (key: string): string[] => (card[key] as string[] | null) ?? [];

  return (
    <div className="max-w-4xl space-y-6">
      <Link href="/admin/creative-cards" className="text-sm text-blue-700 underline">
        ← 創作カード一覧
      </Link>

      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={card.status as string} />
          <span className="rounded bg-stone-100 px-2 py-0.5 text-xs text-stone-700">
            {card.card_type as string}
          </span>
          <span className="text-xs text-stone-500">
            {evidenceTypeLabel(card.evidence_type as string | null)}
          </span>
        </div>
        <h1 className="text-xl font-bold">{card.title as string}</h1>
        {card.summary && <p className="text-stone-700">{card.summary as string}</p>}
      </div>

      {(patterns("positive_patterns").length > 0 ||
        patterns("negative_patterns").length > 0) && (
        <div className="grid gap-4 md:grid-cols-2">
          <PatternList
            title="この操作が現れる形"
            items={patterns("positive_patterns")}
            tone="positive"
          />
          <PatternList
            title="やってはいけない形"
            items={patterns("negative_patterns")}
            tone="negative"
          />
        </div>
      )}

      <section className="space-y-3">
        <h2 className="font-bold">
          根拠原文（{evidenceIds.length}件）
          <span className="ml-2 text-sm font-normal text-stone-500">
            カードの主張がこの原文から読み取れるかを確認してください
          </span>
        </h2>

        {evidenceIds.map((id) => {
          const chunk = chunkById.get(id);
          if (!chunk) {
            return (
              <div
                key={id}
                className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800"
              >
                <code>{id}</code> は実在しません（原典が取り込み直された可能性）
              </div>
            );
          }
          const source = sourceById.get(chunk.source_id as string);
          return (
            <div key={id} className="rounded border border-stone-200 p-3">
              <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-stone-500">
                <span className="font-medium text-stone-700">
                  {source?.title ?? chunk.source_id}
                </span>
                {chunk.chapter_title && <span>（{chunk.chapter_title as string}）</span>}
                {source?.corpus_role && (
                  <span className="rounded bg-stone-100 px-1.5 py-0.5">
                    {source.corpus_role as string}
                  </span>
                )}
                {chunk.speaker_role && (
                  <span className="rounded bg-stone-100 px-1.5 py-0.5">
                    {chunk.speaker_role as string}
                  </span>
                )}
                <code className="text-stone-400">{id}</code>
              </div>
              <p className="whitespace-pre-wrap text-sm leading-relaxed">
                {chunk.text as string}
              </p>
            </div>
          );
        })}

        {evidenceIds.length === 0 && (
          <p className="rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
            根拠が登録されていません。このカードは承認できません。
          </p>
        )}
      </section>

      <section className="space-y-3 border-t border-stone-200 pt-4">
        <h2 className="font-bold">承認</h2>
        <p className="text-sm text-stone-600">
          承認したカードは生成に使われます。未承認のカードは使われません。
        </p>
        <ReviewForm
          cardId={cardId}
          status={card.status as string}
          hasMissingEvidence={missing.length > 0 || evidenceIds.length === 0}
        />
        {card.reviewed_by && (
          <p className="text-xs text-stone-500">
            最終更新: {card.reviewed_by as string} /{" "}
            {new Date(card.reviewed_at as string).toLocaleString("ja-JP")}
          </p>
        )}
      </section>
    </div>
  );
}

function PatternList({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone: "positive" | "negative";
}) {
  if (items.length === 0) return null;
  const color = tone === "positive" ? "text-green-800" : "text-red-700";
  return (
    <div>
      <h3 className="mb-1 text-sm font-medium text-stone-600">{title}</h3>
      <ul className="space-y-1 text-sm">
        {items.map((item) => (
          <li key={item} className={color}>
            {tone === "positive" ? "+ " : "− "}
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}
