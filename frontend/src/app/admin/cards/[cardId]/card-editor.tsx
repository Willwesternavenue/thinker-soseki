"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { saveCard, setCardStatus, type CardFields } from "../actions";

type Card = {
  card_id: string;
  thought_id: string;
  title: string;
  importance: string;
  status: string;
  version: number;
  core_claim: string | null;
  distinctions: unknown;
  answer_policy: string[];
  prohibitions: string[];
  related_thought_ids: string[];
};

const STATUS_ACTIONS: Array<{
  to: "reviewing" | "approved" | "rejected" | "deprecated" | "draft";
  label: string;
  style: string;
}> = [
  { to: "reviewing", label: "レビュー中にする", style: "border-blue-300 text-blue-800" },
  { to: "approved", label: "承認(approved)", style: "border-green-300 text-green-800" },
  { to: "rejected", label: "却下", style: "border-red-300 text-red-700" },
  { to: "deprecated", label: "廃止", style: "border-amber-300 text-amber-800" },
  { to: "draft", label: "draftに戻す", style: "border-stone-300 text-stone-700" },
];

export function CardEditor({ card }: { card: Card }) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [message, setMessage] = useState<string | null>(null);

  function handleSave(formData: FormData) {
    startTransition(async () => {
      setMessage(null);
      const fields = Object.fromEntries(formData) as unknown as CardFields;
      const result = await saveCard(card.card_id, fields);
      setMessage(result.error ?? "保存しました(v" + (card.version + 1) + ")");
      router.refresh();
    });
  }

  function handleStatus(to: (typeof STATUS_ACTIONS)[number]["to"]) {
    startTransition(async () => {
      setMessage(null);
      const result = await setCardStatus(card.card_id, to);
      setMessage(result.error ?? `ステータスを ${to} に変更しました`);
      router.refresh();
    });
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        {STATUS_ACTIONS.filter((a) => a.to !== card.status).map((action) => (
          <button
            key={action.to}
            disabled={isPending}
            onClick={() => handleStatus(action.to)}
            className={`rounded border px-3 py-1 text-xs hover:bg-stone-100 disabled:opacity-50 ${action.style}`}
          >
            {action.label}
          </button>
        ))}
      </div>

      <form action={handleSave} className="space-y-3">
        <Field label="タイトル">
          <input
            name="title"
            defaultValue={card.title}
            required
            className="w-full rounded border border-stone-300 bg-white px-2 py-1.5 text-sm"
          />
        </Field>
        <Field label="重要度">
          <select
            name="importance"
            defaultValue={card.importance}
            className="rounded border border-stone-300 bg-white px-2 py-1.5 text-sm"
          >
            <option value="core">core</option>
            <option value="important">important</option>
            <option value="normal">normal</option>
          </select>
        </Field>
        <Field label="中核命題(core_claim)">
          <textarea
            name="core_claim"
            defaultValue={card.core_claim ?? ""}
            rows={3}
            className="w-full rounded border border-stone-300 bg-white px-2 py-1.5 text-sm"
          />
        </Field>
        <Field label='区別(distinctions、JSON配列: [{"not": "...", "but": "..."}])'>
          <textarea
            name="distinctions"
            defaultValue={JSON.stringify(card.distinctions, null, 2)}
            rows={4}
            className="w-full rounded border border-stone-300 bg-white px-2 py-1.5 font-mono text-xs"
          />
        </Field>
        <Field label="回答方針(answer_policy、1行1項目)">
          <textarea
            name="answer_policy"
            defaultValue={card.answer_policy.join("\n")}
            rows={4}
            className="w-full rounded border border-stone-300 bg-white px-2 py-1.5 text-sm"
          />
        </Field>
        <Field label="禁止事項(prohibitions、1行1項目)">
          <textarea
            name="prohibitions"
            defaultValue={card.prohibitions.join("\n")}
            rows={4}
            className="w-full rounded border border-stone-300 bg-white px-2 py-1.5 text-sm"
          />
        </Field>
        <Field label="関連思想ID(カンマ区切り)">
          <input
            name="related_thought_ids"
            defaultValue={card.related_thought_ids.join(", ")}
            className="w-full rounded border border-stone-300 bg-white px-2 py-1.5 font-mono text-xs"
          />
        </Field>
        {message && <p className="text-sm text-amber-700">{message}</p>}
        <button
          type="submit"
          disabled={isPending}
          className="rounded bg-blue-700 px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {isPending ? "保存中..." : "保存(履歴を残す)"}
        </button>
      </form>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="mb-1 block text-xs text-stone-600">{label}</label>
      {children}
    </div>
  );
}
