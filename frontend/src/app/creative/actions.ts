"use server";

import { createClient } from "@/lib/supabase/server";
import { requireUser } from "@/lib/auth";
import type { WorkerHeartbeat } from "@/lib/worker-presence";
import {
  buildBriefRaw,
  isDuplicateKeyError,
  validateBrief,
  type BriefFormInput,
} from "./creative";

export type ProfileOption = {
  profile_id: string;
  name: string;
  description: string | null;
  disclosure_text: string;
};

export type GenerationState = {
  job_id: string;
  status: string;
  current_step: string | null;
  final_text: string | null;
  display_title: string | null;
  outline: unknown;
  error_message: string | null;
  profile_id: string;
  created_at: string;
};

/** 生成に使える(active な)プロファイルのみ返す。draft/archived は選ばせない。 */
export async function listCreativeProfiles(): Promise<{
  profiles?: ProfileOption[];
  error?: string;
}> {
  await requireUser();
  const supabase = createClient();
  const { data, error } = await supabase
    .from("creative_profiles")
    .select("profile_id, name, description, disclosure_text")
    .eq("status", "active")
    .order("name");
  if (error) return { error: error.message };
  return { profiles: (data ?? []) as ProfileOption[] };
}

/**
 * 生成ジョブを作る。本文生成は worker が行うので、ここは INSERT して job_id を返すだけ。
 *
 * `idempotencyKey` はクライアントが uuid で発行する。二重クリック・リトライで同じ key が
 * 来たら**新しいジョブを作らず既存の job_id を返す**（仕様§13）。
 */
export async function createCreativeGeneration(
  input: BriefFormInput,
  idempotencyKey: string
): Promise<{ jobId?: string; error?: string; errors?: string[] }> {
  const auth = await requireUser();

  const validation = validateBrief(input);
  if (!validation.ok) return { errors: validation.errors };
  if (!idempotencyKey) return { error: "リクエストキーがありません" };

  const supabase = createClient();

  // 画面の選択肢が古い場合に備え、active かどうかをここで必ず確かめる。
  // 曖昧なときに別プロファイルへ自動fallbackはしない（仕様§4・T1設計 §2.1）。
  const { data: profile } = await supabase
    .from("creative_profiles")
    .select("profile_id, status, default_generation_settings")
    .eq("profile_id", input.profileId)
    .maybeSingle();
  if (!profile) return { error: "プロファイルが見つかりません" };
  if (profile.status !== "active") {
    return { error: "このプロファイルは現在使用できません（管理者にお問い合わせください）" };
  }

  const { data, error } = await supabase
    .from("creative_generations")
    .insert({
      profile_id: input.profileId,
      brief_raw: buildBriefRaw(input),
      generation_settings: profile.default_generation_settings ?? {},
      idempotency_key: idempotencyKey,
      created_by: auth.user.id,
    })
    .select("job_id")
    .single();

  if (error) {
    if (isDuplicateKeyError(error)) {
      // 同じ key の既存ジョブへ合流する（二重送信を無害化）
      const { data: existing } = await supabase
        .from("creative_generations")
        .select("job_id")
        .eq("idempotency_key", idempotencyKey)
        .maybeSingle();
      if (existing) return { jobId: existing.job_id as string };
    }
    return { error: error.message };
  }

  return { jobId: data.job_id as string };
}

/** 生成状態の取得（UIのポーリング用）。自分のジョブか admin のみ。 */
export async function getCreativeGeneration(
  jobId: string
): Promise<{ generation?: GenerationState; disclosureText?: string; error?: string }> {
  const auth = await requireUser();
  const supabase = createClient();

  const { data, error } = await supabase
    .from("creative_generations")
    .select(
      "job_id, status, current_step, final_text, display_title, outline, error_message, profile_id, created_by, created_at"
    )
    .eq("job_id", jobId)
    .maybeSingle();
  if (error) return { error: error.message };
  if (!data) return { error: "生成が見つかりません" };
  if (data.created_by !== auth.user.id && auth.profile.role !== "admin") {
    return { error: "この生成を参照する権限がありません" };
  }

  // 免責文は profile 側が正。生成時点の値ではなく現在の値を出す
  // （文言を直したら過去の作品にも反映されるべきなので）。
  const { data: profile } = await supabase
    .from("creative_profiles")
    .select("disclosure_text")
    .eq("profile_id", data.profile_id)
    .maybeSingle();

  return {
    generation: data as unknown as GenerationState,
    disclosureText: (profile?.disclosure_text as string | undefined) ?? "",
  };
}

export type CreativeTraceView = {
  used_card_ids: string[];
  injected_source_ids: string[];
  injected_chunk_ids: string[];
  guard_results: Record<string, unknown>;
  model_ids: Record<string, unknown>;
  prompt_versions: Record<string, unknown>;
  regeneration_count: number;
  created_at: string;
};

/** 結果タブ（使用カード / Creative Trace / Guard）用。権限は生成と同じ。 */
export async function getCreativeTrace(jobId: string): Promise<{
  trace?: CreativeTraceView | null;
  cards?: { card_id: string; card_type: string; title: string; summary: string | null }[];
  error?: string;
}> {
  const auth = await requireUser();
  const supabase = createClient();

  const { data: gen } = await supabase
    .from("creative_generations")
    .select("job_id, created_by")
    .eq("job_id", jobId)
    .maybeSingle();
  if (!gen) return { error: "生成が見つかりません" };
  if (gen.created_by !== auth.user.id && auth.profile.role !== "admin") {
    return { error: "この生成を参照する権限がありません" };
  }

  // worker再起動時の孤児回収で同一ジョブが再実行され得るため最新1件を採る
  // （migration のコメントどおり trace に一意制約は無い）。
  const { data: traces, error } = await supabase
    .from("creative_traces")
    .select(
      "used_card_ids, injected_source_ids, injected_chunk_ids, guard_results, model_ids, prompt_versions, regeneration_count, created_at"
    )
    .eq("job_id", jobId)
    .order("created_at", { ascending: false })
    .limit(1);
  if (error) return { error: error.message };

  const trace = (traces?.[0] as CreativeTraceView | undefined) ?? null;
  if (!trace || trace.used_card_ids.length === 0) return { trace, cards: [] };

  const { data: cards } = await supabase
    .from("creative_cards")
    .select("card_id, card_type, title, summary")
    .in("card_id", trace.used_card_ids);

  return { trace, cards: (cards ?? []) as NonNullable<Awaited<ReturnType<typeof getCreativeTrace>>["cards"]> };
}

/**
 * ワーカーの生存確認(創作画面のポーリング用)。
 *
 * ⚠️ 「行が無い」(= 一度も起動していない)と「取得に失敗した」を区別する。
 * 取得失敗を不在として扱うと誤警告が続き、警告そのものが無視されるようになる。
 */
export async function getWorkerHeartbeat(): Promise<{
  heartbeat?: WorkerHeartbeat | null;
  error?: string;
}> {
  await requireUser();
  const supabase = createClient();
  const { data, error } = await supabase
    .from("worker_heartbeats")
    .select("status, current_job_id, last_seen_at")
    .eq("worker_name", "ingestion")
    .maybeSingle();
  if (error) return { error: error.message };
  return { heartbeat: (data as WorkerHeartbeat) ?? null };
}
