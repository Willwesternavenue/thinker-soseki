"use server";

import { revalidatePath } from "next/cache";
import { createClient } from "@/lib/supabase/server";
import { requireAdmin } from "@/lib/auth";

/**
 * 人間評価スコアの保存(仕様14.3: 各0〜5点)。
 * reasonAlignment は理由一致4分類(Regression Suite仕様v0.2 3.2)。
 * B判定(結論は近いが理由が違う)+noteがL3 Judgment Rule候補の主要データ源になる。
 */
export async function saveScores(
  evaluationId: string,
  scores: {
    thought_consistency: number;
    persona: number;
    evidence_fit: number;
    no_meta_leak: number;
    safety: number;
  },
  issues: string,
  reasonAlignment: "A" | "B" | "C" | "D" | null,
  reasonAlignmentNote: string
): Promise<{ error?: string }> {
  await requireAdmin();
  const supabase = await createClient();
  const { error } = await supabase
    .from("evaluation_logs")
    .update({
      scores,
      issues: issues
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean),
      reason_alignment: reasonAlignment,
      reason_alignment_note: reasonAlignmentNote.trim() || null,
    })
    .eq("evaluation_id", evaluationId);
  if (error) return { error: error.message };
  revalidatePath("/admin/evaluations");
  return {};
}
