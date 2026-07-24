"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { saveScores } from "./actions";

const SCORE_KEYS = [
  ["thought_consistency", "思想一貫性"],
  ["persona", "ペルソナ"],
  ["evidence_fit", "根拠適合"],
  ["no_meta_leak", "メタ漏れなし"],
  ["safety", "安全性"],
] as const;

// 理由一致4分類(Regression Suite仕様v0.2 3.2)。B判定がL3規則候補の主要データ源
const REASON_ALIGNMENT_OPTIONS = [
  ["A", "結論も理由も近い"],
  ["B", "結論は近いが理由が違う"],
  ["C", "理由の方向は近いが結論が違う"],
  ["D", "結論も理由も違う"],
] as const;

export function ScoreForm({
  evaluationId,
  scores,
  issues,
  reasonAlignment,
  reasonAlignmentNote,
}: {
  evaluationId: string;
  scores: Record<string, number>;
  issues: string[];
  reasonAlignment: string | null;
  reasonAlignmentNote: string | null;
}) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [message, setMessage] = useState<string | null>(null);

  function handleSave(formData: FormData) {
    startTransition(async () => {
      const result = await saveScores(
        evaluationId,
        {
          thought_consistency: Number(formData.get("thought_consistency")),
          persona: Number(formData.get("persona")),
          evidence_fit: Number(formData.get("evidence_fit")),
          no_meta_leak: Number(formData.get("no_meta_leak")),
          safety: Number(formData.get("safety")),
        },
        formData.get("issues") as string,
        (formData.get("reason_alignment") as "A" | "B" | "C" | "D" | null) ??
          null,
        (formData.get("reason_alignment_note") as string) ?? ""
      );
      setMessage(result.error ?? "保存しました");
      router.refresh();
    });
  }

  return (
    <form action={handleSave} className="space-y-3 text-xs">
      <fieldset className="space-y-1">
        <legend className="text-stone-500">
          理由一致(本人らしさ。Bが規則不足発見の最重要データ)
        </legend>
        <div className="flex flex-wrap gap-3">
          {REASON_ALIGNMENT_OPTIONS.map(([value, label]) => (
            <label
              key={value}
              className="flex cursor-pointer items-center gap-1.5 rounded border border-stone-300 bg-white px-2 py-1.5 has-[:checked]:border-blue-600 has-[:checked]:bg-blue-50"
            >
              <input
                type="radio"
                name="reason_alignment"
                value={value}
                defaultChecked={reasonAlignment === value}
              />
              <span className="font-bold">{value}.</span>
              {label}
            </label>
          ))}
        </div>
      </fieldset>
      <label className="flex flex-col gap-1">
        <span className="text-stone-500">
          理由がどう違うか(B/C判定時に記入。L3規則候補の種になる)
        </span>
        <textarea
          name="reason_alignment_note"
          defaultValue={reasonAlignmentNote ?? ""}
          rows={2}
          placeholder="例: 励ましてはいるが、結果と価値を切り離す論理を通っていない"
          className="rounded border border-stone-300 bg-white px-2 py-1"
        />
      </label>
      <div className="flex flex-wrap items-end gap-3">
        {SCORE_KEYS.map(([key, label]) => (
          <label key={key} className="flex flex-col gap-1">
            <span className="text-stone-500">{label}</span>
            <input
              type="number"
              name={key}
              min={0}
              max={5}
              defaultValue={scores[key] ?? ""}
              className="w-16 rounded border border-stone-300 bg-white px-2 py-1"
            />
          </label>
        ))}
        <label className="flex min-w-64 flex-1 flex-col gap-1">
          <span className="text-stone-500">問題点(1行1項目)</span>
          <textarea
            name="issues"
            defaultValue={issues.join("\n")}
            rows={2}
            className="rounded border border-stone-300 bg-white px-2 py-1"
          />
        </label>
        <button
          disabled={isPending}
          className="rounded bg-blue-700 px-3 py-1.5 font-medium text-white disabled:opacity-50"
        >
          保存
        </button>
        {message && <span className="text-amber-700">{message}</span>}
      </div>
    </form>
  );
}
