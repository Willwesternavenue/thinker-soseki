-- スクリプト整形の下書き(YouTube生書き起こし → LLM整形 → レビュー → 取り込み)
-- 仕様: docs/superpowers/specs/2026-07-07-transcript-prep-design.md
create table public.transcript_drafts (
  draft_id uuid primary key default gen_random_uuid(),
  person_id text not null default 'natsume_soseki',
  title text not null,
  video_url text,
  hint text,
  priority text not null default 'core' check (priority in
    ('core','important','support','style','archive')),
  raw_text text not null,
  turns jsonb not null default '[]'::jsonb,
  processed_segments int not null default 0,
  status text not null default 'processing' check (status in
    ('processing','review','ingested')),
  source_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create trigger transcript_drafts_updated_at before update on public.transcript_drafts
  for each row execute function public.set_updated_at();

alter table public.transcript_drafts enable row level security;
create policy "admin all" on public.transcript_drafts
  for all using (public.is_admin()) with check (public.is_admin());
