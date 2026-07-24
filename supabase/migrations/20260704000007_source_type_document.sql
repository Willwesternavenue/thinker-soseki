-- 種別に「資料(document)」を追加
alter table public.sources drop constraint sources_source_type_check;
alter table public.sources add constraint sources_source_type_check
  check (source_type in
    ('book','video_transcript','interview','dialogue','lecture',
     'article','essay','profile','document','other'));
