-- Run this once in Supabase: SQL Editor -> New query -> Run
-- Also check: Authentication -> Providers -> Email is enabled
-- (turn "Confirm email" off while testing if you do not want confirmation emails)

create table if not exists public.user_configs (
    user_id          uuid primary key references auth.users(id) on delete cascade,
    email            text not null,
    groq_key         text,
    pixabay_keys     text,
    pexels_key       text,
    youtube_token    text,
    facebook_page_id text,
    facebook_token   text,
    main_topic       text default '',
    sub_topics       text default '',
    content_mode     text default 'table',   -- table | story | puzzle | mixed
    language         text default 'English',
    system_prompt    text,
    html_template    text,                   -- custom template for table mode (null = built-in)
    watermark        text,
    is_active        boolean default false,
    last_status      text,
    last_run_at      timestamptz,
    updated_at       timestamptz default now()
);

-- If the table already existed from an older version, add the newer columns:
alter table public.user_configs add column if not exists watermark   text;
alter table public.user_configs add column if not exists last_status text;
alter table public.user_configs add column if not exists last_run_at timestamptz;

-- Row Level Security: every user can only see and change their OWN row.
alter table public.user_configs enable row level security;

drop policy if exists "own row select" on public.user_configs;
drop policy if exists "own row insert" on public.user_configs;
drop policy if exists "own row update" on public.user_configs;
drop policy if exists "own row delete" on public.user_configs;

create policy "own row select" on public.user_configs
    for select using (auth.uid() = user_id);
create policy "own row insert" on public.user_configs
    for insert with check (auth.uid() = user_id);
create policy "own row update" on public.user_configs
    for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own row delete" on public.user_configs
    for delete using (auth.uid() = user_id);

-- The GitHub Actions worker uses the SERVICE ROLE key, which bypasses RLS,
-- so main.py can still read every active user and write last_status.
