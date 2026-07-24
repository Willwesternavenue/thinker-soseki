-- 思想IDの一括リネーム(全参照・配列・派生列を漏れなく置換)。読み違いID修正に再利用する。
create or replace function public.rename_thought_id(old_id text, new_id text)
returns void
language plpgsql
as $$
begin
  update public.thought_cards set thought_id = new_id where thought_id = old_id;
  update public.thought_questions set target_thought_id = new_id where target_thought_id = old_id;
  update public.thought_evidence_links set thought_id = new_id where thought_id = old_id;
  update public.thought_cards
    set related_thought_ids = array_replace(related_thought_ids, old_id, new_id)
    where old_id = any(related_thought_ids);
  update public.source_chunks
    set related_thought_ids = array_replace(related_thought_ids, old_id, new_id)
    where old_id = any(related_thought_ids);
end;
$$;
