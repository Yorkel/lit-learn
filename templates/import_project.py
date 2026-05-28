"""
import_project.py — push a project-import JSON file into the lit-review Neon DB.

Usage:
    python templates/import_project.py templates/import_<project-id>.json

Expects the JSON to match the schema described in templates/new_project_prompt.md.
Safe to re-run on the same file — uses upserts, no duplicates.
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lit-review-tool-staging"))

import db  # noqa: E402 — imported after sys.path tweak


def main(json_path: Path) -> None:
    data = json.loads(json_path.read_text())

    pid = data["project_id"]
    name = data.get("project_name") or pid

    # Create or find the project
    reg = db.load_registry()
    project = db.get_project(reg, pid)
    if not project:
        # create_project would slug the name; we want the exact pid the JSON specifies
        with db._conn() as c, c.cursor() as cur:
            cur.execute(
                "insert into lr_projects (id, name) values (%s, %s) "
                "on conflict (id) do update set name = excluded.name",
                (pid, name),
            )
        project = {"id": pid, "name": name, "data_dir": pid}
        print(f"created project {pid!r}")
    else:
        print(f"project {pid!r} already exists, will update")

    # Setup
    setup = data.get("setup") or {}
    db.save_setup(project, {
        "title": setup.get("title", ""),
        "thesis": setup.get("thesis", ""),
        "outline": setup.get("outline", []),
        "deadlines": setup.get("deadlines", []),
        "plans": setup.get("plans", ""),
        "default_tags": setup.get("default_tags", []),
        "target_word_count": int(setup.get("target_word_count", 0) or 0),
        "formatting_guidelines": setup.get("formatting_guidelines", ""),
    })
    print(f"  setup: {len(setup.get('outline', []))} sections, "
          f"{len(setup.get('deadlines', []))} deadlines, "
          f"{len(setup.get('default_tags', []))} tags")

    # Draft
    draft = data.get("draft_sections") or {}
    if draft:
        db.save_draft(project, draft)
        print(f"  draft: {len(draft)} sections")

    # Scratchpad
    scratch = data.get("scratchpad") or ""
    if scratch:
        db.save_scratchpad(project, scratch)
        print(f"  scratchpad: {len(scratch)} chars")

    bib = data.get("bib_path") or ""
    if bib:
        print()
        print("Next step — seed sources from the .bib file:")
        print(f"  cd {REPO_ROOT}/lit-review-tool-staging")
        print(f"  python seed_from_bib.py {bib} --project {pid} --create")
        print(f"  python -c \"")
        print(f"import db, pandas as pd")
        print(f"reg = db.load_registry()")
        print(f"p = db.get_project(reg, {pid!r})")
        print(f"df = pd.read_excel('projects/{pid}/sources.xlsx', dtype=str).fillna('')")
        print(f"db.save_sources(p, df)")
        print(f"print(f'imported {{len(df)}} sources')")
        print(f"\"")

    print()
    print(f"DONE — open lit-review tool and switch to project '{pid}'.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    main(Path(sys.argv[1]))
