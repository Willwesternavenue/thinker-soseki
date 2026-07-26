import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";
import type { Persona, ThoughtCard } from "./types";

/** approvedカードをthought_idでID直接取得(仕様7.5。ベクトル検索は使わない)。 */
export async function fetchApprovedThoughtCards(
  db: SupabaseClient,
  personId: string,
  thoughtIds: string[]
): Promise<ThoughtCard[]> {
  if (!thoughtIds.length) return [];
  const { data } = await db
    .from("thought_cards")
    .select(
      "card_id, thought_id, title, importance, core_claim, distinctions, answer_policy, prohibitions, related_thought_ids, representative_chunk_ids"
    )
    .eq("person_id", personId)
    .eq("status", "approved")
    .in("thought_id", thoughtIds);
  // thought_ids の順序を保つ(先頭=primary)
  const byId = new Map((data ?? []).map((c) => [c.thought_id, c as ThoughtCard]));
  return thoughtIds.map((tid) => byId.get(tid)).filter(Boolean) as ThoughtCard[];
}

/** 全approvedカード(LLM分類器の入力用)。 */
export async function fetchAllApprovedCards(
  db: SupabaseClient,
  personId: string
): Promise<ThoughtCard[]> {
  const { data } = await db
    .from("thought_cards")
    .select(
      "card_id, thought_id, title, importance, core_claim, distinctions, answer_policy, prohibitions, related_thought_ids, representative_chunk_ids"
    )
    .eq("person_id", personId)
    .eq("status", "approved");
  return (data ?? []) as ThoughtCard[];
}

/** 汎用フォールバックカードの解決(仕様2.3 / 7.4 Stage5)。 */
export async function resolveFallbackCard(
  db: SupabaseClient,
  persona: Persona
): Promise<ThoughtCard | null> {
  if (!persona.fallback_card_id) return null;
  const { data } = await db
    .from("thought_cards")
    .select(
      "card_id, thought_id, title, importance, core_claim, distinctions, answer_policy, prohibitions, related_thought_ids, representative_chunk_ids"
    )
    .eq("card_id", persona.fallback_card_id)
    .eq("status", "approved")
    .maybeSingle();
  return (data as ThoughtCard) ?? null;
}

export type MergedCards = {
  primary: ThoughtCard;
  secondaries: ThoughtCard[];
  prohibitions: string[]; // 和集合
  all: ThoughtCard[];
};

/**
 * カード統合(仕様7.5): primary 1 + secondary 最大2。
 * prohibitions は和集合。複雑な統合ロジックは作らない。
 */
export function mergeThoughtCards(
  primary: ThoughtCard,
  secondaries: ThoughtCard[]
): MergedCards {
  const limited = secondaries.slice(0, 2);
  const prohibitions = [
    ...new Set([primary, ...limited].flatMap((c) => c.prohibitions ?? [])),
  ];
  return { primary, secondaries: limited, prohibitions, all: [primary, ...limited] };
}
