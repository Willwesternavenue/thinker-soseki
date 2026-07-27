import { fetchCreativeGenerations } from "./actions";
import { GenerationsClient } from "./generations-client";

export const dynamic = "force-dynamic";

/**
 * 創作生成ジョブの監視(T5 admin)。
 *
 * 取り込みジョブの `/admin/jobs` と同じ考え方だが、見るべきものが違う。
 * 創作は「失敗しても本文を保存しない」ので、失敗の原因は trace/guard にしか残らない。
 *
 * 初回ぶんはサーバーで取る。クライアント側の effect は更新だけを受け持つ
 * (effect の中で初回取得もやると、描画されてから数百ms空で待たせることになる)。
 */
export default async function CreativeGenerationsPage() {
  const initial = await fetchCreativeGenerations();

  return (
    <GenerationsClient
      initialRows={initial.generations ?? []}
      initialHeartbeat={initial.heartbeat ?? null}
      initialProfiles={initial.profiles ?? {}}
      initialError={initial.error ?? null}
    />
  );
}
