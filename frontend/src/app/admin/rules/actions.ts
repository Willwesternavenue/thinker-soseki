"use server";

import { revalidatePath } from "next/cache";
import { createClient } from "@/lib/supabase/server";
import { requireAdmin } from "@/lib/auth";

export type ReviewFields = {
  reviewerRole: string;
  verdict: string;
  reviewScope: string;
  note: string;
};

/** スコープ別レビューの記録(仕様: judgment_rules_spec_v0_2 3.8)。 */
export async function saveReview(
  ruleVersionId: string,
  fields: ReviewFields
): Promise<{ error?: string }> {
  const auth = await requireAdmin();
  const supabase = await createClient();
  const { error } = await supabase.from("judgment_rule_reviews").insert({
    rule_version_id: ruleVersionId,
    reviewer_id: auth.user.email ?? auth.user.id,
    reviewer_role: fields.reviewerRole,
    verdict: fields.verdict,
    review_scope: fields.reviewScope,
    note: fields.note.trim() || null,
  });
  if (error) return { error: error.message };
  revalidatePath("/admin/rules");
  return {};
}

/**
 * バージョンのstatus変更。承認済みバージョンのcontentは書き換えない原則のため、
 * 変更できるのはstatusのみ(contentの修正は新バージョン作成で行う。MVPでは
 * import_judgment_rules.pyの再投入)。
 */
export async function setVersionStatus(
  ruleVersionId: string,
  status: string
): Promise<{ error?: string }> {
  const allowed = ["draft", "reviewing", "approved", "rejected", "deprecated"];
  if (!allowed.includes(status)) return { error: "不正なstatus" };
  await requireAdmin();
  const supabase = await createClient();
  const { error } = await supabase
    .from("judgment_rule_versions")
    .update({ status })
    .eq("rule_version_id", ruleVersionId);
  if (error) return { error: error.message };
  revalidatePath("/admin/rules");
  return {};
}
