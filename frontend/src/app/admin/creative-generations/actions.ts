"use server";

import { randomUUID } from "node:crypto";
import { createClient } from "@/lib/supabase/server";
import { requireAdmin } from "@/lib/auth";
import { buildRerunPayload } from "./monitoring";

export type GenerationRow = {
  job_id: string;
  profile_id: string;
  status: string;
  current_step: string | null;
  display_title: string | null;
  error_message: string | null;
  brief_raw: Record<string, unknown>;
  created_by: string;
  created_at: string;
  updated_at: string;
};

export type HeartbeatRow = {
  status: string;
  current_job_id: string | null;
  last_seen_at: string;
};

/** ジョブ一覧 + Worker heartbeat(監視画面のポーリング用)。adminのみ。 */
export async function fetchCreativeGenerations(): Promise<{
  generations?: GenerationRow[];
  heartbeat?: HeartbeatRow | null;
  profiles?: Record<string, string>;
  error?: string;
}> {
  await requireAdmin();
  const supabase = createClient();

  const [genRes, hbRes, profRes] = await Promise.all([
    supabase
      .from("creative_generations")
      .select(
        "job_id, profile_id, status, current_step, display_title, error_message, brief_raw, created_by, created_at, updated_at"
      )
      .order("created_at", { ascending: false })
      .limit(500),
    supabase
      .from("worker_heartbeats")
      .select("status, current_job_id, last_seen_at")
      .order("last_seen_at", { ascending: false })
      .limit(1),
    supabase.from("creative_profiles").select("profile_id, name"),
  ]);

  if (genRes.error) return { error: genRes.error.message };

  return {
    generations: (genRes.data ?? []) as GenerationRow[],
    heartbeat: ((hbRes.data ?? [])[0] as HeartbeatRow | undefined) ?? null,
    profiles: Object.fromEntries(
      (profRes.data ?? []).map((p) => [p.profile_id as string, p.name as string])
    ),
  };
}

export type TraceRow = {
  used_card_ids: string[];
  injected_source_ids: string[];
  injected_chunk_ids: string[];
  guard_results: Record<string, unknown>;
  model_ids: Record<string, unknown>;
  prompt_versions: Record<string, unknown>;
  regeneration_count: number;
  created_at: string;
};

/** 1ジョブの詳細(trace / guard)。失敗ジョブの原因追跡に使う。 */
export async function fetchCreativeTrace(jobId: string): Promise<{
  generation?: GenerationRow & { outline: unknown; final_text: string | null };
  trace?: TraceRow | null;
  error?: string;
}> {
  await requireAdmin();
  const supabase = createClient();

  const { data: generation } = await supabase
    .from("creative_generations")
    .select("*")
    .eq("job_id", jobId)
    .maybeSingle();
  if (!generation) return { error: "生成が見つかりません" };

  // 孤児回収で同一ジョブが再実行され得るため最新1件を採る(trace に一意制約は無い)
  const { data: traces } = await supabase
    .from("creative_traces")
    .select(
      "used_card_ids, injected_source_ids, injected_chunk_ids, guard_results, model_ids, prompt_versions, regeneration_count, created_at"
    )
    .eq("job_id", jobId)
    .order("created_at", { ascending: false })
    .limit(1);

  return {
    generation: generation as GenerationRow & { outline: unknown; final_text: string | null },
    trace: ((traces ?? [])[0] as TraceRow | undefined) ?? null,
  };
}

/**
 * 同じ依頼で新しいジョブを作る。
 *
 * 元のジョブは書き換えない(失敗の記録を残したまま再挑戦できるようにする)。
 */
export async function rerunCreativeGeneration(
  jobId: string
): Promise<{ jobId?: string; error?: string }> {
  const auth = await requireAdmin();
  const supabase = createClient();

  const { data: job } = await supabase
    .from("creative_generations")
    .select("profile_id, brief_raw, generation_settings")
    .eq("job_id", jobId)
    .maybeSingle();
  if (!job) return { error: "生成が見つかりません" };

  // 承認済みカードが無いまま再実行しても同じ理由で失敗する。手前で止める。
  const { count } = await supabase
    .from("creative_cards")
    .select("card_id", { count: "exact", head: true })
    .eq("profile_id", job.profile_id)
    .eq("status", "approved");
  if (!count) {
    return { error: "承認済みの創作カードが0枚のため、再実行しても同じ理由で失敗します" };
  }

  const { data, error } = await supabase
    .from("creative_generations")
    .insert(
      buildRerunPayload(
        job as { profile_id: string; brief_raw: unknown; generation_settings: unknown },
        randomUUID(),
        auth.user.id
      )
    )
    .select("job_id")
    .single();
  if (error) return { error: error.message };

  return { jobId: data.job_id as string };
}
