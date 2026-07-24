"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { requireAdmin } from "@/lib/auth";
import { buildCleanTxt, type Turn } from "@/lib/transcripts/prep";

/** 下書きを作成してレビューページへ遷移する。 */
export async function createDraft(
  formData: FormData
): Promise<{ error?: string }> {
  await requireAdmin();
  const supabase = await createClient();
  const title = ((formData.get("title") as string) || "").trim();
  const rawText = ((formData.get("raw_text") as string) || "").trim();
  const videoUrl = ((formData.get("video_url") as string) || "").trim() || null;
  const hint = ((formData.get("hint") as string) || "").trim() || null;
  const priority = (formData.get("priority") as string) || "core";
  if (!title || !rawText) return { error: "タイトルと本文は必須です" };

  const { data, error } = await supabase
    .from("transcript_drafts")
    .insert({ title, raw_text: rawText, video_url: videoUrl, hint, priority })
    .select("draft_id")
    .single();
  if (error) return { error: error.message };

  revalidatePath("/admin/transcripts");
  redirect(`/admin/transcripts/${data.draft_id}`);
}

/** レビュー中の編集内容を保存する。 */
export async function saveTurns(
  draftId: string,
  turns: Turn[]
): Promise<{ error?: string }> {
  await requireAdmin();
  const supabase = await createClient();
  const { error } = await supabase
    .from("transcript_drafts")
    .update({ turns })
    .eq("draft_id", draftId);
  if (error) return { error: error.message };
  return {};
}

/** 確定: 整形TXTをStorageへ保存し sources + ingestion_jobs を作成する。 */
export async function ingestDraft(
  draftId: string
): Promise<{ error?: string; sourceId?: string }> {
  await requireAdmin();
  const supabase = await createClient();
  const { data: draft } = await supabase
    .from("transcript_drafts")
    .select("*")
    .eq("draft_id", draftId)
    .single();
  if (!draft) return { error: "下書きが見つかりません" };
  if (draft.status === "ingested") return { error: "取り込み済みです" };

  const turns = (draft.turns ?? []) as Turn[];
  if (turns.length === 0) return { error: "整形結果がありません" };
  // 除外済みターンは取り込まれないので話者未確定でも構わない
  const unresolved = turns.filter(
    (t) => t.speaker === "?" && !t.excluded
  ).length;
  if (unresolved > 0) {
    return {
      error: `話者未確定(?)のターンが${unresolved}件あります。レビューで確定してください`,
    };
  }

  // sources採番(uploadSourceと同じ流儀。VIDEO固定)
  const { data: existing } = await supabase
    .from("sources")
    .select("source_id")
    .like("source_id", "VIDEO\\_%")
    .order("source_id", { ascending: false })
    .limit(1);
  const lastNum = existing?.[0]
    ? parseInt(existing[0].source_id.split("_").pop() ?? "0", 10)
    : 0;
  const sourceId = `VIDEO_${String(lastNum + 1).padStart(3, "0")}`;

  const txt = buildCleanTxt(
    draft.title as string,
    draft.video_url as string | null,
    turns
  );
  const storagePath = `${sourceId}/original.txt`;
  const { error: uploadError } = await supabase.storage
    .from("originals")
    .upload(storagePath, new Blob([txt], { type: "text/plain" }), {
      upsert: true,
    });
  if (uploadError) return { error: `アップロード失敗: ${uploadError.message}` };

  const { error: sourceError } = await supabase.from("sources").insert({
    source_id: sourceId,
    person_id: "x_shigyo",
    title: draft.title,
    source_type: "video_transcript",
    author: "執行草舟",
    file_type: "txt",
    priority: draft.priority,
    status: "raw",
    original_file_path: storagePath,
  });
  if (sourceError) return { error: `sources作成失敗: ${sourceError.message}` };

  const { error: jobError } = await supabase
    .from("ingestion_jobs")
    .insert({ source_id: sourceId, status: "pending" });
  if (jobError) return { error: `ジョブ作成失敗: ${jobError.message}` };

  await supabase
    .from("transcript_drafts")
    .update({ status: "ingested", source_id: sourceId })
    .eq("draft_id", draftId);

  revalidatePath("/admin/transcripts");
  revalidatePath("/admin/sources");
  revalidatePath("/admin/jobs");
  return { sourceId };
}
