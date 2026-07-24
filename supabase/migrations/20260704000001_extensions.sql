-- 拡張機能の有効化(仕様3.6)
-- pgvector: ベクトル検索(MVPは正確検索、ANNインデックスなし)
-- PGroonga: 日本語全文検索(MVPから導入)
create extension if not exists vector with schema extensions;
create extension if not exists pgroonga;

-- updated_at 自動更新トリガー関数
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;
