# CLAUDE.md — Learning Notes App

Instructions for Claude Code to set up, run, and extend this app.

---

## What this is

A Streamlit learning notes app with two screens:

1. **Overview** — all modules listed, expandable to sections and topics, tick boxes, progress bars, add module/section/topic
2. **Topic** — full topic map on the left sidebar, rich text notes editor on the right, chat panel (if API key set), resources table tab

Data lives in **Neon Postgres** — see `db.py` for the data layer. Tables are prefixed `ln_*` and live in the `public` schema. Connection string comes from `NEON_DATABASE_URL` (env var or Streamlit secrets).

---

## First-time setup

```bash
# 1. Install dependencies
pip install -r requirements.txt --break-system-packages

# 2. Set NEON_DATABASE_URL — either in repo-root .env (auto-loaded) or in
#    .streamlit/secrets.toml. Copy the template to start:
cp secrets.toml.template .streamlit/secrets.toml
# Fill in NEON_DATABASE_URL, NOTES_PASSWORD (optional), ANTHROPIC_API_KEY (optional)

# 3. Run
streamlit run app.py
```

The schema has already been applied to the Neon database (see [../supabase/schema.sql](../supabase/schema.sql) — yes despite the folder name, the file is Neon-compatible after the Supabase pivot). If you ever recreate the database, re-run that SQL.

Optional one-off seeding from a Word doc still works via `seed_from_docx.py` — it writes to `learning_notes_data.json`, which you can then load into Neon via:
```bash
python -c "import db, json; db.save_data(json.load(open('learning_notes_data.json')))"
```

---

## Environment variables / secrets

| Key | Purpose | Required? |
|-----|---------|-----------|
| `NEON_DATABASE_URL` | Connection string to the Neon Postgres database | **Yes** |
| `NOTES_PASSWORD` | Password gate for the app | No — leave blank for no gate |
| `ANTHROPIC_API_KEY` | Enables ✨ Clean up, Chat, Summarise | No — app works without it |

Set these either in repo-root `.env` (local, auto-loaded by `db.py`), in `.streamlit/secrets.toml` (local Streamlit), or in the Streamlit Community Cloud Secrets panel (deployed).

---

## Deploying to Streamlit Community Cloud

1. Push this repo (`lit-learn`) to GitHub.
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app**.
3. Point at the repo, branch `main`, **Main file path** = `learning-app/app.py`.
4. **Advanced settings → Secrets** — paste:
   ```toml
   NEON_DATABASE_URL = "postgresql://...@ep-xxx-pooler.eu-west-2.aws.neon.tech/neondb?sslmode=require"
   NOTES_PASSWORD = "your-password-here"
   ANTHROPIC_API_KEY = "sk-ant-..."
   ```
5. Deploy — first build ~2 min, then you get a permanent URL.

Data persists in Neon, so container restarts don't lose anything. No more `git add learning_notes_data.json` dance.

---

## Rich text editor (known limitation)

Streamlit's native `st.text_area` does not support WYSIWYG formatting. The toolbar buttons in the mockup (`mockup_v7.html`) are shown for design reference.

To get a real rich text editor in Streamlit, Claude Code should install and integrate one of:

- **streamlit-quill** (`pip install streamlit-quill`) — Quill.js editor, supports H1/H2/H3/bold/italic/bullets/code
- **st-tiptap** — Tiptap-based editor

Example with streamlit-quill:
```python
from streamlit_quill import st_quill

content = st_quill(
    value=topic.get("notes_html", ""),
    html=True,
    key=f"quill_{topic['id']}"
)
if content is not None and content != topic.get("notes_html", ""):
    topic["notes_html"] = content
    save_data(data)
```

The `notes_html` field in the data schema already stores HTML, so swapping in a rich text component requires only replacing the `st.text_area` call in `topic_screen()`.

---

## File structure

```
app.py                          Main Streamlit app
db.py                           Neon (Postgres) data layer — load_data/save_data/log_pomo/etc.
seed_from_docx.py               One-off seeder (legacy: writes learning_notes_data.json)
requirements.txt                Python deps (streamlit, anthropic, psycopg, python-docx)
learning_notes_data.json        Historical seed; data now lives in Neon
mockup_v7.html                  Approved design reference — open in browser
secrets.toml.template           Template — copy to .streamlit/secrets.toml and fill in
.gitignore
CLAUDE.md                       This file
```

---

## Key functions to know

| Function | File | What it does |
|----------|------|-------------|
| `load_data()` / `save_data()` | db.py | Read/write the full nested data tree to Neon |
| `load_time()` / `log_pomo()` / `today_mins()` | db.py | Pomodoro session log (per-day totals) |
| `overview()` | app.py | Renders the overview screen |
| `topic_screen()` | app.py | Renders the topic detail screen |
| `pomodoro()` | app.py | Renders the Pomodoro timer (called from sidebar) |
| `llm_clean()` | app.py | Calls Claude API to clean up pasted notes |
| `llm_chat()` | app.py | Calls Claude API for topic chat |
| `seed_from_docx.py` | — | Parses Word doc outline table → JSON file (one-off) |

---

## Common tasks for Claude Code

**Add a new module manually (no docx):**
The app has an "Add module" expander in the sidebar on the overview screen. Use that.

**Rename a module/section/topic:**
Add an inline rename input to the UI — ask Claude Code. Direct database edits via Neon SQL editor are also fine.

**Export all notes to Word doc:**
Ask Claude Code to add a function using `python-docx` that iterates all modules, sections, topics and writes their `notes_html` content to a `.docx` file. The data layer already returns the full nested dict.

**Add a new LLM feature:**
Follow the pattern in `llm_clean()` — call `anthropic.Anthropic().messages.create()` and handle the response. All LLM features are gated behind `if HAS_LLM`.

**Optimize per-click latency:**
Wrap interactive panels (topic checkbox, notes textarea, chat panel) in `@st.fragment` so only that panel reruns on interaction instead of the whole script. Estimated ~2-3 hr pass to make the app feel snappy.
