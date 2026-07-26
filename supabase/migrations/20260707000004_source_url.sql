-- 原典の元動画URL(ベンダー書き起こしヘッダー等から抽出)。将来の発言→動画リンクに使う。
alter table public.sources add column source_url text;
