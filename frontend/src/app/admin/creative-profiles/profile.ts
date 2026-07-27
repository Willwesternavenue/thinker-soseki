/**
 * 創作プロファイル管理(T3a)の純粋ロジック。
 *
 * プロファイルは生成の前提条件（正書法・免責文・表示題名・Guard閾値）をすべて持つ。
 * ここが空のまま active になると、誤認防止の仕組みが丸ごと効かなくなるため、
 * 保存時の検証をこのファイルに集約してテストで固定する。
 */

export type ProfileFormFields = {
  profile_id: string;
  person_id: string;
  name: string;
  slug: string;
  description: string;
  orthography_policy: string;
  target_language: string;
  historical_period: string;
  disclosure_text: string;
  display_title_format: string;
  copyright_policy: string;
  /** 改行区切り */
  source_ids: string;
  /** 改行区切り */
  corpus_roles: string;
  ngram_n: string;
  lcs_threshold: string;
  ngram_overlap_ratio_max: string;
  max_regenerations: string;
};

export type ValidationResult = { ok: true } | { ok: false; errors: string[] };

const ID_PATTERN = /^[A-Za-z0-9_-]+$/;
/** worker の `build_display_title` は `.format(title=...)` を使うので置換子は title だけ。 */
const TITLE_PLACEHOLDER = "{title}";
const PLACEHOLDER_PATTERN = /\{([^}]*)\}/g;

export function validateProfile(fields: ProfileFormFields): ValidationResult {
  const errors: string[] = [];
  const required = (value: string, label: string) => {
    if (!value.trim()) errors.push(`${label}は必須です`);
  };

  if (!ID_PATTERN.test(fields.profile_id.trim())) {
    errors.push("IDは英数字と _ - のみで入力してください");
  }
  if (!ID_PATTERN.test(fields.slug.trim())) {
    errors.push("slugは英数字と _ - のみで入力してください");
  }
  required(fields.person_id, "人物");
  required(fields.name, "名前");
  // 生成文全体の表記を決めるため必須（仕様§4・migration の not null）
  required(fields.orthography_policy, "正書法");
  // 本文と同一ビューに常時表示する（仕様§5.1）
  required(fields.disclosure_text, "免責文");

  errors.push(...validateDisplayTitleFormat(fields.display_title_format));
  errors.push(...validateGuard(fields));

  return errors.length > 0 ? { ok: false, errors } : { ok: true };
}

/**
 * 表示題名の型を検証する。ここが誤認防止の最後の砦になる（仕様§5.1）。
 *
 * - `{title}` が無い → どの作品も同じ固定文になる
 * - `{title}` だけ → 素の題名がそのまま出て真作と見分けが付かない
 * - 未知の置換子 → worker の `.format()` が KeyError で落ち、生成が失敗する
 */
function validateDisplayTitleFormat(format: string): string[] {
  const value = format.trim();
  if (!value) return ["表示題名の型は必須です"];

  const errors: string[] = [];
  if (!value.includes(TITLE_PLACEHOLDER)) {
    errors.push("表示題名の型には {title} を含めてください");
  } else if (value === TITLE_PLACEHOLDER) {
    errors.push(
      "表示題名の型が {title} だけです。AI創作と分かる語を必ず添えてください"
    );
  }

  for (const match of value.matchAll(PLACEHOLDER_PATTERN)) {
    if (match[1] !== "title") {
      errors.push(`表示題名の型に使えない置換子があります: {${match[1]}}`);
    }
  }
  return errors;
}

function validateGuard(fields: ProfileFormFields): string[] {
  const errors: string[] = [];
  const num = (raw: string, label: string, min: number, max?: number) => {
    const n = Number(raw.trim());
    if (raw.trim() === "" || !Number.isFinite(n)) {
      errors.push(`${label}は数値で入力してください`);
      return;
    }
    if (n < min || (max !== undefined && n > max)) {
      errors.push(
        max === undefined
          ? `${label}は${min}以上で入力してください`
          : `${label}は${min}〜${max}の範囲で入力してください`
      );
    }
  };

  num(fields.ngram_n, "n-gram長", 1);
  num(fields.lcs_threshold, "LCS閾値", 1);
  num(fields.ngram_overlap_ratio_max, "n-gram重なり率の上限", 0, 1);
  num(fields.max_regenerations, "再生成の上限", 0);
  return errors;
}

export type ProfileRow = {
  profile_id: string;
  person_id: string;
  name: string;
  slug: string;
  description: string | null;
  orthography_policy: string;
  target_language: string;
  historical_period: string | null;
  disclosure_text: string;
  display_title_format: string;
  copyright_policy: string | null;
  source_scope: { source_ids: string[]; corpus_roles: string[] };
  default_generation_settings: Record<string, unknown>;
};

/**
 * DB に入れる行を組み立てる。**status は含めない** —
 * 状態変更（draft→active→archived）は編集とは別の操作にして、
 * 保存のついでに公開されることを防ぐ。
 */
export function buildProfileRow(fields: ProfileFormFields): ProfileRow {
  const lines = (s: string) => s.split("\n").map((l) => l.trim()).filter(Boolean);
  const optional = (s: string) => (s.trim() ? s.trim() : null);

  return {
    profile_id: fields.profile_id.trim(),
    person_id: fields.person_id.trim(),
    name: fields.name.trim(),
    slug: fields.slug.trim(),
    description: optional(fields.description),
    orthography_policy: fields.orthography_policy.trim(),
    target_language: fields.target_language.trim() || "ja",
    historical_period: optional(fields.historical_period),
    disclosure_text: fields.disclosure_text.trim(),
    display_title_format: fields.display_title_format.trim(),
    copyright_policy: optional(fields.copyright_policy),
    source_scope: {
      source_ids: lines(fields.source_ids),
      corpus_roles: lines(fields.corpus_roles),
    },
    // 閾値はコードに直書きせずここに持たせる（仕様§8.1）
    default_generation_settings: {
      use_rag: true,
      use_cards: true,
      rules: "off",
      preset_name: "cards_only",
      guard: {
        ngram_n: Number(fields.ngram_n),
        lcs_threshold: Number(fields.lcs_threshold),
        ngram_overlap_ratio_max: Number(fields.ngram_overlap_ratio_max),
        max_regenerations: Number(fields.max_regenerations),
      },
    },
  };
}

/** 保存前に「実際にどう表示されるか」を見せるための組み立て。 */
export function previewDisplayTitle(format: string, sampleTitle = "鏡"): string {
  return format.replaceAll(TITLE_PLACEHOLDER, sampleTitle);
}

const TRANSITIONS: Record<string, string[]> = {
  draft: ["active"],
  active: ["draft", "archived"],
  // archived から直接 active へは戻さない。内容を見直してから公開させるため。
  archived: ["draft"],
};

export function allowedStatusTransitions(current: string): string[] {
  return TRANSITIONS[current] ?? [];
}

export const STATUS_LABELS: Record<string, string> = {
  draft: "下書き（生成に使われない）",
  active: "運用中（生成に使われる）",
  archived: "停止",
};
