/**
 * 創作モードUI(T5)の純粋ロジック。
 *
 * server action / client component の両方から使うため副作用を持たない。
 * worker 側(`worker/src/creative/`)の契約に合わせる箇所は各所にコメントで示す。
 */

export type BriefFormInput = {
  profileId: string;
  motif: string;
  situation: string;
  emotionalTarget: string;
  period: string;
  /** フォーム値なので文字列。検証を通ってから数値化する。 */
  length: string;
  /** 改行区切りの自由記述。 */
  constraints: string;
};

/** worker の `normalize_brief` が読むキー名で渡す（prompts.py の JSON 契約）。 */
export type BriefRaw = {
  motif?: string;
  situation?: string;
  emotional_target?: string;
  period?: string;
  length?: number;
  constraints?: string[];
};

export type ValidationResult =
  | { ok: true }
  | { ok: false; errors: string[] };

/** 短すぎ・長すぎる指定は生成が破綻するため入口で弾く。 */
export const LENGTH_MIN = 200;
export const LENGTH_MAX = 20000;

/**
 * 入力検証。エラーは**まとめて**返す（1つ直すたびに再送させないため）。
 *
 * プロファイルの `status='active'` 検証は server action 側で行う。
 * ここで active 判定を持つと、画面の選択肢が古いときに誤って通してしまう。
 */
export function validateBrief(input: BriefFormInput): ValidationResult {
  const errors: string[] = [];

  if (!input.profileId.trim()) {
    errors.push("プロファイルを選んでください");
  }
  if (!input.motif.trim()) {
    errors.push("モチーフを入力してください");
  }

  const length = input.length.trim();
  if (length) {
    const n = Number(length);
    if (!Number.isFinite(n) || !Number.isInteger(n)) {
      errors.push("文字数は数値で入力してください");
    } else if (n < LENGTH_MIN || n > LENGTH_MAX) {
      errors.push(`文字数は${LENGTH_MIN}〜${LENGTH_MAX}の範囲で入力してください`);
    }
  }

  return errors.length > 0 ? { ok: false, errors } : { ok: true };
}

/**
 * `creative_generations.brief_raw` に入れる値を組み立てる。
 *
 * 空欄のキーは**入れない**。worker 側は欠けたキーを「(指定なし)」として扱うので、
 * 空文字を送るとプロンプトに空欄が混ざる。
 */
export function buildBriefRaw(input: BriefFormInput): BriefRaw {
  const brief: BriefRaw = {};
  const put = (key: "motif" | "situation" | "emotional_target" | "period", v: string) => {
    if (v.trim()) brief[key] = v.trim();
  };

  put("motif", input.motif);
  put("situation", input.situation);
  put("emotional_target", input.emotionalTarget);
  put("period", input.period);

  const length = input.length.trim();
  if (length) brief.length = Number(length);

  const constraints = input.constraints
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (constraints.length > 0) brief.constraints = constraints;

  return brief;
}

/**
 * 進捗表示のステップ。**worker の実行順**に並べる。
 *
 * 仕様§6.2 の記載順は brief が先だが、実装は失敗が確定しているジョブで LLM を
 * 呼ばないよう profile/cards の検証を先に済ませる（generate.py `prepare_generation`）。
 * ここを仕様の順に並べると進捗が行き来して見えるため実装順に合わせる。
 */
export const STEPS = [
  { key: "profile", label: "プロファイル確認" },
  { key: "cards", label: "承認済みカード取得" },
  { key: "brief", label: "依頼内容の整理" },
  { key: "sources", label: "原典の読み込み" },
  { key: "outline", label: "構成づくり" },
  { key: "draft", label: "本文執筆" },
  { key: "guard", label: "類似・誤認チェック" },
  { key: "save", label: "保存" },
] as const;

export type StepProgress = {
  /** 1始まり。未開始・未知は 0。 */
  index: number;
  total: number;
  label: string;
};

/** current_step を進捗表示に変換する。未知の値でも落ちない。 */
export function stepProgress(step: string | null | undefined): StepProgress {
  const i = STEPS.findIndex((s) => s.key === step);
  return {
    index: i < 0 ? 0 : i + 1,
    total: STEPS.length,
    label: i < 0 ? "準備中" : STEPS[i].label,
  };
}

/** 終端(succeeded/failed)に達するまでポーリングを続ける。 */
export function shouldKeepPolling(status: string): boolean {
  return status === "pending" || status === "running";
}

/**
 * `idempotency_key` の一意制約違反かどうか。
 *
 * 二重クリック・リトライで同じ key を送った場合に既存ジョブへ合流させるための判定
 * （仕様§13）。他の制約違反まで冪等扱いすると別種の不具合を握り潰すので code で見る。
 */
export function isDuplicateKeyError(error: { code?: string } | null | undefined): boolean {
  return error?.code === "23505";
}

export type FailureMessage = { title: string; hint: string; detail: string };

/**
 * `error_message` 先頭の分類タグ（repo.py の ERROR_* ）をユーザー向け文言に変換する。
 * 原文は必ず `detail` に残す（管理者が原因を追えるように）。
 */
export function failureMessage(errorMessage: string | null | undefined): FailureMessage {
  const detail = errorMessage ?? "";
  if (detail.startsWith("invariant_violation")) {
    return {
      title: "承認済みの創作カードが足りないため生成できません",
      hint: "管理画面の「創作カード」でカードを承認してから、もう一度お試しください。",
      detail,
    };
  }
  if (detail.startsWith("guard_exhausted")) {
    return {
      title: "原典に似すぎたため、安全側で中止しました",
      hint: "モチーフや状況を原典から少し離すと通りやすくなります。",
      detail,
    };
  }
  if (detail.startsWith("llm_error")) {
    return {
      title: "生成中にエラーが起きました",
      hint: "一時的な失敗の可能性があります。もう一度お試しください。",
      detail,
    };
  }
  return {
    title: "生成に失敗しました",
    hint: "解消しない場合は管理者にお問い合わせください。",
    detail,
  };
}

/**
 * 本文を画面に出してよいかの判定（仕様§5.1 の誤認防止）。
 *
 * 本文・表示題名・免責文が**揃っているときだけ**表示する。素の題名や
 * 免責なしの本文を出さないための不変条件をここに閉じ込める。
 */
export function canRenderWork(
  generation: { status: string; final_text: string | null; display_title: string | null },
  disclosureText: string | null | undefined
): boolean {
  return (
    generation.status === "succeeded" &&
    !!generation.final_text?.trim() &&
    !!generation.display_title?.trim() &&
    !!disclosureText?.trim()
  );
}
