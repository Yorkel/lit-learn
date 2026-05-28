# lit-review-tool

A single-user, multi-project Streamlit tool for literature review. Built for academic writers managing several papers in parallel.

Each **project** is one paper you're writing. The tool stores per-project data in `./projects/<project-id>/` in whatever directory you run it from — so you can keep each paper's lit-review data inside that paper's own repo.

## What's in it

- **Setup tab** — paper title, thesis, structured outline (sections + subsections with ✓ Written + reorder), deadlines, plans, target word count, formatting guidelines, paper list (bulk import from .bib or single add)
- **Review tab** — paper card, inline URL-add, tags input with ✨ LLM suggest, 📝 Summary section with ✨ LLM generate, three staging-buffer text boxes (Direct quotes / Notes to summarise / My thoughts) each with ✨ Clean, single ✅ Add to Excel button that appends + clears, ⭐ High importance flag
- **Draft tab** — section picker from your outline, draft text area with word-count progress, right pane = paper-notes picker + checklist of papers not yet incorporated
- **Compiled notes tab** — sortable / editable table of all sources
- **Sidebar** — project switcher, Pomodoro timer, scratchpad, 🤔 Explain-to-me (paste any text → plain-language explanation)

LLM features use Claude Haiku 4.5 and require `ANTHROPIC_API_KEY` in the env. They're optional — without the key, the buttons hide and the rest of the app works normally.

## Install

```bash
# Option A — clone + run from any folder
git clone https://github.com/<you>/lit-review-tool.git ~/lit-review-tool
pip install -r ~/lit-review-tool/requirements.txt

# Option B — pip install (editable) so you can `import lit_review_app`
pip install -e ~/lit-review-tool
```

## Run

```bash
# Inside whichever paper repo you're working on
cd ~/repos/my-paper
streamlit run ~/lit-review-tool/lit_review_app.py
```

The app creates `./projects/` + `./projects.json` in the cwd. So Paper A's lit data lives in Paper A's repo; Paper B's in Paper B's; etc.

In a GitHub Codespace, Streamlit will prompt you to open the forwarded port (default 8501). The port stays private to you.

## Seed from a .bib

```bash
cd ~/repos/my-paper
python ~/lit-review-tool/seed_from_bib.py refs.bib --project my-paper --create
```

This parses your `.bib` file, creates `./projects/my-paper/` if needed, and adds one row per entry to `sources.xlsx`. Existing keys are skipped.

## .env

Drop a `.env` in your paper repo (or its parent) with:

```
ANTHROPIC_API_KEY=sk-ant-...
```

The tool auto-loads `.env` from the cwd, the cwd's parent, then the tool install dir. Don't commit `.env` (it's in this repo's `.gitignore` already; add to your paper repo's too).

## Data layout

Inside each `./projects/<project-id>/`:

| File | Purpose |
|---|---|
| `setup.json` | paper title, thesis, structured outline, deadlines, plans, default tags, target word count, formatting guidelines |
| `sources.xlsx` | one row per cited paper (16 columns) |
| `draft.json` | draft prose keyed by section / subsection |
| `scratchpad.md` | free-form jottings, autosaved |
| `time_log.json` | Pomodoro session log |

`./projects.json` is the registry of projects in this folder, with which one is active.

## Updating the tool

```bash
cd ~/lit-review-tool && git pull
```

(or `pip install -U git+https://github.com/<you>/lit-review-tool.git` if you installed via pip.)

## Notes

- Single user, single machine — no auth, no cloud sync
- Designed for use inside a GitHub Codespace but works anywhere with Python 3.10+
- See [CLAUDE.md](CLAUDE.md) for context if you're handing this repo to Claude Code for further development
