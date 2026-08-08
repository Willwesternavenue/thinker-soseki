"use server";

import { revalidatePath } from "next/cache";
import { createClient } from "@/lib/supabase/server";
import { requireAdmin } from "@/lib/auth";
import {
  allowedStatusTransitions,
  buildProfileRow,
  validateProfile,
  type ProfileFormFields,
} from "./profile";

/**
 * プロファイルの作成・更新(T3a)。
 *
 * status は触らない。編集の副作用で公開されないよう、状態変更は
 * `changeProfileStatus` に分けている。
 */
export async function saveCreativeProfile(
  fields: ProfileFormFields,
  mode: "create" | "update"
): Promise<{ error?: string; errors?: string[]; profileId?: string }> {
  await requireAdmin();

  const validation = validateProfile(fields);
  if (!validation.ok) return { errors: validation.errors };

  const supabase = createClient();
  const profileId = fields.profile_id.trim();
  const personId = fields.person_id.trim();

  // 人物が実在することを確かめる（FK違反をDBエラーとして見せない）
  const { data: person } = await supabase
    .from("personas")
    .select("person_id")
    .eq("person_id", personId)
    .maybeSingle();
  if (!person) return { error: `人物 ${personId} が存在しません` };

  // 更新時は既存の設定を読んでから組み立てる。
  // `update` は default_generation_settings を丸ごと置き換えるので、ここで
  // 読まないと**フォームに入力欄の無い設定が保存のたびに消える**
  // （2026-08-03: Guard 閾値を直しただけで rules が assist → off に戻った）。
  let existingSettings: Record<string, unknown> | undefined;
  if (mode === "update") {
    const { data: existing } = await supabase
      .from("creative_profiles")
      .select("default_generation_settings")
      .eq("profile_id", profileId)
      .maybeSingle();
    existingSettings =
      (existing?.default_generation_settings as Record<string, unknown> | null) ??
      undefined;
  }

  const row = buildProfileRow(fields, existingSettings);

  if (mode === "create") {
    const { error } = await supabase.from("creative_profiles").insert(row);
    if (error) {
      return {
        error:
          error.code === "23505"
            ? "同じID または slug のプロファイルが既にあります"
            : error.message,
      };
    }
  } else {
    const { error } = await supabase
      .from("creative_profiles")
      .update(row)
      .eq("profile_id", row.profile_id);
    if (error) return { error: error.message };
  }

  revalidatePath("/admin/creative-profiles");
  revalidatePath(`/admin/creative-profiles/${row.profile_id}`);
  return { profileId: row.profile_id };
}

/**
 * 状態変更。active にするときだけ**承認済みカードの有無**を確かめる。
 *
 * カード0枚でも生成側で invariant_violation として失敗するが、
 * それはユーザーが待たされた末に失敗すること。管理者が公開する時点で止める。
 */
export async function changeProfileStatus(
  profileId: string,
  next: string
): Promise<{ error?: string }> {
  await requireAdmin();
  const supabase = createClient();

  const { data: profile } = await supabase
    .from("creative_profiles")
    .select("profile_id, status")
    .eq("profile_id", profileId)
    .maybeSingle();
  if (!profile) return { error: "プロファイルが見つかりません" };

  if (!allowedStatusTransitions(profile.status as string).includes(next)) {
    return { error: `${profile.status} から ${next} へは変更できません` };
  }

  if (next === "active") {
    const { count } = await supabase
      .from("creative_cards")
      .select("card_id", { count: "exact", head: true })
      .eq("profile_id", profileId)
      .eq("status", "approved");
    if (!count) {
      return {
        error:
          "承認済みの創作カードが0枚です。カードを承認してから運用中にしてください",
      };
    }
  }

  const { error } = await supabase
    .from("creative_profiles")
    .update({ status: next })
    .eq("profile_id", profileId);
  if (error) return { error: error.message };

  revalidatePath("/admin/creative-profiles");
  revalidatePath(`/admin/creative-profiles/${profileId}`);
  return {};
}
