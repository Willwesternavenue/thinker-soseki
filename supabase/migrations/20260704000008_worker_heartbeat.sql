-- Workerの生存確認(heartbeat)。UIがWorkerの稼働状態を表示するために使う。
-- Workerはポーリングループと各ステップで last_seen_at を更新する。
create table public.worker_heartbeats (
  worker_name text primary key,
  status text not null default 'idle',       -- idle | processing
  current_job_id uuid,
  current_source_id text,
  last_seen_at timestamptz not null default now()
);

alter table public.worker_heartbeats enable row level security;

-- adminのみ閲覧(Workerはservice_roleで書き込み、RLSバイパス)
create policy "admin read heartbeat" on public.worker_heartbeats
  for select using (public.is_admin());
