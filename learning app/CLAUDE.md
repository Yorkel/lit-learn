# CLAUDE.md — Learning Notes App

Instructions for Claude Code to set up, run, and extend this app.

---

## What this is

A Streamlit learning notes app with two screens:

1. **Overview** — all modules listed, expandable to sections and topics, tick boxes, progress bars, add module/section/topic
2. **Topic** — full topic map on the left sidebar, rich text notes editor on the right, chat panel (if API key set), resources table tab

Data lives in `learning_notes_data.json`. Everything is local and private.

---

## First-time setup

```bash
# 1. Install dependencies
pip install -r requirements.txt --break-system-packages

# 2. Seed data from a course notes Word doc (run once per module)
python seed_from_docx.py 02_edukate_genai_llm_notes.docx \
  --title "Generative AI and Large Language Models" \
  --sub "Module 7 · L6 AI Engineer · Cambridge Spark"

# To add more modules, run again with a different docx:
python seed_from_docx.py another_module.docx --title "ML in Production"

# 3. Set secrets (optional but recommended)
cp .streamlit/secrets.toml.template .streamlit/secrets.toml
# Edit .streamlit/secrets.toml and fill in your values

# 4. Run
streamlit run app.py
```

---

## Environment variables / secrets

| Key | Purpose | Required? |
|-----|---------|-----------|
| `NOTES_PASSWORD` | Password gate for the app | No — leave blank for no gate |
| `ANTHROPIC_API_KEY` | Enables ✨ Clean up, Chat, Summarise | No — app works without it |
| `LEARNING_NOTES_DATA` | Custom path for data file | No — defaults to `learning_notes_data.json` |
| `LEARNING_NOTES_TIME` | Custom path for time log | No — defaults to `learning_notes_time.json` |

Set these either in `.streamlit/secrets.toml` (local) or in the Streamlit Community Cloud Secrets panel (deployed).

---

## Deploying to Streamlit Community Cloud (live, password-protected)

1. Push this repo to GitHub (public or private)
2. Go to [share.streamlit.io](https://share.streamlit.io) → New app
3. Point at your repo, branch `main`, file `app.py`
4. Click **Advanced settings → Secrets** and paste:
   ```toml
   NOTES_PASSWORD = "your-password-here"
   ANTHROPIC_API_KEY = "sk-ant-..."
   ```
5. Deploy — you get a permanent URL to bookmark

**Important:** On Community Cloud, `learning_notes_data.json` must be committed to the repo for the app to find it on startup. Changes you make in the app write to the running container but won't persist after a restart. To preserve your notes, commit the data file periodically:
```bash
git add learning_notes_data.json
git commit -m "update notes"
git push
```

For fully persistent storage without manual commits, replace the file I/O in `app.py` with Supabase (free tier) — ask Claude Code to implement this if needed.

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
seed_from_docx.py               One-time seeder from Word doc outline table
requirements.txt                Python dependencies
learning_notes_data.json        Your notes data (commit this)
learning_notes_time.json        Pomodoro session log (gitignored)
mockup_v7.html                  Approved design reference — open in browser
.streamlit/
  config.toml                   Streamlit theme config
  secrets.toml.template         Template — copy and fill in
  secrets.toml                  Your real secrets — NEVER commit
.gitignore
CLAUDE.md                       This file
```

---

## Key functions to know

| Function | File | What it does |
|----------|------|-------------|
| `load_data()` / `save_data()` | app.py | Read/write the JSON data file |
| `overview()` | app.py | Renders the overview screen |
| `topic_screen()` | app.py | Renders the topic detail screen |
| `pomodoro()` | app.py | Renders the Pomodoro timer (called from sidebar) |
| `llm_clean()` | app.py | Calls Claude API to clean up pasted notes |
| `llm_chat()` | app.py | Calls Claude API for topic chat |
| `seed_from_docx.py` | — | Parses Word doc outline table → JSON |

---

## Common tasks for Claude Code

**Add a new module manually (no docx):**
The app has an "Add module" expander in the sidebar on the overview screen. Use that.

**Rename a module/section/topic:**
Edit `learning_notes_data.json` directly and restart the app, or add an edit UI — ask Claude Code to add inline rename inputs.

**Add persistent storage (Supabase):**
Replace `load_data()`/`save_data()` with Supabase client calls. The data schema is already JSON-compatible.

**Export all notes to Word doc:**
Ask Claude Code to add a function using `python-docx` that iterates all modules, sections, topics and writes their `notes_html` content to a `.docx` file.

**Add a new LLM feature:**
Follow the pattern in `llm_clean()` — call `anthropic.Anthropic().messages.create()` and handle the response. All LLM features are gated behind `if HAS_LLM`.
