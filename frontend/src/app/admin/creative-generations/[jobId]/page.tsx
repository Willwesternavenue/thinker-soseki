import Link from "next/link";
import { notFound } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { failureMessage, stepProgress } from "@/app/creative/creative";
import { summarizeGuard } from "../monitoring";

export const dynamic = "force-dynamic";

/**
 * 1ジョブの詳細。**失敗ジョブの原因追跡が主目的**。
 *
 * 創作は違反したまま本文を保存しないので、失敗の原因は trace と guard_results に
 * しか残らない(仕様§15.2)。どこまで進んで何に引っかかったかをここで見る。
 */
export default async function CreativeGenerationPage({
  params,
}: {
  params: Promise<{ jobId: string }>;
}) {
  const { jobId } = await params;
  const supabase = createClient();

  const { data: job } = await supabase
    .from("creative_generations")
    .select("*")
    .eq("job_id", jobId)
    .maybeSingle();
  if (!job) notFound();

  const [{ data: traces }, { data: profile }] = await Promise.all([
    supabase
      .from("creative_traces")
      .select("*")
      .eq("job_id", jobId)
      .order("created_at", { ascending: false })
      .limit(1),
    supabase
      .from("creative_profiles")
      .select("name")
      .eq("profile_id", job.profile_id)
      .maybeSingle(),
  ]);
  const trace = (traces ?? [])[0] as Record<string, unknown> | undefined;

  const usedCardIds = (trace?.used_card_ids as string[] | undefined) ?? [];
  const { data: cards } = await supabase
    .from("creative_cards")
    .select("card_id, card_type, title, status")
    .in("card_id", usedCardIds.length > 0 ? usedCardIds : ["__none__"]);

  const guard = summarizeGuard(trace?.guard_results as Record<string, unknown> | undefined);
  const progress = stepProgress(job.current_step as string | null);
  const failure = job.status === "failed" ? failureMessage(job.error_message as string) : null;

  return (
    <div className="max-w-4xl space-y-6">
      <Link href="/admin/creative-generations" className="text-sm text-blue-700 underline">
        ← 創作生成ジョブ一覧
      </Link>

      <div className="space-y-1">
        <h1 className="text-xl font-bold">
          {(job.display_title as string) ??
            ((job.brief_raw as Record<string, unknown>)?.motif as string) ??
            "(モチーフ未指定)"}
        </h1>
        <p className="text-sm text-stone-600">
          {profile?.name ?? (job.profile_id as string)} / {job.status as string}
          {job.status !== "succeeded" && ` / ${progress.label}（${progress.index}/${progress.total}）`}
        </p>
        <p className="font-mono text-xs text-stone-400">{jobId}</p>
      </div>

      {failure && (
        <section className="space-y-2 rounded border border-red-300 bg-red-50 p-4">
          <h2 className="font-bold text-red-800">{failure.title}</h2>
          <p className="text-sm text-red-800">{failure.hint}</p>
          <pre className="overflow-x-auto rounded bg-red-100 p-2 text-xs whitespace-pre-wrap">
            {failure.detail}
          </pre>
        </section>
      )}

      <Section title="依頼内容">
        <Json value={job.brief_raw} />
        {job.brief_normalized != null && (
          <>
            <h3 className="mt-3 mb-1 text-sm font-medium text-stone-600">
              正規化後（Step1 の結果）
            </h3>
            <Json value={job.brief_normalized} />
          </>
        )}
      </Section>

      <Section
        title="Guard"
        note="原典との類似・誤認の判定。閾値はプロファイルの設定から読まれる。"
      >
        {guard.passed === null ? (
          <p className="text-sm text-stone-500">
            Guard に到達する前に終了したため、判定結果はありません。
          </p>
        ) : (
          <>
            <div className="mb-2 flex flex-wrap gap-3 text-sm">
              <Stat label="判定" value={guard.passed ? "通過" : "違反"} />
              <Stat label="連続一致" value={`${guard.lcsLen ?? "—"} 字`} />
              <Stat
                label="n-gram重なり"
                value={guard.ngramRatio == null ? "—" : `${(guard.ngramRatio * 100).toFixed(1)}%`}
              />
              <Stat label="再生成" value={`${(trace?.regeneration_count as number) ?? 0} 回`} />
            </div>
            {guard.lcsText && (
              <p className="mb-2 text-xs text-stone-600">
                最長一致: <code className="rounded bg-stone-100 px-1">{guard.lcsText}</code>
              </p>
            )}
            {guard.violations.length > 0 && (
              <ul className="mb-2 space-y-1 rounded border border-red-300 bg-red-50 p-2 text-sm text-red-800">
                {guard.violations.map((v) => (
                  <li key={v}>{v}</li>
                ))}
              </ul>
            )}
            <Json value={trace?.guard_results} />
          </>
        )}
      </Section>

      <Section title={`使用カード（${usedCardIds.length}枚）`}>
        {usedCardIds.length === 0 ? (
          <p className="text-sm text-stone-500">記録がありません。</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {(cards ?? []).map((c) => (
              <li key={c.card_id as string}>
                <span className="mr-2 text-xs text-stone-500">{c.card_type as string}</span>
                <Link
                  href={`/admin/creative-cards/${c.card_id}`}
                  className="text-blue-700 underline"
                >
                  {c.title as string}
                </Link>
                {c.status !== "approved" && (
                  <span className="ml-2 text-xs text-amber-700">
                    ← 現在は {c.status as string}（生成後に変更された）
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Creative Trace" note="何を入れて何のモデル・プロンプトで作ったか。">
        {trace == null ? (
          <p className="text-sm text-stone-500">trace がありません。</p>
        ) : (
          <dl className="space-y-1 text-sm">
            <Row label="投入した原典">
              {(trace.injected_source_ids as string[]).join(", ") || "—"}
            </Row>
            <Row label="投入したチャンク">
              {(trace.injected_chunk_ids as string[]).length} 件
            </Row>
            <Row label="モデル">
              <Json value={trace.model_ids} />
            </Row>
            <Row label="プロンプト版">
              <Json value={trace.prompt_versions} />
            </Row>
          </dl>
        )}
      </Section>

      {job.status === "succeeded" && (
        <Section title="本文">
          <article className="whitespace-pre-wrap text-sm leading-loose">
            {job.final_text as string}
          </article>
        </Section>
      )}
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
    <section className="space-y-2 rounded border border-stone-200 bg-white p-4">
      <h2 className="font-bold">{title}</h2>
      {note && <p className="text-xs text-stone-600">{note}</p>}
      {children}
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <span className="rounded bg-stone-100 px-2 py-1">
      <span className="text-stone-500">{label}</span> <strong>{value}</strong>
    </span>
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

function Json({ value }: { value: unknown }) {
  return (
    <pre className="overflow-x-auto rounded bg-stone-100 p-2 text-xs whitespace-pre-wrap">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}
