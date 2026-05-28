-- ─────────────────────────────────────────────────────────────────────────────
-- lit-learn — Supabase schema (v2: flat in public with prefixes)
--
-- Originally designed with two schemas (learning_notes, lit_review) but the
-- Supabase "Exposed schemas" dashboard setting did not propagate to PostgREST
-- on this project, so we use `public` (already exposed) with prefixes instead:
--   • ln_*   — Learning Notes app tables
--   • lr_*   — Literature Review tool tables
--
-- Run this entire file once in Supabase → SQL Editor → New query → Run.
-- Re-running is safe (CREATE IF NOT EXISTS / no destructive ops on data).
--
-- Access model: both apps connect with the service_role / secret key from
-- their Streamlit secrets. The Streamlit password gate sits in front of every
-- request, so RLS is intentionally not used.
-- ─────────────────────────────────────────────────────────────────────────────

create extension if not exists "pgcrypto";

-- Clean up the previous (un-exposed) schemas if they exist from v1 of this file.
-- Safe because they had no data — we never finished wiring the apps to them.
drop schema if exists learning_notes cascade;
drop schema if exists lit_review     cascade;

-- Shared trigger to keep updated_at fresh on any UPDATE
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;


-- ═════════════════════════════════════════════════════════════════════════════
-- LEARNING NOTES (prefix: ln_)
-- Mirrors the JSON shape in learning_notes_data.json:
--   ln_courses → ln_modules → ln_sections → ln_topics → ln_resources
-- Text PKs preserve the existing 8-char IDs the app already generates.
-- ═════════════════════════════════════════════════════════════════════════════

create table if not exists public.ln_courses (
  id            text primary key,
  title         text not null default 'My Learning Notes',
  subtitle      text not null default '',
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create table if not exists public.ln_modules (
  id            text primary key,
  course_id     text not null references public.ln_courses(id) on delete cascade,
  title         text not null,
  position      int  not null default 0,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists ln_modules_course_pos_idx
  on public.ln_modules (course_id, position);

create table if not exists public.ln_sections (
  id            text primary key,
  module_id     text not null references public.ln_modules(id) on delete cascade,
  title         text not null,
  position      int  not null default 0,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists ln_sections_module_pos_idx
  on public.ln_sections (module_id, position);

create table if not exists public.ln_topics (
  id            text primary key,
  section_id    text not null references public.ln_sections(id) on delete cascade,
  name          text not null,
  done          boolean not null default false,
  notes_html    text not null default '',
  position      int  not null default 0,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists ln_topics_section_pos_idx
  on public.ln_topics (section_id, position);

create table if not exists public.ln_resources (
  id            uuid primary key default gen_random_uuid(),
  topic_id      text not null references public.ln_topics(id) on delete cascade,
  title         text not null,
  type          text not null default '',          -- video / paper / article / code / docs
  authors       text not null default '',
  url           text not null default '',
  reviewed      boolean not null default false,
  position      int  not null default 0,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists ln_resources_topic_pos_idx
  on public.ln_resources (topic_id, position);

-- Pomodoro session log (was learning_notes_time.json)
create table if not exists public.ln_time_log (
  id            bigserial primary key,
  log_date      date not null default current_date,
  minutes       numeric not null,
  created_at    timestamptz not null default now()
);
create index if not exists ln_time_log_date_idx
  on public.ln_time_log (log_date);

-- updated_at triggers
drop trigger if exists trg_ln_courses_updated   on public.ln_courses;
drop trigger if exists trg_ln_modules_updated   on public.ln_modules;
drop trigger if exists trg_ln_sections_updated  on public.ln_sections;
drop trigger if exists trg_ln_topics_updated    on public.ln_topics;
drop trigger if exists trg_ln_resources_updated on public.ln_resources;

create trigger trg_ln_courses_updated   before update on public.ln_courses
  for each row execute function public.set_updated_at();
create trigger trg_ln_modules_updated   before update on public.ln_modules
  for each row execute function public.set_updated_at();
create trigger trg_ln_sections_updated  before update on public.ln_sections
  for each row execute function public.set_updated_at();
create trigger trg_ln_topics_updated    before update on public.ln_topics
  for each row execute function public.set_updated_at();
create trigger trg_ln_resources_updated before update on public.ln_resources
  for each row execute function public.set_updated_at();


-- ═════════════════════════════════════════════════════════════════════════════
-- LIT REVIEW (prefix: lr_)
-- Mirrors the per-project file layout:
--   projects/<id>/setup.json, sources.xlsx, draft.json, scratchpad.md, time_log.json
-- Complex nested structures (outline, deadlines) live in JSONB columns.
-- ═════════════════════════════════════════════════════════════════════════════

-- Singleton app-state row (replaces the {"active_project": ...} field
-- of projects.json). We just upsert id='global'.
create table if not exists public.lr_app_state (
  id                  text primary key default 'global',
  active_project_id   text,
  updated_at          timestamptz not null default now()
);
insert into public.lr_app_state (id) values ('global')
  on conflict (id) do nothing;

create table if not exists public.lr_projects (
  id            text primary key,           -- slug, e.g. 'sst-llm-judge'
  name          text not null,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

-- One row per project. All the Setup-tab fields live here.
create table if not exists public.lr_setup (
  project_id              text primary key references public.lr_projects(id) on delete cascade,
  title                   text not null default '',
  thesis                  text not null default '',
  outline                 jsonb not null default '[]'::jsonb,   -- [{id, title, written, subsections:[{id,title,written}]}]
  deadlines               jsonb not null default '[]'::jsonb,   -- [{label, date_iso}]
  plans                   text not null default '',
  default_tags            jsonb not null default '[]'::jsonb,   -- ["tag1", "tag2", ...]
  target_word_count       int  not null default 0,
  formatting_guidelines   text not null default '',
  updated_at              timestamptz not null default now()
);

-- One row per cited paper (was sources.xlsx, 17 cols)
create table if not exists public.lr_sources (
  id            bigserial primary key,
  project_id    text not null references public.lr_projects(id) on delete cascade,
  key           text not null default '',          -- bibtex key
  title         text not null default '',
  authors       text not null default '',
  year          text not null default '',
  venue         text not null default '',
  source_type   text not null default '',
  doi           text not null default '',
  url           text not null default '',
  tags          text not null default '',
  quotes        text not null default '',
  notes         text not null default '',
  thoughts      text not null default '',
  summary       text not null default '',
  status        text not null default '',
  drafted       text not null default '',
  flag          text not null default '',          -- '' or '⭐'
  flag_note     text not null default '',
  position      int  not null default 0,           -- preserves xlsx row order
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (project_id, key)                         -- bib-key dedup within project
);
create index if not exists lr_sources_project_pos_idx
  on public.lr_sources (project_id, position);

-- Draft text keyed by flattened section name ("Introduction" or "Introduction > Motivation")
create table if not exists public.lr_draft (
  project_id    text not null references public.lr_projects(id) on delete cascade,
  section_name  text not null,
  draft_text    text not null default '',
  updated_at    timestamptz not null default now(),
  primary key (project_id, section_name)
);

-- Free-form scratchpad per project (was scratchpad.md)
create table if not exists public.lr_scratchpad (
  project_id    text primary key references public.lr_projects(id) on delete cascade,
  content       text not null default '',
  updated_at    timestamptz not null default now()
);

-- Pomodoro session log per project (was time_log.json)
create table if not exists public.lr_time_log (
  id            bigserial primary key,
  project_id    text not null references public.lr_projects(id) on delete cascade,
  log_date      date not null default current_date,
  minutes       numeric not null,
  created_at    timestamptz not null default now()
);
create index if not exists lr_time_log_project_date_idx
  on public.lr_time_log (project_id, log_date);

-- updated_at triggers
drop trigger if exists trg_lr_projects_updated   on public.lr_projects;
drop trigger if exists trg_lr_setup_updated      on public.lr_setup;
drop trigger if exists trg_lr_sources_updated    on public.lr_sources;
drop trigger if exists trg_lr_draft_updated      on public.lr_draft;
drop trigger if exists trg_lr_scratchpad_updated on public.lr_scratchpad;
drop trigger if exists trg_lr_app_state_updated  on public.lr_app_state;

create trigger trg_lr_projects_updated   before update on public.lr_projects
  for each row execute function public.set_updated_at();
create trigger trg_lr_setup_updated      before update on public.lr_setup
  for each row execute function public.set_updated_at();
create trigger trg_lr_sources_updated    before update on public.lr_sources
  for each row execute function public.set_updated_at();
create trigger trg_lr_draft_updated      before update on public.lr_draft
  for each row execute function public.set_updated_at();
create trigger trg_lr_scratchpad_updated before update on public.lr_scratchpad
  for each row execute function public.set_updated_at();
create trigger trg_lr_app_state_updated  before update on public.lr_app_state
  for each row execute function public.set_updated_at();

-- Force PostgREST to pick up the new tables immediately
notify pgrst, 'reload schema';
