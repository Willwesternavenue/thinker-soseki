-- Storageバケット(仕様3.6 / 6.1)
-- originals: アップロード原本(PDF / Word / TXT / 書き起こし)
-- clean_texts: Worker整形済みテキスト
insert into storage.buckets (id, name, public)
values
  ('originals', 'originals', false),
  ('clean_texts', 'clean_texts', false)
on conflict (id) do nothing;

-- adminのみ読み書き可能(Workerは service_role でバイパス)
create policy "admin read originals" on storage.objects
  for select using (bucket_id in ('originals','clean_texts') and public.is_admin());
create policy "admin insert originals" on storage.objects
  for insert with check (bucket_id in ('originals','clean_texts') and public.is_admin());
create policy "admin update originals" on storage.objects
  for update using (bucket_id in ('originals','clean_texts') and public.is_admin());
create policy "admin delete originals" on storage.objects
  for delete using (bucket_id in ('originals','clean_texts') and public.is_admin());
