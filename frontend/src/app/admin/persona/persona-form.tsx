"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { savePersona, type PersonaFields } from "./actions";

type Persona = {
  person_id: string;
  display_name: string;
  system_prompt: string;
  first_person: string;
  banned_terms_exact: string[];
  banned_terms_contextual: string[];
  style_rules: { tone?: string; catchphrases_usage?: string };
  quote_policy: { max_quote_length?: number };
  safety_policy: { no_assert?: string[] };
  fallback_card_id: string | null;
};

type CardOption = { card_id: string; title: string };

export function PersonaForm({
  persona,
  approvedCards,
}: {
  persona: Persona;
  approvedCards: CardOption[];
}) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [message, setMessage] = useState<string | null>(null);

  function handleSave(formData: FormData) {
    startTransition(async () => {
      setMessage(null);
      const fields = Object.fromEntries(formData) as unknown as PersonaFields;
      const result = await savePersona(persona.person_id, fields);
      setMessage(result.error ?? "保存しました。次の回答から反映されます。");
      router.refresh();
    });
  }

  return (
    <form action={handleSave} className="space-y-5">
      <section className="space-y-3 rounded-lg border border-stone-200 bg-white p-5">
        <h2 className="font-semibold">人格プロンプト</h2>
        <p className="text-xs text-stone-500">
          すべての回答生成の土台になるシステムプロンプトです。保存すると次の回答からすぐ反映されます。
        </p>
        <Field label="表示名">
          <input
            name="display_name"
            defaultValue={persona.display_name}
            required
            className="w-64 rounded border border-stone-300 bg-white px-2 py-1.5 text-sm"
          />
        </Field>
        <Field label="システムプロンプト *">
          <textarea
            name="system_prompt"
            defaultValue={persona.system_prompt}
            required
            rows={14}
            className="w-full rounded border border-stone-300 bg-white px-3 py-2 font-mono text-xs leading-relaxed"
          />
        </Field>
        <Field label="一人称 *">
          <input
            name="first_person"
            defaultValue={persona.first_person}
            required
            className="w-24 rounded border border-stone-300 bg-white px-2 py-1.5 text-sm"
          />
        </Field>
      </section>

      <section className="space-y-3 rounded-lg border border-stone-200 bg-white p-5">
        <h2 className="font-semibold">語り口</h2>
        <div className="flex gap-4">
          <Field label="トーン">
            <input
              name="tone"
              defaultValue={persona.style_rules?.tone ?? "抑えめ"}
              className="w-40 rounded border border-stone-300 bg-white px-2 py-1.5 text-sm"
            />
          </Field>
          <Field label="口癖の使用">
            <input
              name="catchphrases_usage"
              defaultValue={persona.style_rules?.catchphrases_usage ?? "少しだけ"}
              className="w-40 rounded border border-stone-300 bg-white px-2 py-1.5 text-sm"
            />
          </Field>
        </div>
      </section>

      <section className="space-y-3 rounded-lg border border-stone-200 bg-white p-5">
        <h2 className="font-semibold">禁止語(Output Guard)</h2>
        <p className="text-xs text-stone-500">
          回答を返す前の検査に使われます。完全一致は検出したら即座に再生成、
          文脈依存はAIが「内部の仕組みへの言及か」を判定します。
        </p>
        <div className="grid grid-cols-2 gap-4">
          <Field label="完全一致で禁止(1行1語。例: 社長が)">
            <textarea
              name="banned_terms_exact"
              defaultValue={persona.banned_terms_exact.join("\n")}
              rows={5}
              className="w-full rounded border border-stone-300 bg-white px-2 py-1.5 text-sm"
            />
          </Field>
          <Field label="文脈依存で検査(1行1語。例: 私は)">
            <textarea
              name="banned_terms_contextual"
              defaultValue={persona.banned_terms_contextual.join("\n")}
              rows={5}
              className="w-full rounded border border-stone-300 bg-white px-2 py-1.5 text-sm"
            />
          </Field>
        </div>
      </section>

      <section className="space-y-3 rounded-lg border border-stone-200 bg-white p-5">
        <h2 className="font-semibold">安全方針・引用</h2>
        <Field label="断定しない話題(1行1項目)">
          <textarea
            name="no_assert"
            defaultValue={(persona.safety_policy?.no_assert ?? []).join("\n")}
            rows={4}
            className="w-full rounded border border-stone-300 bg-white px-2 py-1.5 text-sm"
          />
        </Field>
        <div className="flex gap-4">
          <Field label="引用の最大文字数">
            <input
              type="number"
              name="max_quote_length"
              defaultValue={persona.quote_policy?.max_quote_length ?? 100}
              min={20}
              max={500}
              className="w-28 rounded border border-stone-300 bg-white px-2 py-1.5 text-sm"
            />
          </Field>
          <Field label="フォールバックカード(ルーティング全滅時に必ず使う)">
            <select
              name="fallback_card_id"
              defaultValue={persona.fallback_card_id ?? ""}
              required
              className="rounded border border-stone-300 bg-white px-2 py-1.5 text-sm"
            >
              {approvedCards.map((card) => (
                <option key={card.card_id} value={card.card_id}>
                  {card.title}({card.card_id})
                </option>
              ))}
            </select>
          </Field>
        </div>
      </section>

      {message && <p className="text-sm text-amber-700">{message}</p>}
      <button
        type="submit"
        disabled={isPending}
        className="rounded bg-blue-700 px-5 py-2 text-sm font-medium text-white hover:bg-blue-800 disabled:opacity-50"
      >
        {isPending ? "保存中..." : "保存"}
      </button>
    </form>
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
