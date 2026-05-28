# CLAUDE.md — context for Claude Code

## What this repo is

`lit-review-tool` — a single-user, multi-project Streamlit literature-review tool. Each project is one paper the user is writing.

**2026-05-28 update:** data was originally per-project filesystem (`./projects/<id>/setup.json`, `sources.xlsx`, `draft.json`, etc.) inside whatever cwd the tool was launched from. Now everything lives in a single **Neon Postgres** database, in `lr_*`-prefixed tables in the `public` schema. The multi-project model is preserved — projects are still keyed by id and the UI lets you switch between them — but all projects' data is in one central database instead of scattered across paper-repo folders.

Originally built on 2026-05-27 inside `github.com/yorkel/sst-llm-judge`, lifted into its own repo + ported to Neon on 2026-05-28.

## Who the user is

Louise Yorke — researcher with several papers in flight, working from GitHub Codespaces, has ADHD. Lit review has been the biggest blocker in her career; the tool exists to make it manageable. UX decisions should always weight ADHD-friendliness highly:

- one item on screen at a time
- autoload last-viewed item
- paste-first capture, no required fields, no validation
- staging-buffer + explicit commit (paste → optionally clean → ✅ Add to Excel commits + clears, so the user can dump-and-go)
- visible progress (counters, badges, target word count)
- "flag to come back to" mechanism
- search across own notes for fast retrieval

She types fast and informally (lowercase, typos OK) and prefers terse responses — ~1/3 of instinctive length, no preambles, one draft not three. Don't pile on after a direction is given. When she's decided something, execute — don't re-litigate.

## Architecture

### Files

- `lit_review_app.py` — the Streamlit app (~1.9k LoC, single file)
- `db.py` — Neon data layer; all file-I/O helpers were moved here, signatures preserved
- `seed_from_bib.py` — CLI to seed a project's sources from a `.bib` file (legacy: writes xlsx; needs porting to also push to Neon)
- `requirements.txt` — `streamlit`, `pandas`, `openpyxl`, `anthropic`, `psycopg[binary]`
- `.streamlit/config.toml` — green theme (`#2D6E47` primary)
- `secrets.toml.template` — copy to `.streamlit/secrets.toml` for local dev
- `pyproject.toml` — declares the two top-level modules, `pip install -e .`-able

### Data layout (in Neon Postgres, `public` schema, prefix `lr_`)

| Table | Purpose |
|---|---|
| `lr_app_state` | singleton row holding `active_project_id` |
| `lr_projects` | `{id, name}` — one row per paper |
| `lr_setup` | per-project paper config: title, thesis, outline (jsonb), deadlines (jsonb), plans, default_tags, target_word_count, formatting_guidelines |
| `lr_sources` | one row per cited paper (the old `sources.xlsx` 17 columns) |
| `lr_draft` | per `(project_id, section_name)`: the draft prose for that section |
| `lr_scratchpad` | per-project free-form text |
| `lr_time_log` | per-project pomodoro session log |

Connection comes from `NEON_DATABASE_URL` (env var or Streamlit secret). `db.py` auto-loads `.env` from its own dir, parent dir, and cwd.

### What changed in the rewrite

- `load_registry()` / `save_registry()` now hit `lr_app_state` + `lr_projects` (no more `projects.json`).
- `load_setup()` / `save_setup()` use `lr_setup` with `jsonb` columns for outline/deadlines.
- `load_sources()` / `save_sources()` round-trip a pandas DataFrame through `lr_sources` (same 17 columns).
- `load_draft()` / `save_draft()` use `(project_id, section_name)` PK on `lr_draft`.
- `load_scratchpad()` / `save_scratchpad()` upsert into `lr_scratchpad`.
- `log_session()` inserts into `lr_time_log`; `load_time_log()` reads ordered by id.
- `project_data_dir()` still exists but returns a notional path — nothing writes to disk anymore.
- New: `delete_project()` for clean removal.

### Tabs

- **Setup** — paper config + Papers section (bulk bib import + single-add). Outline editor: each section is a bordered container with a green "Section N" badge; rows are `title input / ✓ Written / ↑ / ↓ / ✕`; subsections numbered `↳ N.M`; `+ section` / `+ subsection` buttons add empty rows. Stable IDs on sections/subsections so reordering doesn't scramble widget state.
- **Review** — paper card numbered `Paper NN ·`, inline URL-add when missing, status selectbox, tags input with ✨ Suggest, 📝 Summary with ✨ Generate, three staging-buffer textareas (Direct quotes / Notes / Thoughts) each with ✨ Clean, ✅ Add to Excel appends + clears, View-saved expander, ⭐ High importance flag.
- **Draft** — section picker from flattened outline; word count `current / target`; right pane = paper-notes picker + checklist of papers not yet incorporated + `Incorporated: X / Y` counter; Build draft.md export.
- **Compiled notes** — `st.data_editor` table with `LinkColumn` for URL, filter by tag substring, Save edits / Download xlsx.

### Sidebar

Project switcher · Pomodoro (in an `@st.fragment(run_every=1)` so only the timer ticks, not the whole page) · per-project Scratchpad · 🤔 Explain-to-me (LLM, plain-language explanation of any pasted text, **not** "ten-year-old" framing and **without** a relevance-to-paper paragraph) · LLM status indicator at the bottom.

### LLM features (Claude Haiku 4.5)

Four LLM buttons all use a **deferred-flag + `st.spinner` pattern** so the user sees a spinning indicator with status text during the 2–5 s call instead of a frozen UI:

1. **✨ Clean** (per staging box × 3) — `llm_clean(raw)` — fixes OCR / line-break noise
2. **✨ Suggest** (tags input) — `llm_suggest_tags(paper_meta, quotes, notes, thoughts, thesis)` — returns 3–6 lowercase topic tags
3. **✨ Generate** (Summary section) — `llm_summarise_paper(paper_meta, quotes, notes, thoughts, thesis)` — two paragraphs: what the paper says + how it relates to the user's thesis
4. **🤔 Explain** (sidebar) — `llm_explain(text)` — plain-language explanation aimed at a generally-educated reader, no relevance-to-paper paragraph

If `ANTHROPIC_API_KEY` is not set, all LLM buttons hide and the rest of the app works normally.

## Hard rules (learned 2026-05-27 during initial build)

- **No `§` symbol anywhere** in seeded content or UI. Use "Section" instead.
- **Categories are topic tags**, not source-type labels (rejected: `Foundational / Closest precursor / Adjacent`; accepted: free-form lowercase topic tags).
- **Widget keys must not collide with `st.session_state` direct assignments** — streamlit 1.35+ raises `StreamlitValueAssignmentNotAllowedError`. Use distinct names (e.g. `pom_start_ts` for state, `pom_play_btn` for button key).
- **`use_container_width=True` is deprecated post-2025-12-31** — use `width="stretch"` everywhere.
- **Never auto-run notebooks**; the user runs all `.ipynb` files. `.py` scripts are fine to run directly (e.g. seed scripts).
- **Visual section dividers needed** — sections should be visually distinguishable with bordered containers + green badges, not just dividers.
- **No "+ subsection" placeholder text inputs** — replace with explicit `+ subsection` buttons that add an empty editable row.

## Development conventions

- **Approval before destructive actions** — state exactly what will be cleared / deleted in chat and wait for explicit yes. "Build X" is not approval for sub-decisions.
- **Edit, don't rewrite** when the change is local. Rewrite (`Write`) only when restructuring large sections.
- **Run the app yourself** (you = Claude Code) before claiming a fix works — at minimum verify `python3 -c "import ast; ast.parse(...)"`. Streamlit will only fail at request time, so syntax check + smoke test is the floor.
- **Backup the .py file as `.bak`** before large rewrites; remove the `.bak` once verified working.
- **No `TodoWrite` for single-file edit sessions**; just narrate as you go.

## Open / deferred work

- The current single-file design is intentional. Don't split into modules unless features clearly demand it.
- LLM streaming (`st.write_stream`) for the Generate / Explain buttons would improve perceived latency further — deferred until shape stabilises.
- No tests yet. Add `pytest` smoke tests for the bib parser + the source/setup IO helpers when there's a reason.
- A public marketing pass (proper README, screenshots, demo project) is deferred until the user has personally driven the tool through one full paper cycle.

## When the user comes back

She'll open the deployed Streamlit Cloud URL (one URL covers all her papers — projects are switchable in the sidebar). Local development is `streamlit run lit_review_app.py` from this directory with `NEON_DATABASE_URL` in env or `.streamlit/secrets.toml`.

New paper = new project via the sidebar "+ New project" button. Bib seeding via `python seed_from_bib.py ...` still writes `sources.xlsx` to disk — needs porting to write directly to `lr_sources` table. For now, after running it, you can push the xlsx into Neon via `db.save_sources(project, pd.read_excel('path/to/sources.xlsx', dtype=str).fillna(''))`.

If something breaks, first move is `python3 -c "import ast; ast.parse(open('lit_review_app.py').read())"` then run streamlit and read the traceback. Most failures are streamlit's widget-key / session-state rules (see Hard rules above). For DB issues, check `NEON_DATABASE_URL` is set and `db.load_registry()` works in a Python REPL.
