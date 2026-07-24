-- 用語集(スクリプト整形のASR誤変換修正・話者判定の基準)
-- 仕様: docs/superpowers/specs/2026-07-07-transcript-prep-design.md
-- kind='term' 正しい表記(単純置換の基準) / kind='rule' 文脈で判断する使い分けルール
create table public.glossary_terms (
  id uuid primary key default gen_random_uuid(),
  person_id text not null default 'x_shigyo',
  kind text not null default 'term' check (kind in ('term','rule')),
  content text not null,
  note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create trigger glossary_terms_updated_at before update on public.glossary_terms
  for each row execute function public.set_updated_at();

alter table public.glossary_terms enable row level security;
create policy "admin all" on public.glossary_terms
  for all using (public.is_admin()) with check (public.is_admin());

-- 初期データはマイグレーションに固定しない(正本はDB/dump)。
-- 空テーブルで作成し、用語は data dump の復元、または /admin/用語集 で投入する。
-- 以前はここで初期61件をseedしていたが、dump復元と二重投入(122件)になるため撤去した。
