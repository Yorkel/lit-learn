# New-project prompt for the lit-review tool

**How to use:**
1. Open a Claude Code session inside the paper repo you want to import (the one with your draft, notes, PDFs, .bib file, etc.).
2. Paste the **fenced block below** as your first message — exactly as written, no edits needed.
3. Claude reads the repo and replies with a single JSON blob.
4. Save that JSON as `/workspaces/lit-learn/templates/import_<project-id>.json` in this codespace.
5. Run the import script:
   ```bash
   python /workspaces/lit-learn/templates/import_project.py templates/import_<project-id>.json
   ```
6. Reload the lit-review browser tab — new project appears in the sidebar with its setup, outline, draft prose, and scratchpad populated. (Sources come from the .bib separately — see end of this file.)

---

## The prompt — copy from here to the end of the fence, paste into the paper-repo Claude

```
I have a literature-review tool at github.com/yorkel/lit-learn.
Each project in that tool is one paper I'm writing. I want to populate
a project for THIS paper.

Read this repo and produce ONE JSON object with exactly the fields below
that I can import into the tool. Use the paper's draft, README, .bib file,
any notes/PDFs/markdown files, and recent commit messages as your sources.
Don't make things up — leave any field you can't infer as "" or [].

OUTPUT FORMAT — emit only this JSON object, no surrounding prose:

{
  "project_id": "<short-kebab-case slug, e.g. 'sst-llm-judge'>",
  "project_name": "<human readable name, e.g. 'SST LLM Judge'>",
  "setup": {
    "title": "<full paper title from the draft>",
    "thesis": "<one-sentence statement of the paper's main argument>",
    "outline": [
      {
        "title": "<top-level section name from the draft, e.g. 'Introduction'>",
        "written": <true if that section in the draft has substantive prose, else false>,
        "subsections": [
          {"title": "<subsection name>", "written": <bool>}
        ]
      }
    ],
    "deadlines": [
      {"label": "<e.g. 'EMNLP submission' or 'co-author review'>",
       "date_iso": "YYYY-MM-DD"}
    ],
    "plans": "<free-form: any TODO list or 'next steps' notes you find in the repo>",
    "default_tags": ["<3-6 lowercase topic tags this paper is about>"],
    "target_word_count": <integer; 8000 if you can't tell>,
    "formatting_guidelines": "<venue-specific style notes if any, else ''>"
  },
  "draft_sections": {
    "<section name exactly matching outline title>": "<existing draft prose for that section>",
    "<Parent > Child for subsections>": "<draft prose>"
  },
  "scratchpad": "<any free-form research notes, TODOs, or scratch text you find>",
  "bib_path": "<relative path to .bib file in this repo, or '' if none>"
}

RULES:
- Section names in `draft_sections` MUST exactly match `title` values in `outline`
  (or "Parent > Child" for subsections, e.g. "Introduction > Motivation").
- Output the JSON object only — no markdown fence, no explanation.
- If no .bib exists, set "bib_path": "".
- If no draft prose exists yet, set "draft_sections": {}.
```

---

## After you have the JSON

Save it under `templates/` and run:

```bash
python /workspaces/lit-learn/templates/import_project.py \
  /workspaces/lit-learn/templates/import_<project-id>.json
```

The script:
- Creates the project in Neon if it doesn't exist (or updates it if it does)
- Pushes setup / draft / scratchpad fields
- If `bib_path` is set, prints a follow-up command to seed sources from the .bib file

## Sources from .bib (separate step)

`seed_from_bib.py` still writes to an .xlsx. After running it, push that into Neon:

```bash
cd /workspaces/lit-learn/lit-review-tool-staging
python seed_from_bib.py /path/to/refs.bib --project <project-id> --create  # writes projects/<id>/sources.xlsx
python -c "
import db, pandas as pd
reg = db.load_registry()
p = db.get_project(reg, '<project-id>')
df = pd.read_excel('projects/<project-id>/sources.xlsx', dtype=str).fillna('')
db.save_sources(p, df)
print(f'imported {len(df)} sources into {p[\"id\"]}')
"
```
