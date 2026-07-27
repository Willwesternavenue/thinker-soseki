import { describe, expect, it } from "vitest";
import {
  allowedStatusTransitions,
  buildProfileRow,
  previewDisplayTitle,
  validateProfile,
  type ProfileFormFields,
} from "./profile";

const valid: ProfileFormFields = {
  profile_id: "cp_yume_juya",
  person_id: "natsume_soseki",
  name: "夢十夜",
  slug: "yume-juya",
  description: "『夢十夜』を参照した新作短編を生成するためのプロファイル",
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

function errorsOf(patch: Partial<ProfileFormFields>): string[] {
  const r = validateProfile({ ...valid, ...patch });
  return r.ok ? [] : r.errors;
}

describe("validateProfile(必須項目)", () => {
  it("正しい入力は通る", () => {
    expect(validateProfile(valid)).toEqual({ ok: true });
  });

  it("IDは英数と_-のみ許す", () => {
    expect(errorsOf({ profile_id: "cp 夢十夜" }).join()).toContain("ID");
    expect(errorsOf({ profile_id: "" }).join()).toContain("ID");
  });

  it("slugも英数と_-のみ許す", () => {
    expect(errorsOf({ slug: "夢十夜" }).join()).toContain("slug");
  });

  it("名前は必須", () => {
    expect(errorsOf({ name: " " }).join()).toContain("名前");
  });

  it("正書法は必須(生成文全体に効く決定のため)", () => {
    expect(errorsOf({ orthography_policy: "" }).join()).toContain("正書法");
  });

  it("免責文は必須(本文と同一ビューに常時表示する)", () => {
    expect(errorsOf({ disclosure_text: "" }).join()).toContain("免責");
  });

  it("エラーはまとめて返す", () => {
    expect(errorsOf({ name: "", orthography_policy: "", disclosure_text: "" }).length).toBe(3);
  });
});

describe("validateProfile(表示題名の型)", () => {
  it("{title} を含まない型は許さない(題名が固定文になってしまう)", () => {
    expect(errorsOf({ display_title_format: "第十一夜" }).join()).toContain("{title}");
  });

  it("{title} だけの型は許さない(素の題名がそのまま出るため)", () => {
    const errors = errorsOf({ display_title_format: "{title}" });
    expect(errors.join()).toContain("AI");
  });

  it("未知のプレースホルダは許さない(生成時に落ちるため)", () => {
    expect(errorsOf({ display_title_format: "{name}（AI創作）" }).join()).toContain("{name}");
  });

  it("空の型は許さない", () => {
    expect(errorsOf({ display_title_format: "" }).length).toBeGreaterThan(0);
  });
});

describe("validateProfile(Guard閾値)", () => {
  it("数値でなければ許さない", () => {
    expect(errorsOf({ lcs_threshold: "たくさん" }).join()).toContain("LCS");
  });

  it("重なり率は0〜1の範囲のみ", () => {
    expect(errorsOf({ ngram_overlap_ratio_max: "1.5" }).length).toBeGreaterThan(0);
    expect(errorsOf({ ngram_overlap_ratio_max: "-0.1" }).length).toBeGreaterThan(0);
    expect(errorsOf({ ngram_overlap_ratio_max: "0" })).toEqual([]);
  });

  it("n-gram長とLCS閾値は1以上", () => {
    expect(errorsOf({ ngram_n: "0" }).length).toBeGreaterThan(0);
    expect(errorsOf({ lcs_threshold: "0" }).length).toBeGreaterThan(0);
  });

  it("再生成上限は0以上(0は再生成しない設定として有効)", () => {
    expect(errorsOf({ max_regenerations: "0" })).toEqual([]);
    expect(errorsOf({ max_regenerations: "-1" }).length).toBeGreaterThan(0);
  });
});

describe("buildProfileRow(DB行の組み立て)", () => {
  it("source_scope を改行区切りから作る", () => {
    expect(buildProfileRow(valid).source_scope).toEqual({
      source_ids: ["AOZORA_000799"],
      corpus_roles: ["narrative_reference", "creative_grammar"],
    });
  });

  it("Guard閾値を数値で default_generation_settings に入れる", () => {
    expect(buildProfileRow(valid).default_generation_settings).toMatchObject({
      guard: {
        ngram_n: 10,
        lcs_threshold: 20,
        ngram_overlap_ratio_max: 0.05,
        max_regenerations: 2,
      },
    });
  });

  it("status は含めない(状態変更は別操作にするため)", () => {
    expect(buildProfileRow(valid)).not.toHaveProperty("status");
  });

  it("空欄の任意項目は null にする(空文字を残さない)", () => {
    const row = buildProfileRow({ ...valid, historical_period: "", description: " " });
    expect(row.historical_period).toBeNull();
    expect(row.description).toBeNull();
  });
});

describe("previewDisplayTitle(保存前の見え方)", () => {
  it("題名を差し込んで見せる", () => {
    expect(previewDisplayTitle("{title}（AI創作）")).toBe("鏡（AI創作）");
  });

  it("壊れた型でも例外を投げない", () => {
    expect(previewDisplayTitle("{name}")).toBe("{name}");
    expect(previewDisplayTitle("")).toBe("");
  });
});

describe("allowedStatusTransitions(状態遷移)", () => {
  it("draft からは active にできる", () => {
    expect(allowedStatusTransitions("draft")).toEqual(["active"]);
  });

  it("active からは draft へ戻すか archived にできる", () => {
    expect(allowedStatusTransitions("active")).toEqual(["draft", "archived"]);
  });

  it("archived からは draft へ戻せる(active へ直接は戻さない)", () => {
    expect(allowedStatusTransitions("archived")).toEqual(["draft"]);
  });

  it("未知の状態でも落ちない", () => {
    expect(allowedStatusTransitions("なにか")).toEqual([]);
  });
});
