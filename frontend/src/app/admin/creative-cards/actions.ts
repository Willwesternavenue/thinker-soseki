"use server";

import { revalidatePath } from "next/cache";
import { createClient } from "@/lib/supabase/server";
import { requireAdmin } from "@/lib/auth";
import { checkApprovable } from "./approval";

/**
 * 創作カードの承認(T3b)。
 *
 * ⚠️ 承認は生成へ直結する。**根拠チャンクが実在するかを必ず確かめてから**承認する
 * (指示書§14.5)。worker 側の approve_card と同じ規律。
 */
export async function approveCreativeCard(
  cardId: string
): Promise<{ error?: string }> {
  const auth = await requireAdmin();
  const supabase = await createClient();

  const { data: card } = await supabase
    .from("creative_cards")
    .select("card_id, evidence_chunk_ids")
    .eq("card_id", cardId)
    .single();
  if (!card) return { error: "カードが見つかりません" };

  const evidence: string[] = card.evidence_chunk_ids ?? [];
  const { data: found } = await supabase
    .from("source_chunks")
    .select("chunk_id")
    .in("chunk_id", evidence.length > 0 ? evidence : ["__none__"]);

  const check = checkApprovable(
    evidence,
    (found ?? []).map((r) => r.chunk_id)
  );
  if (!check.ok) return { error: check.reason };

  const { error } = await supabase
    .from("creative_cards")
    .update({
      status: "approved",
      reviewed_by: auth.user.id,
      reviewed_at: new Date().toISOString(),
    })
    .eq("card_id", cardId);
  if (error) return { error: error.message };

  revalidatePath("/admin/creative-cards");
  revalidatePath(`/admin/creative-cards/${cardId}`);
  return {};
}

/** 却下。却下したカードは生成にも再生成候補にも入らない。 */
export async function rejectCreativeCard(
  cardId: string
): Promise<{ error?: string }> {
  return setStatus(cardId, "rejected");
}

/** 承認を取り消して下書きへ戻す。 */
export async function unapproveCreativeCard(
  cardId: string
): Promise<{ error?: string }> {
  return setStatus(cardId, "draft");
}

async function setStatus(
  cardId: string,
  status: "rejected" | "draft"
): Promise<{ error?: string }> {
  const auth = await requireAdmin();
  const supabase = await createClient();

  const { error } = await supabase
    .from("creative_cards")
    .update({
      status,
      reviewed_by: auth.user.id,
      reviewed_at: new Date().toISOString(),
    })
    .eq("card_id", cardId);
  if (error) return { error: error.message };

  revalidatePath("/admin/creative-cards");
  revalidatePath(`/admin/creative-cards/${cardId}`);
  return {};
}
