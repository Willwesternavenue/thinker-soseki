import Link from "next/link";
import { notFound } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { ProfileForm } from "../profile-form";
import type { ProfileFormFields } from "../profile";

export const dynamic = "force-dynamic";

export default async function CreativeProfilePage({
  params,
}: {
  params: Promise<{ profileId: string }>;
}) {
  const { profileId } = await params;
  const supabase = createClient();

  const { data: profile } = await supabase
    .from("creative_profiles")
    .select("*")
    .eq("profile_id", profileId)
    .maybeSingle();
  if (!profile) notFound();

  const [{ data: personas }, { count: approved }] = await Promise.all([
    supabase.from("personas").select("person_id"),
    supabase
      .from("creative_cards")
      .select("card_id", { count: "exact", head: true })
      .eq("profile_id", profileId)
      .eq("status", "approved"),
  ]);

  const scope = (profile.source_scope ?? {}) as {
    source_ids?: string[];
    corpus_roles?: string[];
  };
  const guard = ((profile.default_generation_settings ?? {}) as { guard?: Record<string, number> })
    .guard ?? {};

  const initial: ProfileFormFields = {
    profile_id: profile.profile_id as string,
    person_id: profile.person_id as string,
    name: profile.name as string,
    slug: profile.slug as string,
    description: (profile.description as string) ?? "",
    orthography_policy: profile.orthography_policy as string,
    target_language: (profile.target_language as string) ?? "ja",
    historical_period: (profile.historical_period as string) ?? "",
    disclosure_text: profile.disclosure_text as string,
    display_title_format: profile.display_title_format as string,
    copyright_policy: (profile.copyright_policy as string) ?? "",
    source_ids: (scope.source_ids ?? []).join("\n"),
    corpus_roles: (scope.corpus_roles ?? []).join("\n"),
    ngram_n: String(guard.ngram_n ?? 10),
    lcs_threshold: String(guard.lcs_threshold ?? 20),
    ngram_overlap_ratio_max: String(guard.ngram_overlap_ratio_max ?? 0.05),
    max_regenerations: String(guard.max_regenerations ?? 2),
  };

  return (
    <div className="space-y-4">
      <Link href="/admin/creative-profiles" className="text-sm text-blue-700 underline">
        ← 創作プロファイル一覧
      </Link>
      <h1 className="text-xl font-bold">{profile.name as string}</h1>
      <ProfileForm
        mode="update"
        initial={initial}
        status={profile.status as string}
        approvedCards={approved ?? 0}
        personIds={(personas ?? []).map((p) => p.person_id as string)}
      />
    </div>
  );
}
