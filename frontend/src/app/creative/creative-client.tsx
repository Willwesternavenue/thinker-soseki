"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { LogoutButton } from "@/components/logout-button";
import {
  createCreativeGeneration,
  getCreativeGeneration,
  getCreativeTrace,
  type CreativeTraceView,
  type GenerationState,
  type ProfileOption,
} from "./actions";
import {
  STEPS,
  canRenderWork,
  failureMessage,
  shouldKeepPolling,
  stepProgress,
  validateBrief,
  type BriefFormInput,
} from "./creative";

const POLL_MS = 3000;

const EMPTY_FORM: BriefFormInput = {
  profileId: "",
  motif: "",
  situation: "",
  emotionalTarget: "",
  period: "",
  length: "",
  constraints: "",
};

type UsedCard = { card_id: string; card_type: string; title: string; summary: string | null };
type Tab = "work" | "outline" | "cards" | "trace" | "guard";

export function CreativeClient({
  profiles,
  isAdmin,
}: {
  profiles: ProfileOption[];
  isAdmin: boolean;
  canStartWorker: boolean;
}) {
  const [form, setForm] = useState<BriefFormInput>({
    ...EMPTY_FORM,
    profileId: profiles[0]?.profile_id ?? "",
  });
  const [errors, setErrors] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [generation, setGeneration] = useState<GenerationState | null>(null);
  const [disclosure, setDisclosure] = useState("");
  const [trace, setTrace] = useState<CreativeTraceView | null>(null);
  const [usedCards, setUsedCards] = useState<UsedCard[]>([]);
  const [tab, setTab] = useState<Tab>("work");
  // 二重送信でジョブが増えないよう、同じ入力に対しては同じキーを使い回す
  const idempotencyKeyRef = useRef<string | null>(null);

  const set = (key: keyof BriefFormInput) => (value: string) =>
    setForm((f) => ({ ...f, [key]: value }));

  const loadTrace = useCallback(async (jobId: string) => {
    const { trace, cards } = await getCreativeTrace(jobId);
    setTrace(trace ?? null);
    setUsedCards((cards ?? []) as UsedCard[]);
  }, []);

  // 生成中はポーリングし、終端(succeeded/failed)で止めて trace を読む
  useEffect(() => {
    if (!generation || !shouldKeepPolling(generation.status)) return;
    const jobId = generation.job_id;
    const timer = setInterval(async () => {
      const { generation: next, disclosureText } = await getCreativeGeneration(jobId);
      if (!next) return;
      setGeneration(next);
      if (disclosureText !== undefined) setDisclosure(disclosureText);
      if (!shouldKeepPolling(next.status)) loadTrace(jobId);
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [generation, loadTrace]);

  async function handleSubmit() {
    if (submitting) return;
    const validation = validateBrief(form);
    if (!validation.ok) {
      setErrors(validation.errors);
      return;
    }
    setErrors([]);
    setSubmitting(true);
    idempotencyKeyRef.current ??= crypto.randomUUID();

    const result = await createCreativeGeneration(form, idempotencyKeyRef.current);
    setSubmitting(false);

    if (result.errors) return setErrors(result.errors);
    if (result.error) return setErrors([result.error]);
    if (!result.jobId) return setErrors(["生成を開始できませんでした"]);

    setTrace(null);
    setUsedCards([]);
    setTab("work");
    const { generation, disclosureText } = await getCreativeGeneration(result.jobId);
    if (generation) {
      setGeneration(generation);
      setDisclosure(disclosureText ?? "");
    }
  }

  function handleReset() {
    idempotencyKeyRef.current = null;
    setGeneration(null);
    setTrace(null);
    setUsedCards([]);
    setErrors([]);
  }

  const profile = profiles.find((p) => p.profile_id === form.profileId);
  const running = generation != null && shouldKeepPolling(generation.status);

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 p-6">
      <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-stone-200 pb-3">
        <div>
          <h1 className="text-xl font-bold">創作</h1>
          <p className="text-sm text-stone-600">
            原典の特徴を学んだAIが<strong>新しい作品を書きます</strong>。
            本人の思想を答える<Link href="/chat" className="mx-1 underline">思想対話</Link>
            とは別の機能です。
          </p>
        </div>
        <div className="text-xs text-stone-500">
          {isAdmin && (
            <Link href="/admin/creative-cards" className="mr-3 underline hover:text-stone-700">
              管理画面へ
            </Link>
          )}
          <LogoutButton className="underline hover:text-red-700" />
        </div>
      </header>

      {profiles.length === 0 && (
        <p className="rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
          使用できる創作プロファイルがありません。管理者がプロファイルを
          active にすると生成できるようになります。
        </p>
      )}

      {!generation && profiles.length > 0 && (
        <section className="space-y-4">
          <Field label="プロファイル" required>
            <select
              value={form.profileId}
              onChange={(e) => set("profileId")(e.target.value)}
              className="w-full rounded border border-stone-300 p-2"
            >
              {profiles.map((p) => (
                <option key={p.profile_id} value={p.profile_id}>
                  {p.name}
                </option>
              ))}
            </select>
            {profile?.description && (
              <p className="mt-1 text-xs text-stone-500">{profile.description}</p>
            )}
          </Field>

          <Field label="モチーフ" required hint="中心に置きたいもの（例: 鏡）">
            <Input value={form.motif} onChange={set("motif")} placeholder="鏡" />
          </Field>
          <Field label="状況" hint="物語の核となる出来事">
            <Input
              value={form.situation}
              onChange={set("situation")}
              placeholder="鏡の中の自分だけが年を取る"
            />
          </Field>
          <Field label="読後感" hint="読み終えたときに残ってほしいもの">
            <Input
              value={form.emotionalTarget}
              onChange={set("emotionalTarget")}
              placeholder="静かな恐ろしさ"
            />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="時代設定" hint="空欄なら原典に合わせます">
              <Input value={form.period} onChange={set("period")} placeholder="明治" />
            </Field>
            <Field label="文字数" hint="空欄なら既定値">
              <Input value={form.length} onChange={set("length")} placeholder="1500" />
            </Field>
          </div>
          <Field label="追加制約" hint="1行に1つ">
            <textarea
              value={form.constraints}
              onChange={(e) => set("constraints")(e.target.value)}
              rows={3}
              placeholder={"夢の記述から始める\n登場人物は二人まで"}
              className="w-full rounded border border-stone-300 p-2 text-sm"
            />
          </Field>

          {errors.length > 0 && (
            <ul className="space-y-1 rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800">
              {errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          )}

          <button
            type="button"
            onClick={handleSubmit}
            disabled={submitting}
            className="rounded bg-stone-800 px-5 py-2 text-white disabled:bg-stone-300"
          >
            {submitting ? "送信中…" : "生成する"}
          </button>
          <p className="text-xs text-stone-500">
            生成には数分かかります。この画面を開いたままお待ちください。
          </p>
        </section>
      )}

      {running && generation && <Progress step={generation.current_step} />}

      {generation?.status === "failed" && (
        <Failure errorMessage={generation.error_message} onRetry={handleReset} />
      )}

      {generation && canRenderWork(generation, disclosure) && (
        <Result
          generation={generation}
          disclosure={disclosure}
          trace={trace}
          usedCards={usedCards}
          tab={tab}
          onTab={setTab}
          onReset={handleReset}
        />
      )}
    </div>
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

function Input({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <input
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className="w-full rounded border border-stone-300 p-2"
    />
  );
}

function Progress({ step }: { step: string | null }) {
  const p = stepProgress(step);
  return (
    <section className="space-y-3 rounded border border-stone-200 bg-white p-4">
      <div className="flex items-baseline justify-between">
        <p className="font-medium">{p.label}</p>
        <p className="text-sm text-stone-500">
          {p.index} / {p.total}
        </p>
      </div>
      <div className="h-2 overflow-hidden rounded bg-stone-100">
        <div
          className="h-full bg-stone-700 transition-[width] duration-500"
          style={{ width: `${(p.index / p.total) * 100}%` }}
        />
      </div>
      <ol className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-stone-500">
        {STEPS.map((s, i) => (
          <li key={s.key} className={i < p.index ? "text-stone-800" : undefined}>
            {i < p.index ? "✓" : "・"}
            {s.label}
          </li>
        ))}
      </ol>
    </section>
  );
}

function Failure({
  errorMessage,
  onRetry,
}: {
  errorMessage: string | null;
  onRetry: () => void;
}) {
  const m = failureMessage(errorMessage);
  return (
    <section className="space-y-2 rounded border border-red-300 bg-red-50 p-4">
      <h2 className="font-bold text-red-800">{m.title}</h2>
      <p className="text-sm text-red-800">{m.hint}</p>
      <details className="text-xs text-red-700">
        <summary className="cursor-pointer">詳細</summary>
        <pre className="mt-1 whitespace-pre-wrap">{m.detail}</pre>
      </details>
      <button type="button" onClick={onRetry} className="rounded border border-red-400 px-3 py-1 text-sm">
        入力に戻る
      </button>
    </section>
  );
}

const TABS: [Tab, string][] = [
  ["work", "作品"],
  ["outline", "構成"],
  ["cards", "使用カード"],
  ["trace", "Creative Trace"],
  ["guard", "Guard"],
];

function Result({
  generation,
  disclosure,
  trace,
  usedCards,
  tab,
  onTab,
  onReset,
}: {
  generation: GenerationState;
  disclosure: string;
  trace: CreativeTraceView | null;
  usedCards: UsedCard[];
  tab: Tab;
  onTab: (t: Tab) => void;
  onReset: () => void;
}) {
  return (
    <section className="space-y-4">
      <h2 className="text-lg font-bold">{generation.display_title}</h2>

      {/* 免責はタブの外に置く。タブを切り替えても消えない位置であることが要件(仕様§5.1) */}
      <p className="rounded border border-stone-300 bg-stone-100 p-3 text-sm text-stone-700">
        {disclosure}
      </p>

      <div className="flex flex-wrap gap-1 border-b border-stone-200 text-sm">
        {TABS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => onTab(key)}
            className={`rounded-t px-3 py-1.5 ${
              tab === key ? "bg-stone-200 font-medium" : "hover:bg-stone-100"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "work" && (
        <article className="whitespace-pre-wrap leading-loose">
          {generation.final_text}
        </article>
      )}

      {tab === "outline" && (
        <pre className="overflow-x-auto rounded bg-stone-100 p-3 text-xs">
          {JSON.stringify(generation.outline, null, 2)}
        </pre>
      )}

      {tab === "cards" && (
        <div className="space-y-2">
          {usedCards.length === 0 && <p className="text-sm text-stone-500">記録がありません。</p>}
          {usedCards.map((c) => (
            <div key={c.card_id} className="rounded border border-stone-200 p-3">
              <div className="text-xs text-stone-500">{c.card_type}</div>
              <div className="font-medium">{c.title}</div>
              {c.summary && <p className="text-sm text-stone-600">{c.summary}</p>}
            </div>
          ))}
        </div>
      )}

      {tab === "trace" && (
        <dl className="space-y-2 text-sm">
          {trace == null && <p className="text-stone-500">記録がありません。</p>}
          {trace && (
            <>
              <Row label="使用カード">{trace.used_card_ids.length} 枚</Row>
              <Row label="投入した原典">{trace.injected_source_ids.join(", ") || "—"}</Row>
              <Row label="投入したチャンク">{trace.injected_chunk_ids.length} 件</Row>
              <Row label="再生成回数">{trace.regeneration_count} 回</Row>
              <Row label="モデル">
                <pre className="whitespace-pre-wrap text-xs">
                  {JSON.stringify(trace.model_ids, null, 2)}
                </pre>
              </Row>
              <Row label="プロンプト版">
                <pre className="whitespace-pre-wrap text-xs">
                  {JSON.stringify(trace.prompt_versions, null, 2)}
                </pre>
              </Row>
            </>
          )}
        </dl>
      )}

      {tab === "guard" && (
        <pre className="overflow-x-auto rounded bg-stone-100 p-3 text-xs">
          {trace ? JSON.stringify(trace.guard_results, null, 2) : "記録がありません。"}
        </pre>
      )}

      <button
        type="button"
        onClick={onReset}
        className="rounded border border-stone-400 px-4 py-2 text-sm"
      >
        別の作品を書く
      </button>
    </section>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[10rem_1fr] gap-2 border-b border-stone-100 py-1">
      <dt className="text-stone-500">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}
