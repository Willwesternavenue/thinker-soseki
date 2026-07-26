"use server";

import { revalidatePath } from "next/cache";
import { createClient } from "@/lib/supabase/server";
import { requireAdmin } from "@/lib/auth";

const ID_PREFIX: Record<string, string> = {
  book: "BOOK",
  video_transcript: "VIDEO",
  interview: "INTV",
  dialogue: "DLG",
  lecture: "LECT",
  article: "ART",
  essay: "ESSAY",
  profile: "PROF",
  document: "DOC",
  other: "OTH",
};

function guessFileType(fileName: string): string {
  const lower = fileName.toLowerCase();
  if (lower.endsWith(".pdf")) return "pdf";
  if (lower.endsWith(".docx") || lower.endsWith(".doc")) return "docx";
  if (lower.endsWith(".txt")) return "txt";
  throw new Error(`未対応の拡張子です: ${fileName}(PDF / Word / TXT のみ対応)`);
}

/** 原典アップロード(仕様6.1)。Storage保存 + sources + ingestion_jobs 作成。adminのみ。 */
export async function uploadSource(formData: FormData): Promise<{ error?: string }> {
  await requireAdmin();
  const supabase = await createClient();

  const file = formData.get("file") as File | null;
  const title = (formData.get("title") as string)?.trim();
  const sourceType = formData.get("source_type") as string;
  const priority = (formData.get("priority") as string) || "support";
  const author = ((formData.get("author") as string) || "").trim() || null;

  if (!file || !title || !sourceType) {
    return { error: "ファイル・タイトル・種別は必須です" };
  }

  let fileType: string;
  try {
    fileType = guessFileType(file.name);
  } catch (e) {
    return { error: (e as Error).message };
  }

  // 連番でsource_idを採番(例: BOOK_001)
  const prefix = ID_PREFIX[sourceType] ?? "DOC";
  const { data: existing } = await supabase
    .from("sources")
    .select("source_id")
    .like("source_id", `${prefix}\\_%`)
    .order("source_id", { ascending: false })
    .limit(1);
  const lastNum = existing?.[0]
    ? parseInt(existing[0].source_id.split("_").pop() ?? "0", 10)
    : 0;
  const sourceId = `${prefix}_${String(lastNum + 1).padStart(3, "0")}`;

  // Storageのオブジェクトキーは日本語・スペース不可のため、拡張子のみ引き継いだ固定名にする
  const ext = file.name.toLowerCase().split(".").pop() ?? "bin";
  const storagePath = `${sourceId}/original.${ext}`;
  const { error: uploadError } = await supabase.storage
    .from("originals")
    .upload(storagePath, file, { upsert: true });
  if (uploadError) return { error: `アップロード失敗: ${uploadError.message}` };

  const { error: sourceError } = await supabase.from("sources").insert({
    source_id: sourceId,
    person_id: "merleau_ponty",
    title,
    source_type: sourceType,
    author,
    file_type: fileType,
    priority,
    status: "raw",
    original_file_path: storagePath,
  });
  if (sourceError) return { error: `sources作成失敗: ${sourceError.message}` };

  const { error: jobError } = await supabase
    .from("ingestion_jobs")
    .insert({ source_id: sourceId, status: "pending" });
  if (jobError) return { error: `ジョブ作成失敗: ${jobError.message}` };

  revalidatePath("/admin/sources");
  revalidatePath("/admin/jobs");
  return {};
}

/**
 * 原典の無効化/再有効化(adminのみ)。
 *
 * 検索(RPC・evidence)もカード生成も source_chunks.status='active' で絞るため、
 * チャンクを 'disabled' にすれば全経路から外れる。原典の行とStorageは残すので
 * 誤操作しても再有効化で戻せる。
 *
 * 'superseded'(再チャンクで置き換わった旧版)は触らない。混ぜると再有効化時に
 * 古いチャンクまで復活してしまうため、active ⇄ disabled のみを入れ替える。
 */
export async function setSourceEnabled(
  sourceId: string,
  enabled: boolean
): Promise<{ error?: string }> {
  await requireAdmin();
  const supabase = await createClient();

  const { error } = await supabase
    .from("source_chunks")
    .update({ status: enabled ? "active" : "disabled" })
    .eq("source_id", sourceId)
    .eq("status", enabled ? "disabled" : "active");
  if (error) return { error: `状態の更新に失敗: ${error.message}` };

  revalidatePath("/admin/sources");
  return {};
}
