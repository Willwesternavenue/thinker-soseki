"use server";

import { revalidatePath } from "next/cache";
import { createClient } from "@/lib/supabase/server";
import { requireAdmin } from "@/lib/auth";
import { embedText } from "@/lib/embedding";

export type CardFields = {
  title: string;
  importance: string;
  core_claim: string;
  distinctions: string; // JSON文字列
  answer_policy: string; // 改行区切り
  prohibitions: string; // 改行区切り
  related_thought_ids: string; // カンマ区切り
};

function splitLines(s: string): string[] {
  return s.split("\n").map((l) => l.trim()).filter(Boolean);
}

/** カード編集保存。version++ と編集履歴(revision)を残す(仕様3.4 / 16.5)。 */
export async function saveCard(
  cardId: string,
  fields: CardFields
): Promise<{ error?: string }> {
  const auth = await requireAdmin();
  const supabase = await createClient();

  const { data: current } = await supabase
    .from("thought_cards")
    .select("*")
    .eq("card_id", cardId)
    .single();
  if (!current) return { error: "カードが見つかりません" };

  let distinctions: unknown;
  try {
    distinctions = JSON.parse(fields.distinctions || "[]");
  } catch {
    return { error: "「区別」はJSON配列で入力してください" };
  }

  const searchText = [
    fields.title,
    fields.core_claim,
    ...splitLines(fields.answer_policy),
  ].join(" ");

  // 編集前スナップショットを履歴に残す
  const { error: revError } = await supabase.from("thought_card_revisions").insert({
    card_id: cardId,
    version: current.version,
    snapshot: current,
    edited_by: auth.user.id,
  });
  if (revError) return { error: `履歴保存失敗: ${revError.message}` };

  const { error } = await supabase
    .from("thought_cards")
    .update({
      title: fields.title,
      importance: fields.importance,
      core_claim: fields.core_claim,
      distinctions,
      answer_policy: splitLines(fields.answer_policy),
      prohibitions: splitLines(fields.prohibitions),
      related_thought_ids: fields.related_thought_ids
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
      search_text: searchText,
      embedding: JSON.stringify(await embedText(searchText)),
      version: current.version + 1,
    })
    .eq("card_id", cardId);
  if (error) return { error: error.message };

  revalidatePath(`/admin/cards/${cardId}`);
  revalidatePath("/admin/cards");
  return {};
}

/**
 * ステータス遷移(draft / reviewing / approved / rejected / deprecated)。
 * approved時(仕様6.11):
 * - このカードのdraft質問を active 化
 * - 派生列 related_thought_ids を正本(approvedリンク)から再生成
 */
export async function setCardStatus(
  cardId: string,
  status: "draft" | "reviewing" | "approved" | "rejected" | "deprecated"
): Promise<{ error?: string }> {
  await requireAdmin();
  const supabase = await createClient();

  const { error } = await supabase
    .from("thought_cards")
    .update({ status })
    .eq("card_id", cardId);
  if (error) {
    if (error.code === "23505") {
      return {
        error:
          "同じthought_idのapprovedカードが既に存在します。先に既存カードをdeprecatedにしてください。",
      };
    }
    return { error: error.message };
  }

  if (status === "approved") {
    await supabase
      .from("thought_questions")
      .update({ status: "active" })
      .eq("target_card_id", cardId)
      .eq("status", "draft");
    await supabase.rpc("rebuild_related_thought_ids", {
      target_person_id: "natsume_soseki",
    });
  }
  if (status === "deprecated" || status === "rejected") {
    // 使われなくなったカードの質問はルーティング対象から外す
    await supabase
      .from("thought_questions")
      .update({ status: "inactive" })
      .eq("target_card_id", cardId);
  }

  revalidatePath(`/admin/cards/${cardId}`);
  revalidatePath("/admin/cards");
  return {};
}

/** 原典リンクのレビュー(仕様6.10)。quote_allowedはverbatim=trueのチャンクのみ許可。 */
export async function reviewLink(
  linkId: string,
  update: { status?: "approved" | "rejected" | "draft"; quote_allowed?: boolean }
): Promise<{ error?: string }> {
  await requireAdmin();
  const supabase = await createClient();

  if (update.quote_allowed === true) {
    const { data: link } = await supabase
      .from("thought_evidence_links")
      .select("chunk_id, source_chunks(verbatim)")
      .eq("link_id", linkId)
      .single();
    const chunk = Array.isArray(link?.source_chunks)
      ? link?.source_chunks[0]
      : link?.source_chunks;
    if (!chunk?.verbatim) {
      return {
        error: "verbatim=falseのチャンクは引用可能にできません(仕様7.8)",
      };
    }
  }

  const { error } = await supabase
    .from("thought_evidence_links")
    .update(update)
    .eq("link_id", linkId);
  if (error) return { error: error.message };

  // 正本(リンク)変更 → 派生列を同期(仕様5.3)
  if (update.status !== undefined) {
    await supabase.rpc("rebuild_related_thought_ids", {
      target_person_id: "natsume_soseki",
    });
  }
  revalidatePath("/admin/cards");
  return {};
}
