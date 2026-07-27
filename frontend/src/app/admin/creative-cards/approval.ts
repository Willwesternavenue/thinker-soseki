/**
 * 創作カード承認の判定ロジック(T3b)。
 *
 * server action から切り出した純関数。承認は生成へ直結するため、
 * **根拠チャンクが実在するか**を必ず確かめる(指示書§14.5「evidence切れのカードを検出」)。
 * worker 側の approve_card と同じ規律を frontend でも守る。
 */

export type ApprovalCheck =
  | { ok: true }
  | { ok: false; reason: string };

/**
 * 承認してよいかを判定する。
 *
 * @param evidenceChunkIds カードが根拠として持つ chunk_id
 * @param foundChunkIds     実際にDBに存在した chunk_id
 */
export function checkApprovable(
  evidenceChunkIds: string[] | null | undefined,
  foundChunkIds: string[]
): ApprovalCheck {
  const evidence = evidenceChunkIds ?? [];
  if (evidence.length === 0) {
    return { ok: false, reason: "根拠チャンクが無いカードは承認できません" };
  }

  const found = new Set(foundChunkIds);
  const missing = evidence.filter((id) => !found.has(id));
  if (missing.length > 0) {
    return {
      ok: false,
      reason: `根拠チャンクが実在しないため承認できません: ${missing.join(", ")}`,
    };
  }
  return { ok: true };
}

/** 創作カードの根拠が「漱石自身の創作論」か「小説本文での実演」か(指示書§11.2)。 */
export const EVIDENCE_TYPE_LABELS: Record<string, string> = {
  author_creative_theory: "創作論（本人が直接論じたもの）",
  demonstrated_in_fiction: "小説本文での実演",
  critic_interpretation: "批評家の解釈",
};

export function evidenceTypeLabel(evidenceType: string | null): string {
  if (!evidenceType) return "（未分類）";
  return EVIDENCE_TYPE_LABELS[evidenceType] ?? evidenceType;
}
