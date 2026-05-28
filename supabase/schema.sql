-- ─────────────────────────────────────────────────────────────────────────────
-- lit-learn — Supabase schema
-- Two independent schemas inside one Supabase project (free-tier-friendly):
--   • learning_notes   — the Learning Notes app
--   • lit_review       — the Literature Review tool
--
-- Run this entire file once in Supabase → SQL Editor → New query → Run.
-- Re-running is safe (CREATE IF NOT EXISTS / no destructive ops).
--
-- Access model: both apps connect with the service_role key from their
-- Streamlit secrets. The Streamlit password gate sits in front of every
-- request, so RLS is intentionally not used. Do NOT expose the anon key
-- with permissive policies — keep all DB access server-side.
-- ─────────────────────────────────────────────────────────────────────────────

create extension if not exists "pgcrypto";

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
-- SCHEMA: learning_notes
-- Mirrors the JSON shape in learning_notes_data.json:
--   courses → modules → sections → topics → resources
-- Text PKs preserve the existing 8-char IDs the app already generates.
-- ═════════════════════════════════════════════════════════════════════════════

create schema if not exists learning_notes;

create table if not exists learning_notes.courses (
  id            text primary key,
  title         text not null default 'My Learning Notes',
  subtitle      text not null default '',
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create table if not exists learning_notes.modules (
  id            text primary key,
  course_id     text not null references learning_notes.courses(id) on delete cascade,
  title         text not null,
  position      int  not null default 0,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists modules_course_pos_idx
  on learning_notes.modules (course_id, position);

create table if not exists learning_notes.sections (
  id            text primary key,
  module_id     text not null references learning_notes.modules(id) on delete cascade,
  title         text not null,
  position      int  not null default 0,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists sections_module_pos_idx
  on learning_notes.sections (module_id, position);

create table if not exists learning_notes.topics (
  id            text primary key,
  section_id    text not null references learning_notes.sections(id) on delete cascade,
  name          text not null,
  done          boolean not null default false,
  notes_html    text not null default '',
  position      int  not null default 0,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists topics_section_pos_idx
  on learning_notes.topics (section_id, position);

create table if not exists learning_notes.resources (
  id            uuid primary key default gen_random_uuid(),
  topic_id      text not null references learning_notes.topics(id) on delete cascade,
  title         text not null,
  type          text not null default '',          -- video / paper / article / code / docs
  authors       text not null default '',
  url           text not null default '',
  reviewed      boolean not null default false,
  position      int  not null default 0,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists resources_topic_pos_idx
  on learning_notes.resources (topic_id, position);

-- Pomodoro session log (was learning_notes_time.json)
create table if not exists learning_notes.time_log (
  id            bigserial primary key,
  log_date      date not null default current_date,
  minutes       numeric not null,
  created_at    timestamptz not null default now()
);
create index if not exists time_log_date_idx
  on learning_notes.time_log (log_date);

-- updated_at triggers
drop trigger if exists trg_courses_updated   on learning_notes.courses;
drop trigger if exists trg_modules_updated   on learning_notes.modules;
drop trigger if exists trg_sections_updated  on learning_notes.sections;
drop trigger if exists trg_topics_updated    on learning_notes.topics;
drop trigger if exists trg_resources_updated on learning_notes.resources;

create trigger trg_courses_updated   before update on learning_notes.courses
  for each row execute function public.set_updated_at();
create trigger trg_modules_updated   before update on learning_notes.modules
  for each row execute function public.set_updated_at();
create trigger trg_sections_updated  before update on learning_notes.sections
  for each row execute function public.set_updated_at();
create trigger trg_topics_updated    before update on learning_notes.topics
  for each row execute function public.set_updated_at();
create trigger trg_resources_updated before update on learning_notes.resources
  for each row execute function public.set_updated_at();


-- ═════════════════════════════════════════════════════════════════════════════
-- SCHEMA: lit_review
-- Mirrors the per-project file layout:
--   projects/<id>/setup.json, sources.xlsx, draft.json, scratchpad.md, time_log.json
-- Complex nested structures (outline, deadlines) live in JSONB columns.
-- ═════════════════════════════════════════════════════════════════════════════

create schema if not exists lit_review;

-- Singleton app-state row (replaces the {"active_project": ...} field
-- of projects.json). We just upsert id='global'.
create table if not exists lit_review.app_state (
  id                  text primary key default 'global',
  active_project_id   text,
  updated_at          timestamptz not null default now()
);
insert into lit_review.app_state (id) values ('global')
  on conflict (id) do nothing;

create table if not exists lit_review.projects (
  id            text primary key,           -- slug, e.g. 'sst-llm-judge'
  name          text not null,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

-- One row per project. All the Setup-tab fields live here.
create table if not exists lit_review.setup (
  project_id              text primary key references lit_review.projects(id) on delete cascade,
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
create table if not exists lit_review.sources (
  id            bigserial primary key,
  project_id    text not null references lit_review.projects(id) on delete cascade,
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
create index if not exists sources_project_pos_idx
  on lit_review.sources (project_id, position);

-- Draft text keyed by flattened section name ("Introduction" or "Introduction > Motivation")
create table if not exists lit_review.draft (
  project_id    text not null references lit_review.projects(id) on delete cascade,
  section_name  text not null,
  draft_text    text not null default '',
  updated_at    timestamptz not null default now(),
  primary key (project_id, section_name)
);

-- Free-form scratchpad per project (was scratchpad.md)
create table if not exists lit_review.scratchpad (
  project_id    text primary key references lit_review.projects(id) on delete cascade,
  content       text not null default '',
  updated_at    timestamptz not null default now()
);

-- Pomodoro session log per project (was time_log.json)
create table if not exists lit_review.time_log (
  id            bigserial primary key,
  project_id    text not null references lit_review.projects(id) on delete cascade,
  log_date      date not null default current_date,
  minutes       numeric not null,
  created_at    timestamptz not null default now()
);
create index if not exists time_log_project_date_idx
  on lit_review.time_log (project_id, log_date);

-- updated_at triggers
drop trigger if exists trg_lr_projects_updated   on lit_review.projects;
drop trigger if exists trg_lr_setup_updated      on lit_review.setup;
drop trigger if exists trg_lr_sources_updated    on lit_review.sources;
drop trigger if exists trg_lr_draft_updated      on lit_review.draft;
drop trigger if exists trg_lr_scratchpad_updated on lit_review.scratchpad;
drop trigger if exists trg_lr_app_state_updated  on lit_review.app_state;

create trigger trg_lr_projects_updated   before update on lit_review.projects
  for each row execute function public.set_updated_at();
create trigger trg_lr_setup_updated      before update on lit_review.setup
  for each row execute function public.set_updated_at();
create trigger trg_lr_sources_updated    before update on lit_review.sources
  for each row execute function public.set_updated_at();
create trigger trg_lr_draft_updated      before update on lit_review.draft
  for each row execute function public.set_updated_at();
create trigger trg_lr_scratchpad_updated before update on lit_review.scratchpad
  for each row execute function public.set_updated_at();
create trigger trg_lr_app_state_updated  before update on lit_review.app_state
  for each row execute function public.set_updated_at();


-- ═════════════════════════════════════════════════════════════════════════════
-- Expose both schemas to PostgREST so the supabase-py client can use them.
-- After running this file, also go to: Project Settings → API → "Exposed
-- schemas" and add  learning_notes, lit_review  to the comma-separated list
-- (or just paste:  public, learning_notes, lit_review). Then Save.
-- ═════════════════════════════════════════════════════════════════════════════

grant usage on schema learning_notes to anon, authenticated, service_role;
grant usage on schema lit_review     to anon, authenticated, service_role;

grant all on all tables    in schema learning_notes to service_role;
grant all on all sequences in schema learning_notes to service_role;
grant all on all tables    in schema lit_review     to service_role;
grant all on all sequences in schema lit_review     to service_role;

alter default privileges in schema learning_notes
  grant all on tables    to service_role;
alter default privileges in schema learning_notes
  grant all on sequences to service_role;
alter default privileges in schema lit_review
  grant all on tables    to service_role;
alter default privileges in schema lit_review
  grant all on sequences to service_role;
