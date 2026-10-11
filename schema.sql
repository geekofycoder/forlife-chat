-- The one table for v0: every chat message, grouped by visitor.
-- Row-level security is ON with no policies, so the public Supabase key can't read it;
-- only our backend (which connects with DATABASE_URL) can.

create table public.messages (
  id          bigint generated always as identity primary key,
  visitor_id  uuid        not null,
  role        text        not null check (role in ('user', 'assistant')),
  content     text        not null,
  created_at  timestamptz not null default now()
);

create index messages_visitor_id_idx on public.messages (visitor_id, id);

alter table public.messages enable row level security;
