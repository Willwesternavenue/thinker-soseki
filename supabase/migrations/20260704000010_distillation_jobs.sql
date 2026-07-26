-- 蒸留→カード生成ジョブ(人物単位、横断)。管理画面のボタンから投入し、Workerが実行する。
-- 原典アップロードの ingestion_jobs(source単位・軽蒸留まで)とは別物。
create table public.distillation_jobs (
  job_id uuid primary key default gen_random_uuid(),
  person_id text not null references public.personas(person_id),
  kind text not null default 'all' check (kind in ('all', 'heavy', 'cards', 'questions')),
  status text not null default 'pending' check (status in
    ('pending', 'running', 'succeeded', 'failed')),
  current_step text,
  result jsonb,
  error_message text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index distillation_jobs_status_idx on public.distillation_jobs (status, created_at);

alter table public.distillation_jobs enable row level security;
create policy "admin all" on public.distillation_jobs
  for all using (public.is_admin()) with check (public.is_admin());

create trigger distillation_jobs_updated_at before update on public.distillation_jobs
  for each row execute function public.set_updated_at();
