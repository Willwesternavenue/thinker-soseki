"use server";

import { revalidatePath } from "next/cache";
import { createClient } from "@/lib/supabase/server";
import { requireAdmin } from "@/lib/auth";

/** 用語または使い分けルールを追加する。 */
export async function addGlossaryTerm(formData: FormData): Promise<{
  error?: string;
}> {
  await requireAdmin();
  const supabase = await createClient();
  const kind = (formData.get("kind") as string) === "rule" ? "rule" : "term";
  const content = ((formData.get("content") as string) || "").trim();
  const reading = ((formData.get("reading") as string) || "").trim() || null;
  const note = ((formData.get("note") as string) || "").trim() || null;
  if (!content) return { error: "表記は必須です" };

  const { error } = await supabase
    .from("glossary_terms")
    .insert({ kind, content, reading, note });
  if (error) return { error: error.message };

  revalidatePath("/admin/glossary");
  return {};
}

/** 用語の1フィールドを更新する(セル直接編集)。 */
export async function updateGlossaryTerm(
  id: string,
  field: "content" | "reading" | "note",
  value: string
): Promise<{ error?: string }> {
  await requireAdmin();
  const supabase = await createClient();
  const v = value.trim();
  if (field === "content" && !v) return { error: "表記は空にできません" };
  const { error } = await supabase
    .from("glossary_terms")
    .update({ [field]: v || null })
    .eq("id", id);
  if (error) return { error: error.message };

  revalidatePath("/admin/glossary");
  return {};
}

/** 用語を削除する。 */
export async function deleteGlossaryTerm(id: string): Promise<{
  error?: string;
}> {
  await requireAdmin();
  const supabase = await createClient();
  const { error } = await supabase
    .from("glossary_terms")
    .delete()
    .eq("id", id);
  if (error) return { error: error.message };

  revalidatePath("/admin/glossary");
  return {};
}
