"""
import_module.py — push a new-module JSON file into the Learning Notes Neon DB.

Usage:
    python templates/import_module.py templates/import_module_<slug>.json

Appends the module to the existing course tree. If a module with the same
title already exists, it's REPLACED (so re-running with an updated JSON
overwrites only that module, not unrelated ones).

Expects the JSON shape produced by templates/new_module_prompt.md:
{
  "module_title": "...",
  "sections": [{"title": "...", "topics": [{"name": "...", ...}]}]
}
"""
import json
import random
import string
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "learning app"))

import db  # noqa: E402


def _uid() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def _build_module(payload: dict) -> dict:
    """Convert the JSON-from-prompt shape into the app's internal module shape."""
    mod = {
        "id": _uid(),
        "title": payload["module_title"],
        "sections": [],
    }
    for s_in in payload.get("sections", []):
        sec = {
            "id": _uid(),
            "title": s_in.get("title", ""),
            "topics": [],
        }
        for t_in in s_in.get("topics", []):
            topic = {
                "id": _uid(),
                "name": t_in.get("name", ""),
                "done": False,
                "notes_html": t_in.get("starter_notes", "") or "",
                "resources": [
                    {
                        "title": r.get("title", ""),
                        "type": r.get("type", ""),
                        "authors": r.get("authors", ""),
                        "url": r.get("url", ""),
                        "reviewed": False,
                    }
                    for r in (t_in.get("resources") or [])
                ],
            }
            sec["topics"].append(topic)
        mod["sections"].append(sec)
    return mod


def main(json_path: Path) -> None:
    payload = json.loads(json_path.read_text())
    title = payload["module_title"]
    new_mod = _build_module(payload)

    data = db.load_data()
    existing = [m for m in data["modules"] if m["title"] == title]

    if existing:
        # Replace the existing module by title (preserve its id so links don't break)
        new_mod["id"] = existing[0]["id"]
        data["modules"] = [
            new_mod if m["title"] == title else m
            for m in data["modules"]
        ]
        verb = "replaced"
    else:
        data["modules"].append(new_mod)
        verb = "added"

    db.save_data(data)

    n_sec = len(new_mod["sections"])
    n_top = sum(len(s["topics"]) for s in new_mod["sections"])
    n_res = sum(len(t["resources"]) for s in new_mod["sections"] for t in s["topics"])
    print(f"{verb} module {title!r}: {n_sec} sections, {n_top} topics, {n_res} resources")
    print(f"Open the Learning Notes app and the module is at the bottom of the list.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    main(Path(sys.argv[1]))
