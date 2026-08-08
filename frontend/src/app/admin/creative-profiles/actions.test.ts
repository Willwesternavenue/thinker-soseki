import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ProfileFormFields } from "./profile";

/**
 * 保存が「フォームに無い設定」を壊さないことを固定する。
 *
 * 2026-08-03 の事故: 管理画面で Guard 閾値を直しただけで
 * `default_generation_settings.rules` が assist → off に戻り、ブリッジ6件が
 * 黙って切れた。`update` は JSON を丸ごと置き換えるため、保存側が既存値を
 * 読んで渡さないと入力欄の無い設定が消える。エラーは出ない。
 */

const h = vi.hoisted(() => ({
  updates: [] as Record<string, unknown>[],
  existing: {
    use_rag: true,
    use_cards: true,
    rules: "assist",
    device_exclusion: "on",
    preset_name: "cards_only",
    guard: {
      ngram_n: 1,
      lcs_threshold: 1,
      ngram_overlap_ratio_max: 1,
      max_regenerations: 1,
    },
  } as Record<string, unknown>,
}));

vi.mock("next/cache", () => ({ revalidatePath: () => {} }));
vi.mock("@/lib/auth", () => ({ requireAdmin: async () => {} }));
vi.mock("@/lib/supabase/server", () => ({
  createClient: () => ({
    from(table: string) {
      return {
        select() {
          return this;
        },
        eq() {
          return this;
        },
        maybeSingle: async () =>
          table === "personas"
            ? { data: { person_id: "natsume_soseki" } }
            : { data: { default_generation_settings: h.existing } },
        insert: async (row: Record<string, unknown>) => {
          h.updates.push(row);
          return { error: null };
        },
        update(row: Record<string, unknown>) {
          h.updates.push(row);
          return { eq: async () => ({ error: null }) };
        },
      };
    },
  }),
}));

const { saveCreativeProfile } = await import("./actions");

const valid: ProfileFormFields = {
  profile_id: "cp_yume_juya",
  person_id: "natsume_soseki",
  name: "夢十夜",
  slug: "yume-juya",
  description: "説明",
  orthography_policy: "新字新仮名",
  target_language: "ja",
  historical_period: "明治",
  disclosure_text: "本文はAIが生成した創作物であり、原作者本人の作品ではありません。",
  display_title_format: "{title}（AI創作）",
  copyright_policy: "原典はパブリックドメイン",
  source_ids: "AOZORA_000799",
  corpus_roles: "narrative_reference\ncreative_grammar",
  ngram_n: "10",
  lcs_threshold: "20",
  ngram_overlap_ratio_max: "0.05",
  max_regenerations: "2",
};

const settingsOf = (i = 0) =>
  h.updates[i].default_generation_settings as Record<string, unknown>;

describe("saveCreativeProfile(既存設定の引き継ぎ)", () => {
  beforeEach(() => {
    h.updates.length = 0;
  });

  it("更新時、フォームに入力欄の無い設定(rules・device_exclusion)を既存から引き継ぐ", async () => {
    await saveCreativeProfile(valid, "update");
    expect(settingsOf()).toMatchObject({ rules: "assist", device_exclusion: "on" });
  });

  it("更新時、Guard閾値はフォームの値で上書きする(このフォームが所有するため)", async () => {
    await saveCreativeProfile(valid, "update");
    expect(settingsOf().guard).toEqual({
      ngram_n: 10,
      lcs_threshold: 20,
      ngram_overlap_ratio_max: 0.05,
      max_regenerations: 2,
    });
  });

  it("新規作成時は既存を読まず既定値で作る", async () => {
    await saveCreativeProfile(valid, "create");
    expect(settingsOf()).toMatchObject({ rules: "off" });
    expect(settingsOf()).not.toHaveProperty("device_exclusion");
  });
});
