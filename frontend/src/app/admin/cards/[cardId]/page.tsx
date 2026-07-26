import Link from "next/link";
import { notFound } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { StatusBadge } from "../status-badge";
import { CardEditor } from "./card-editor";
import { LinkReview } from "./link-review";

export const dynamic = "force-dynamic";

export default async function CardDetailPage({
  params,
}: {
  params: Promise<{ cardId: string }>;
}) {
  const { cardId } = await params;
  const supabase = await createClient();

  const { data: card } = await supabase
    .from("thought_cards")
    .select("*")
    .eq("card_id", cardId)
    .single();
  if (!card) notFound();

  const { data: links } = await supabase
    .from("thought_evidence_links")
    .select(
      "link_id, chunk_id, evidence_role, strength, quote_allowed, status, note, source_chunks(verbatim, text)"
    )
    .eq("thought_id", card.thought_id)
    .order("link_id");

  const { data: questions } = await supabase
    .from("thought_questions")
    .select("question_id, question, intent, status")
    .eq("target_card_id", cardId)
    .order("question_id");

  const { data: revisions } = await supabase
    .from("thought_card_revisions")
    .select("revision_id, version, created_at")
    .eq("card_id", cardId)
    .order("revision_id", { ascending: false })
    .limit(10);

  const linkRows = (links ?? []).map((l) => {
    const chunk = Array.isArray(l.source_chunks) ? l.source_chunks[0] : l.source_chunks;
    return {
      link_id: l.link_id,
      chunk_id: l.chunk_id,
      evidence_role: l.evidence_role,
      strength: l.strength,
      quote_allowed: l.quote_allowed,
      status: l.status,
      note: l.note,
      verbatim: chunk?.verbatim ?? false,
      chunk_text: chunk?.text ?? "",
    };
  });

  return (
    <div className="space-y-8">
      <div>
        <Link href="/admin/cards" className="text-xs text-stone-500 hover:text-stone-700">
          ← カード一覧へ
        </Link>
        <div className="mt-2 flex items-center gap-3">
          <h1 className="text-xl font-bold">{card.title}</h1>
          <StatusBadge status={card.status} />
          <span className="font-mono text-xs text-stone-500">
            {card.thought_id} / v{card.version}
          </span>
        </div>
        <p className="mt-1 text-xs text-stone-500">
          カードの内容は回答方針であり、本人の発言そのものではない(仕様5.6)
        </p>
      </div>

      <CardEditor card={card} />

      <section className="space-y-3">
        <h2 className="font-semibold">
          原典リンク(正本)とquote_allowedレビュー
        </h2>
        <p className="text-xs text-stone-500">
          引用可能条件: evidence_role=quote かつ verbatim=true かつ quote_allowed=true(仕様7.8)。
          リンクの承認/却下は派生列 related_thought_ids に自動反映されます。
        </p>
        <LinkReview links={linkRows} />
      </section>

      <section className="space-y-3">
        <h2 className="font-semibold">質問対応情報({questions?.length ?? 0}件)</h2>
        <ul className="space-y-1">
          {(questions ?? []).map((q) => (
            <li key={q.question_id} className="flex items-center gap-2 text-sm">
              <StatusBadge status={q.status} />
              <span className="rounded bg-stone-200 px-1.5 py-0.5 text-xs">
                {q.intent}
              </span>
              <span className="text-stone-700">{q.question}</span>
            </li>
          ))}
        </ul>
        <p className="text-xs text-stone-500">
          編集は<Link href="/admin/questions" className="underline">質問対応情報管理</Link>で。
          カード承認時にdraft質問はactive化されます。
        </p>
      </section>

      <section className="space-y-2">
        <h2 className="font-semibold">編集履歴</h2>
        <ul className="text-xs text-stone-500">
          {(revisions ?? []).map((r) => (
            <li key={r.revision_id}>
              v{r.version} — {new Date(r.created_at).toLocaleString("ja-JP")}
            </li>
          ))}
          {!revisions?.length && <li>履歴なし</li>}
        </ul>
      </section>
    </div>
  );
}
