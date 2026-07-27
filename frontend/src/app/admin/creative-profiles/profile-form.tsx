"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { changeProfileStatus, saveCreativeProfile } from "./actions";
import {
  STATUS_LABELS,
  allowedStatusTransitions,
  previewDisplayTitle,
  type ProfileFormFields,
} from "./profile";

const EMPTY: ProfileFormFields = {
  profile_id: "",
  person_id: "",
  name: "",
  slug: "",
  description: "",
  orthography_policy: "",
  target_language: "ja",
  historical_period: "",
  disclosure_text: "",
  display_title_format: "{title}（AI創作）",
  copyright_policy: "",
  source_ids: "",
  corpus_roles: "",
  ngram_n: "10",
  lcs_threshold: "20",
  ngram_overlap_ratio_max: "0.05",
  max_regenerations: "2",
};

export function ProfileForm({
  initial,
  mode,
  status,
  personIds,
  approvedCards,
}: {
  initial?: Partial<ProfileFormFields>;
  mode: "create" | "update";
  status?: string;
  personIds: string[];
  approvedCards?: number;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [fields, setFields] = useState<ProfileFormFields>({
    ...EMPTY,
    person_id: personIds[0] ?? "",
    ...initial,
  });
  const [errors, setErrors] = useState<string[]>([]);
  const [message, setMessage] = useState<string | null>(null);

  const set = (key: keyof ProfileFormFields) => (value: string) =>
    setFields((f) => ({ ...f, [key]: value }));

  function handleSave() {
    setErrors([]);
    setMessage(null);
    startTransition(async () => {
      const result = await saveCreativeProfile(fields, mode);
      if (result.errors) return setErrors(result.errors);
      if (result.error) return setErrors([result.error]);
      setMessage("保存しました");
      if (mode === "create") router.push(`/admin/creative-profiles/${result.profileId}`);
      else router.refresh();
    });
  }

  function handleStatus(next: string) {
    setErrors([]);
    setMessage(null);
    startTransition(async () => {
      const result = await changeProfileStatus(fields.profile_id, next);
      if (result.error) return setErrors([result.error]);
      setMessage(`状態を ${next} にしました`);
      router.refresh();
    });
  }

  return (
    <div className="max-w-3xl space-y-6">
      <Section title="基本">
        <Field label="ID" required hint="英数字と _ - のみ。作成後は変更できません">
          <Text
            value={fields.profile_id}
            onChange={set("profile_id")}
            disabled={mode === "update"}
            placeholder="cp_yume_juya"
          />
        </Field>
        <Field label="人物" required>
          <select
            value={fields.person_id}
            onChange={(e) => set("person_id")(e.target.value)}
            className="w-full rounded border border-stone-300 p-2 text-sm"
          >
            {personIds.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
        </Field>
        <Field label="名前" required hint="管理画面と創作画面に出ます">
          <Text value={fields.name} onChange={set("name")} placeholder="夢十夜" />
        </Field>
        <Field label="slug" required hint="英数字と _ - のみ">
          <Text value={fields.slug} onChange={set("slug")} placeholder="yume-juya" />
        </Field>
        <Field label="説明">
          <Area value={fields.description} onChange={set("description")} rows={2} />
        </Field>
      </Section>

      <Section
        title="誤認防止"
        note="この3つが生成物を真作と誤認させないための仕組みです。空のまま運用しないでください。"
      >
        <Field
          label="正書法"
          required
          hint="生成文全体の表記。原典に合わせます（例: 新字新仮名）"
        >
          <Text
            value={fields.orthography_policy}
            onChange={set("orthography_policy")}
            placeholder="新字新仮名"
          />
        </Field>
        <Field
          label="免責文"
          required
          hint="作品本文と同じ画面に常に表示されます"
        >
          <Area value={fields.disclosure_text} onChange={set("disclosure_text")} rows={3} />
        </Field>
        <Field
          label="表示題名の型"
          required
          hint="{title} を含め、AI創作と分かる語を必ず添えます"
        >
          <Text
            value={fields.display_title_format}
            onChange={set("display_title_format")}
            placeholder="{title}（AI創作）"
          />
          <p className="mt-1 text-xs text-stone-500">
            表示例: <strong>{previewDisplayTitle(fields.display_title_format)}</strong>
          </p>
        </Field>
      </Section>

      <Section title="参照する原典" note="source_scope。生成時にここから原典を引きます。">
        <Field label="source_id" hint="1行に1つ">
          <Area value={fields.source_ids} onChange={set("source_ids")} rows={2} />
        </Field>
        <Field label="corpus_role" hint="1行に1つ（例: narrative_reference）">
          <Area value={fields.corpus_roles} onChange={set("corpus_roles")} rows={2} />
        </Field>
      </Section>

      <Section
        title="Guard 閾値"
        note="原典との類似を判定する閾値。コードではなくここの値が使われます。"
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="LCS閾値" hint="連続一致がこの文字数以上なら違反">
            <Text value={fields.lcs_threshold} onChange={set("lcs_threshold")} />
          </Field>
          <Field label="n-gram長">
            <Text value={fields.ngram_n} onChange={set("ngram_n")} />
          </Field>
          <Field label="n-gram重なり率の上限" hint="0〜1">
            <Text
              value={fields.ngram_overlap_ratio_max}
              onChange={set("ngram_overlap_ratio_max")}
            />
          </Field>
          <Field label="再生成の上限" hint="超えたら安全側で失敗させます">
            <Text value={fields.max_regenerations} onChange={set("max_regenerations")} />
          </Field>
        </div>
      </Section>

      <Section title="その他">
        <Field label="時代設定">
          <Text value={fields.historical_period} onChange={set("historical_period")} />
        </Field>
        <Field label="言語">
          <Text value={fields.target_language} onChange={set("target_language")} />
        </Field>
        <Field label="著作権メモ">
          <Area value={fields.copyright_policy} onChange={set("copyright_policy")} rows={2} />
        </Field>
      </Section>

      {errors.length > 0 && (
        <ul className="space-y-1 rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          {errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
      {message && (
        <p className="rounded border border-green-300 bg-green-50 p-3 text-sm text-green-800">
          {message}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2 border-t border-stone-200 pt-4">
        <button
          type="button"
          onClick={handleSave}
          disabled={pending}
          className="rounded bg-stone-800 px-5 py-2 text-sm text-white disabled:bg-stone-300"
        >
          {mode === "create" ? "作成する" : "保存する"}
        </button>
        {mode === "update" && status && (
          <>
            <span className="ml-2 text-sm text-stone-500">
              現在: {STATUS_LABELS[status] ?? status}
              {approvedCards !== undefined && ` / 承認済みカード ${approvedCards} 枚`}
            </span>
            {allowedStatusTransitions(status).map((next) => (
              <button
                key={next}
                type="button"
                onClick={() => handleStatus(next)}
                disabled={pending}
                className="rounded border border-stone-400 px-3 py-1.5 text-sm disabled:opacity-50"
              >
                {next} にする
              </button>
            ))}
          </>
        )}
      </div>
    </div>
  );
}

function Section({
  title,
  note,
  children,
}: {
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3 rounded border border-stone-200 bg-white p-4">
      <h2 className="font-bold">{title}</h2>
      {note && <p className="text-xs text-stone-600">{note}</p>}
      {children}
    </section>
  );
}

function Field({
  label,
  required,
  hint,
  children,
}: {
  label: string;
  required?: boolean;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="mb-1 block text-sm font-medium">
        {label}
        {required && <span className="ml-1 text-red-600">*</span>}
        {hint && <span className="ml-2 text-xs font-normal text-stone-500">{hint}</span>}
      </label>
      {children}
    </div>
  );
}

function Text({
  value,
  onChange,
  placeholder,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  disabled?: boolean;
}) {
  return (
    <input
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      disabled={disabled}
      className="w-full rounded border border-stone-300 p-2 text-sm disabled:bg-stone-100"
    />
  );
}

function Area({
  value,
  onChange,
  rows,
}: {
  value: string;
  onChange: (v: string) => void;
  rows: number;
}) {
  return (
    <textarea
      value={value}
      onChange={(e) => onChange(e.target.value)}
      rows={rows}
      className="w-full rounded border border-stone-300 p-2 text-sm"
    />
  );
}
