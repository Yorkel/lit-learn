# lit-review-tool

A single-user, multi-project Streamlit tool for literature review. Built for academic writers managing several papers in parallel. Lives inside the [`lit-learn`](../README.md) repo; deployed as one Streamlit Community Cloud app.

Each **project** is one paper you're writing. The sidebar dropdown switches between them. All projects' data lives in one **Neon Postgres** database.

## What's in it

- **Setup tab** — paper title, thesis, structured outline (sections + subsections with ✓ Written + reorder), deadlines, plans, target word count, formatting guidelines, paper list (bulk import from .bib or single add)
- **Review tab** — paper card, inline URL-add, tags input with ✨ LLM suggest, 📝 Summary section with ✨ LLM generate, three staging-buffer text boxes (Direct quotes / Notes to summarise / My thoughts) each with ✨ Clean, single ✅ Add to Excel button that appends + clears, ⭐ High importance flag
- **Draft tab** — section picker from your outline, draft text area with word-count progress, right pane = paper-notes picker + checklist of papers not yet incorporated
- **Compiled notes tab** — sortable / editable table of all sources
- **Sidebar** — project switcher, Pomodoro timer, scratchpad, 🤔 Explain-to-me (paste any text → plain-language explanation)

LLM features use Claude Haiku 4.5 and require `ANTHROPIC_API_KEY`. They're optional — without the key, the ✨/🤔 buttons hide.

## Run locally

```bash
cd lit-review-tool-staging
pip install -r requirements.txt
streamlit run lit_review_app.py        # → http://localhost:8501
```

Environment expects `NEON_DATABASE_URL`. Either set it in `.env` at the repo root (auto-loaded), or copy `secrets.toml.template` → `.streamlit/secrets.toml`.

## Deploy

See the [top-level README](../README.md#first-time-deploy-to-streamlit-community-cloud) for the full Streamlit Cloud setup.

Quick version:
- Main file path: `lit-review-tool-staging/lit_review_app.py`
- Secrets:
  ```toml
  NEON_DATABASE_URL = "postgresql://...pooler.eu-west-2.aws.neon.tech/neondb?sslmode=require"
  LIT_REVIEW_PASSWORD = "..."        # optional gate
  ANTHROPIC_API_KEY = "sk-ant-..."   # optional, enables ✨/🤔
  ```

## Seed from a .bib

`seed_from_bib.py` is still file-based — it produces a `sources.xlsx`. To get those rows into Neon afterwards:

```bash
python seed_from_bib.py refs.bib --project my-paper --create
python -c "
import db, pandas as pd
reg = db.load_registry()
project = db.get_project(reg, 'my-paper')
df = pd.read_excel('projects/my-paper/sources.xlsx', dtype=str).fillna('')
db.save_sources(project, df)
"
```

(Porting `seed_from_bib.py` to write directly to Neon is a small follow-up — see CLAUDE.md.)

## Data layout

All data in **Neon Postgres** (`public` schema, `lr_*` prefix):

| Table | Purpose |
|---|---|
| `lr_app_state` | singleton holding `active_project_id` |
| `lr_projects` | `{id, name}` — one row per paper |
| `lr_setup` | per-project: title, thesis, outline (jsonb), deadlines (jsonb), plans, default_tags, target_word_count, formatting_guidelines |
| `lr_sources` | the 17-column sources table (was `sources.xlsx`) |
| `lr_draft` | `(project_id, section_name) → draft_text` |
| `lr_scratchpad` | per-project free-form text |
| `lr_time_log` | per-project pomodoro session log |

Connection details live in `db.py`. See [CLAUDE.md](CLAUDE.md) for the full architecture rundown.

## Notes

- Single user — no auth beyond the optional password gate
- Designed for daily use from any browser; opening the same project in two tabs and editing concurrently will produce last-write-wins clobbering
- See [CLAUDE.md](CLAUDE.md) for context if you're handing this to Claude Code for further development
