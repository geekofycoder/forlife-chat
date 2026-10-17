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

-- Family profiles. One row per person: the account holder (relation 'self') and each family member they add.
-- Same rule as messages: row-level security on, no policies, so only the backend can read or write.
create table public.members (
  id          uuid        primary key default gen_random_uuid(),
  visitor_id  uuid        not null,
  relation    text        not null check (relation in ('self', 'spouse', 'son', 'daughter', 'father', 'mother',
                                                       'brother', 'sister', 'grandfather', 'grandmother', 'other')),
  name        text        not null check (char_length(name) between 1 and 60),
  dob         date        not null check (dob >= date '1900-01-01'),
  sex         text        not null check (sex in ('male', 'female', 'other')),
  conditions  text        not null default '' check (char_length(conditions) <= 500),
  medicines   text        not null default '' check (char_length(medicines) <= 500),
  allergies   text        not null default '' check (char_length(allergies) <= 500),
  pincode     text        check (pincode ~ '^[1-9][0-9]{5}$'),  -- asked on the 'self' row; "doctors near you" searches this pincode
  created_at  timestamptz not null default now()
);
-- A database created before pincodes needs this one line (run it BEFORE deploying the code that reads the column):
--   alter table public.members add column if not exists pincode text check (pincode ~ '^[1-9][0-9]{5}$');

create index members_visitor_id_idx on public.members (visitor_id, created_at);
create unique index members_one_self_per_visitor on public.members (visitor_id) where relation = 'self';  -- only one "you" each
create unique index members_no_duplicate_people on public.members (visitor_id, relation, lower(name), dob, sex);  -- same person can't be added twice, even by two requests at once

alter table public.members enable row level security;

-- Each family member's own doctors: their family doctor, a child's paediatrician, a woman's gynaecologist.
-- One doctor per role per person. Kept separate from members so directory links can be added here later.
create table public.member_doctors (
  id          uuid        primary key default gen_random_uuid(),
  visitor_id  uuid        not null,
  member_id   uuid        not null references public.members(id) on delete cascade,
  role        text        not null check (role in ('family', 'paediatrician', 'gynaecologist')),
  name        text        not null check (char_length(name) between 1 and 80),
  phone       text        not null default '' check (char_length(phone) <= 16),
  address     text        not null default '' check (char_length(address) <= 200),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create unique index member_doctors_one_per_role on public.member_doctors (member_id, role);
create index member_doctors_visitor_idx on public.member_doctors (visitor_id);
alter table public.member_doctors enable row level security;

-- "Doctors near you": the cleaned search results for one pincode and one kind of doctor (a key from doctors.py, such as
-- 'ent'), shared by everyone. Fresh for 7 days (an empty answer for 1), and an answer up to 30 days old is still served if
-- the search service is down. Only cleaned fields (name, rating, address, phone, position) are stored, never the raw answer.
create table public.nearby_cache (
  pincode      text        not null check (pincode ~ '^[1-9][0-9]{5}$'),
  doctor_type  text        not null,
  results      jsonb       not null default '[]'::jsonb,
  fetched_at   timestamptz not null default now(),
  primary key (pincode, doctor_type)
);
alter table public.nearby_cache enable row level security;
