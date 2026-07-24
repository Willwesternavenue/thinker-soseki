"use server";

import { revalidatePath } from "next/cache";
import { createClient } from "@/lib/supabase/server";
import { requireAdmin } from "@/lib/auth";

export type PersonaFields = {
  display_name: string;
  system_prompt: string;
  first_person: string;
  banned_terms_exact: string; // 1行1語
  banned_terms_contextual: string; // 1行1語
  tone: string;
  catchphrases_usage: string;
  max_quote_length: number;
  no_assert: string; // 1行1項目
  fallback_card_id: string;
};

function splitLines(s: string): string[] {
  return s.split("\n").map((l) => l.trim()).filter(Boolean);
}

/**
 * ペルソナ(プロンプト)設定の保存(仕様5.1)。
 * system_promptは回答生成のシステムプロンプトとして毎回使われるため、
 * 保存した瞬間から次の回答に反映される。
 */
export async function savePersona(
  personId: string,
  fields: PersonaFields
): Promise<{ error?: string }> {
  await requireAdmin();
  const supabase = await createClient();

  if (!fields.system_prompt.trim()) {
    return { error: "システムプロンプトは必須です" };
  }
  if (!fields.first_person.trim()) {
    return { error: "一人称は必須です" };
  }

  // フォールバックカードはapprovedであることを確認(仕様2.3の不変条件の前提)
  const { data: card } = await supabase
    .from("thought_cards")
    .select("card_id, status")
    .eq("card_id", fields.fallback_card_id)
    .maybeSingle();
  if (!card || card.status !== "approved") {
    return {
      error: "フォールバックカードはapprovedのカードを指定してください",
    };
  }

  const { error } = await supabase
    .from("personas")
    .update({
      display_name: fields.display_name,
      system_prompt: fields.system_prompt,
      first_person: fields.first_person.trim(),
      banned_terms_exact: splitLines(fields.banned_terms_exact),
      banned_terms_contextual: splitLines(fields.banned_terms_contextual),
      style_rules: {
        tone: fields.tone,
        catchphrases_usage: fields.catchphrases_usage,
      },
      quote_policy: {
        max_quote_length: Number(fields.max_quote_length) || 100,
        require_verbatim: true,
      },
      safety_policy: { no_assert: splitLines(fields.no_assert) },
      fallback_card_id: fields.fallback_card_id,
    })
    .eq("person_id", personId);
  if (error) return { error: error.message };

  revalidatePath("/admin/persona");
  return {};
}
