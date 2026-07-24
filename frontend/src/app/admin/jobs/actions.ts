"use server";

import { revalidatePath } from "next/cache";
import { createAdminClient } from "@/lib/supabase/admin";
import { requireAdmin } from "@/lib/auth";

export type JobRowData = {
  job_id: string;
  source_id: string;
  status: string;
  current_step: string | null;
  error_message: string | null;
  updated_at: string;
  created_at: string;
  sources: { title: string | null } | { title: string | null }[] | null;
};

export type HeartbeatData = {
  status: string;
  current_source_id: string | null;
  last_seen_at: string;
};

/** ジョブ一覧+Worker heartbeat(ジョブ画面のポーリング用)。adminのみ。 */
export async function fetchJobsData(): Promise<{
  jobs?: JobRowData[];
  heartbeat?: HeartbeatData | null;
  error?: string;
}> {
  await requireAdmin();
  const supabase = createAdminClient();
  const [jobsRes, hbRes] = await Promise.all([
    supabase
      .from("ingestion_jobs")
      .select(
        "job_id, source_id, status, current_step, error_message, updated_at, created_at, sources(title)"
      )
      .order("created_at", { ascending: false })
      .limit(2000),
    supabase
      .from("worker_heartbeats")
      .select("status, current_source_id, last_seen_at")
      .eq("worker_name", "ingestion")
      .maybeSingle(),
  ]);
  if (jobsRes.error) return { error: jobsRes.error.message };
  return {
    jobs: (jobsRes.data as JobRowData[]) ?? [],
    heartbeat: (hbRes.data as HeartbeatData) ?? null,
  };
}

/** 失敗ジョブの再実行(仕様10.1)。pendingへ戻すとWorkerが拾い直す。 */
export async function rerunJob(jobId: string): Promise<{ error?: string }> {
  await requireAdmin();
  const supabase = createAdminClient();
  const { error } = await supabase
    .from("ingestion_jobs")
    .update({ status: "pending", error_message: null, current_step: null })
    .eq("job_id", jobId);
  if (error) return { error: error.message };
  revalidatePath("/admin/jobs");
  return {};
}
