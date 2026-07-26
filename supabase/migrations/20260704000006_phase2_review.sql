-- Phase 2: レビューフロー用の拡張
-- AI生成リンクは draft、人間レビュー後に approved(仕様6.10 / 6.11)
alter table public.thought_evidence_links
  add column status text not null default 'draft'
  check (status in ('draft','approved','rejected'));

-- 重蒸留(Sonnet)の結果格納先(仕様6.6)。
-- 重要な区別 / 禁止すべき解釈 / 回答方針候補 / 代表引用候補 / カード更新候補 / リンク候補
alter table public.chunk_distillations
  add column heavy_json jsonb;

-- 派生列の再生成は approved リンクのみを正本として反映する(仕様6.11)
create or replace function public.rebuild_related_thought_ids(
  target_person_id text default 'natsume_soseki'
)
returns void
language sql
as $$
  update public.source_chunks sc
  set related_thought_ids = coalesce(agg.tids, '{}')
  from (
    select c.chunk_id,
           (select array_agg(distinct l.thought_id)
              from public.thought_evidence_links l
             where l.chunk_id = c.chunk_id
               and l.status = 'approved') as tids
    from public.source_chunks c
    where c.person_id = target_person_id
  ) agg
  where sc.chunk_id = agg.chunk_id
    and sc.related_thought_ids is distinct from coalesce(agg.tids, '{}');
$$;
