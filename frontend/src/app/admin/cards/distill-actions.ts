"use server";

import { createAdminClient } from "@/lib/supabase/admin";
import { requireAdmin } from "@/lib/auth";

export type DistillationJob = {
  job_id: string;
  status: string;
  current_step: string | null;
  result: Record<string, number> | null;
  error_message: string | null;
};

/** 最新の蒸留ジョブ(カード生成ボタンのポーリング用)。adminのみ。 */
export async function getLatestDistillationJob(): Promise<{
  job?: DistillationJob | null;
  error?: string;
}> {
  await requireAdmin();
  const supabase = createAdminClient();
  const { data, error } = await supabase
    .from("distillation_jobs")
    .select("job_id, status, current_step, result, error_message")
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle();
  if (error) return { error: error.message };
  return { job: (data as DistillationJob) ?? null };
}

/**
 * 蒸留→カード生成ジョブを投入する(仕様6.6〜6.9)。
 * Workerが distillation_jobs を拾って重蒸留・原典蒸留・カード候補生成・質問生成を実行する。
 * 既に実行中/待機中のジョブがある場合は二重投入しない。
 */
export async function startDistillation(): Promise<{ jobId?: string; error?: string }> {
  await requireAdmin();
  const supabase = createAdminClient();

  const { data: active } = await supabase
    .from("distillation_jobs")
    .select("job_id")
    .in("status", ["pending", "running"])
    .limit(1);
  if (active && active.length > 0) {
    return { error: "すでに蒸留ジョブが実行中です。完了までお待ちください。" };
  }

  const { data, error } = await supabase
    .from("distillation_jobs")
    .insert({ person_id: "x_shigyo", kind: "all", status: "pending" })
    .select("job_id")
    .single();
  if (error) return { error: error.message };
  return { jobId: data.job_id };
}
