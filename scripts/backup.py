"""
backup.py — dump everything in Neon to human-readable files in ./backups/.

Run manually:
    python scripts/backup.py

Or run from a GitHub Actions cron (see .github/workflows/backup.yml).

Output layout (always overwrites the previous backup — git history is the
versioning):

    backups/
    ├── learning/
    │   ├── learning_notes_data.json     full module/section/topic tree
    │   └── learning_notes_time.json     pomodoro log
    └── lit-review/
        ├── <project-id>/
        │   ├── setup.json               title, thesis, outline, deadlines, …
        │   ├── sources.xlsx             the 17-column sources table
        │   ├── draft.json               { section_name: draft_text }
        │   ├── scratchpad.md            free-form notes
        │   └── time_log.json            pomodoro log for this project
        └── <another-project-id>/
            └── …

Reads NEON_DATABASE_URL from env (or repo-root .env). Safe to commit the
backups/ folder — these are your notes, not credentials.
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "learning app"))
sys.path.insert(0, str(REPO_ROOT / "lit-review-tool-staging"))

# These two imports are deliberately separate — each app's db.py is its own
# module. We import them under aliases so we can talk to both DBs in one run.
import importlib.util


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ln_db = _load("ln_db", REPO_ROOT / "learning app" / "db.py")
lr_db = _load("lr_db", REPO_ROOT / "lit-review-tool-staging" / "db.py")


def backup_learning(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    data = ln_db.load_data()
    (out / "learning_notes_data.json").write_text(json.dumps(data, indent=2))
    (out / "learning_notes_time.json").write_text(json.dumps(ln_db.load_time(), indent=2))
    mods = len(data.get("modules", []))
    print(f"  learning notes: course={data.get('course_title')!r}, {mods} modules")


def backup_lit_review(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    reg = lr_db.load_registry()
    projects = reg.get("projects", [])
    print(f"  lit-review: {len(projects)} project(s)")
    for project in projects:
        pid = project["id"]
        pdir = out / pid
        pdir.mkdir(parents=True, exist_ok=True)

        setup = lr_db.load_setup(project)
        (pdir / "setup.json").write_text(json.dumps(setup, indent=2))

        sources_df = lr_db.load_sources(project)
        sources_df.to_excel(pdir / "sources.xlsx", index=False)

        draft = lr_db.load_draft(project)
        (pdir / "draft.json").write_text(json.dumps(draft, indent=2))

        (pdir / "scratchpad.md").write_text(lr_db.load_scratchpad(project))

        time_log = lr_db.load_time_log(project)
        (pdir / "time_log.json").write_text(json.dumps(time_log, indent=2))

        print(f"    {pid}: {len(sources_df)} sources, "
              f"{len(setup.get('outline', []))} outline sections, "
              f"{len(draft)} drafted sections, "
              f"{len(time_log)} pomo sessions")


def main() -> None:
    backups = REPO_ROOT / "backups"
    print(f"Backing up Neon → {backups}/")
    backup_learning(backups / "learning")
    backup_lit_review(backups / "lit-review")
    print("DONE")


if __name__ == "__main__":
    main()
