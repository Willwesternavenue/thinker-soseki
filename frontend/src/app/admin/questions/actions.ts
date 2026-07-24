"use server";

import { revalidatePath } from "next/cache";
import { createClient } from "@/lib/supabase/server";
import { requireAdmin } from "@/lib/auth";
import { embedText } from "@/lib/embedding";

export async function saveQuestion(
  questionId: string,
  fields: {
    question: string;
    target_thought_id: string;
    intent: string;
    answer_direction: string;
    status: string;
  }
): Promise<{ error?: string }> {
  await requireAdmin();
  const supabase = await createClient();
  // 対象カードをthought_idから引き直す
  const { data: card } = await supabase
    .from("thought_cards")
    .select("card_id")
    .eq("thought_id", fields.target_thought_id)
    .neq("status", "rejected")
    .limit(1)
    .maybeSingle();

  const { error } = await supabase
    .from("thought_questions")
    .update({
      question: fields.question,
      target_thought_id: fields.target_thought_id,
      target_card_id: card?.card_id ?? null,
      intent: fields.intent,
      answer_direction: fields.answer_direction || null,
      status: fields.status,
      embedding: JSON.stringify(await embedText(fields.question)),
    })
    .eq("question_id", questionId);
  if (error) return { error: error.message };
  revalidatePath("/admin/questions");
  return {};
}

export async function addQuestion(fields: {
  question: string;
  target_thought_id: string;
  intent: string;
  answer_direction: string;
}): Promise<{ error?: string }> {
  await requireAdmin();
  const supabase = await createClient();
  const { data: card } = await supabase
    .from("thought_cards")
    .select("card_id")
    .eq("thought_id", fields.target_thought_id)
    .neq("status", "rejected")
    .limit(1)
    .maybeSingle();

  const questionId = `q_manual_${Date.now().toString(36)}`;
  const { error } = await supabase.from("thought_questions").insert({
    question_id: questionId,
    person_id: "x_shigyo",
    question: fields.question,
    target_thought_id: fields.target_thought_id,
    target_card_id: card?.card_id ?? null,
    intent: fields.intent,
    answer_direction: fields.answer_direction || null,
    embedding: JSON.stringify(await embedText(fields.question)),
    status: "active",
  });
  if (error) return { error: error.message };
  revalidatePath("/admin/questions");
  return {};
}

export async function deleteQuestion(questionId: string): Promise<{ error?: string }> {
  await requireAdmin();
  const supabase = await createClient();
  const { error } = await supabase
    .from("thought_questions")
    .delete()
    .eq("question_id", questionId);
  if (error) return { error: error.message };
  revalidatePath("/admin/questions");
  return {};
}

export type TestHit = {
  question_id: string;
  target_thought_id: string;
  question: string;
  intent: string;
  similarity: number;
};

/** テスト検索(仕様10.2): クエリをembeddingし match_thought_questions を実行。 */
export async function testSearch(query: string): Promise<{
  hits?: TestHit[];
  error?: string;
}> {
  await requireAdmin();
  const supabase = await createClient();
  const embedding = await embedText(query);
  const { data, error } = await supabase.rpc("match_thought_questions", {
    query_embedding: JSON.stringify(embedding),
    target_person_id: "x_shigyo",
    match_count: 10,
  });
  if (error) return { error: error.message };
  return { hits: data as TestHit[] };
}
