# Cross-repo client for lit-learn

A single-file Python client (`lit_learn.py`) you can drop into any other repo to read / add / edit / download data in the live lit-learn database. The same database that backs the deployed Streamlit websites.

## Setup (one-time, in any repo)

```bash
# 1. Copy the client into the repo (root or e.g. tools/)
cp /workspaces/lit-learn/client/lit_learn.py /path/to/other-repo/

# 2. Make sure NEON_DATABASE_URL is set in that repo. Either:
echo 'NEON_DATABASE_URL="postgresql://...pooler.eu-west-2.aws.neon.tech/neondb?sslmode=require"' \
  >> /path/to/other-repo/.env
# (and make sure .env is in that repo's .gitignore)

# 3. Install deps in the other repo
pip install "psycopg[binary]>=3.2.0" pandas openpyxl
```

## Usage from Claude in that other repo

After dropping `lit_learn.py` in, you can ask Claude things like:

> "Add a new source to the `llm-judge` lit-review project using `lit_learn.py`. Key `kim2026`, title `Pairwise judge calibration`, authors `Kim et al.`, year `2026`, tags `judge, calibration`."

> "Download the current sources for `llm-judge` into `./backup/sources.xlsx`."

> "Look at my evaluation work in this repo, find any paper I cite that's not in the lit-learn `llm-judge` project, and add it as a new source using `lit_learn.py`."

> "I just learned a concept that goes under the existing 'LLM Evaluation' module. Mark its topic 'Pairwise vs pointwise judge designs' as done, and append my notes from `notes/calibration.md` to its `notes_html`."

Claude will read `lit_learn.py` (the docstring + signatures are self-documenting) and write the right calls. You don't need to memorise the API.

## Quick examples (Python)

```python
import lit_learn as ll

# --- Lit-review ---
ll.lr_projects()
# [{'id': 'llm-judge', 'name': 'llm-judge'}, ...]

df = ll.lr_sources("llm-judge")            # 17-column DataFrame
df.to_excel("snapshot.xlsx", index=False)

ll.lr_add_source("llm-judge",
    key="smith2026",
    title="A New Relevant Paper",
    authors="Smith et al.",
    year="2026",
    tags="evaluation, llm-judge",
    notes="found via google scholar 2026-05-28")

ll.lr_update_source("llm-judge", "smith2026",
    status="reviewed",
    flag="⭐",
    flag_note="key precursor")

ll.lr_save_draft_section("llm-judge", "Introduction",
    open("draft/intro.md").read())

ll.lr_download_project("llm-judge", "backup/")  # snapshot to backup/llm-judge/

# --- Learning notes ---
data = ll.ln_load()                       # full course tree
ll.ln_mark_topic_done("abc12345", True)
ll.ln_set_topic_notes("abc12345", "<p>updated HTML notes</p>")
ll.ln_add_resource("abc12345",
    title="Constitutional AI", type="paper", authors="Bai et al.",
    url="https://arxiv.org/abs/2212.08073")

# Bulk-import a module from a JSON payload (same shape as the website's Import UI)
ll.ln_append_module_from_payload({
    "module_title": "AI Engineer Distinction Prep",
    "sections": [
        {"title": "Eval & LLM-as-Judge",
         "topics": [
            {"name": "Pairwise vs pointwise",
             "starter_notes": "...",
             "resources": [{"title": "...", "type": "paper", "authors": "...", "url": "..."}]},
         ]},
    ],
})

ll.ln_download("learning_snapshot.json")
```

## What's available

See `lit_learn.py` — every public function has a docstring. High-level:

| Domain | Functions |
|---|---|
| Lit-review projects | `lr_projects`, `lr_create_project`, `lr_active_project`, `lr_set_active_project` |
| Lit-review sources | `lr_sources` (DataFrame), `lr_add_source`, `lr_update_source`, `lr_delete_source` |
| Lit-review setup/draft | `lr_get_setup`, `lr_save_setup`, `lr_get_draft`, `lr_save_draft_section`, `lr_get_scratchpad`, `lr_save_scratchpad` |
| Lit-review download | `lr_download_project(project_id, out_dir)` — writes setup.json + sources.xlsx + draft.json + scratchpad.md |
| Learning notes | `ln_load`, `ln_mark_topic_done`, `ln_set_topic_notes`, `ln_add_resource`, `ln_append_module_from_payload`, `ln_download` |
| Raw SQL | `conn()` context manager — for anything not covered above |

## Security note

`NEON_DATABASE_URL` is the **database admin** credential. Anyone with it can read or destroy all data. So:

- **Never commit** `.env` (or the URL anywhere git-tracked).
- The credential is the same one your live Streamlit websites use. If you ever suspect leak, rotate via Neon dashboard → Roles → reset password, then update `.env` here + your two Streamlit Cloud Secrets panels.
- For a read-only consumer (e.g. an analytics notebook you want to share), create a separate read-only Postgres role in Neon and use a different connection string.
