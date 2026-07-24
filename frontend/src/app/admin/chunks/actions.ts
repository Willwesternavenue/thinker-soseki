"use server";

import { revalidatePath } from "next/cache";
import { createClient } from "@/lib/supabase/server";
import { requireAdmin } from "@/lib/auth";

/** 重要度変更(仕様10.2)。重蒸留の対象は importance=high。 */
export async function updateImportance(
  distillationId: string,
  importance: "high" | "normal" | "low"
): Promise<{ error?: string }> {
  await requireAdmin();
  const supabase = await createClient();
  const { error } = await supabase
    .from("chunk_distillations")
    .update({ importance })
    .eq("distillation_id", distillationId);
  if (error) return { error: error.message };
  revalidatePath("/admin/chunks");
  return {};
}
