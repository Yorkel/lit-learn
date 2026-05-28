# CLAUDE.md — context for Claude Code

## What this repo is

`lit-review-tool` — a single-user, multi-project Streamlit literature-review tool. Each project is one paper the user is writing. Data lives in `./projects/<project-id>/` in whatever cwd the user invokes the tool from, so each paper's lit-review notes stay inside that paper's own repo.

Originally built on 2026-05-27 inside `github.com/yorkel/sst-llm-judge` (a paper repo for an EMNLP submission) and lifted into its own repo on 2026-05-28 to be usable across multiple paper repos.

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
- `seed_from_bib.py` — CLI to seed a project's `sources.xlsx` from a `.bib` file
- `requirements.txt` — `streamlit`, `pandas`, `openpyxl`, `anthropic`
- `.streamlit/config.toml` — green theme (`#2D6E47` primary)
- `pyproject.toml` — declares the two top-level modules, `pip install -e .`-able

### Data layout (in user's cwd, not in this repo)

```
./
├── projects.json                  registry: {active_project, projects: [{id, name, data_dir}]}
└── projects/
    └── <project-id>/
        ├── setup.json             paper title, thesis, structured outline, deadlines (DD-MM-YYYY UI / ISO storage), plans, default_tags, target_word_count, formatting_guidelines
        ├── sources.xlsx           16-column table: key / title / authors / year / venue / source_type / doi / url / tags / quotes / notes / thoughts / summary / status / drafted / flag / flag_note
        ├── draft.json             {section_name: "draft text"} — section names are flat strings like "Introduction" or "Introduction > Motivation"
        ├── scratchpad.md          per-project free-form
        └── time_log.json          [{"date": "YYYY-MM-DD", "minutes": N}, ...]
```

`projects/` is in `.gitignore` — never committed to this tool repo.

### Path conventions

- `DATA_DIR = Path.cwd()` — where projects + registry live
- `APP_DIR = Path(__file__).parent` — tool install location, used only for `.env` fallback
- `_load_dotenv()` reads `.env` from cwd, cwd's parent, and the tool install dir

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

She'll typically open a paper repo (e.g. `sst-llm-judge`, `public-sector-sst`, `AI-Governance-Analysis-UK-ROI`), clone or `git pull` this tool repo, and run `streamlit run ~/lit-review-tool/lit_review_app.py` from inside her paper repo. New paper = new project via the sidebar "+ New project" button; bib seeding is one-shot via `python ~/lit-review-tool/seed_from_bib.py ...`.

If something breaks, first move is to check `python3 -c "import ast; ast.parse(open('lit_review_app.py').read())"` and then run streamlit and read the traceback. Most failures are streamlit's widget-key / session-state rules (see Hard rules above).
