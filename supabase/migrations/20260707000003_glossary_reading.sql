-- 用語集に読み(かな)列を追加。将来のWhisper initial-prompt生成・かな→漢字辞書に使う。
alter table public.glossary_terms add column reading text;
