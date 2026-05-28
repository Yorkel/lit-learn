"""
seed_from_bib.py
----------------
Seed a project's sources.xlsx from a .bib file. Existing keys are preserved
— only new ones are added.

Data lives in the current working directory: ./projects/<project-id>/.
Run from inside a paper repo so the lit-review data lives alongside the paper.

Usage:
    cd ~/repos/my-paper
    python /path/to/lit-review-tool/seed_from_bib.py custom.bib --project my-paper
    python /path/to/lit-review-tool/seed_from_bib.py custom.bib --project new-paper --create
"""

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

DATA_DIR = Path.cwd().resolve()  # data lives in the cwd you run this from
REGISTRY_FILE = DATA_DIR / "projects.json"
PROJECTS_DIR = DATA_DIR / "projects"

SOURCE_COLS = [
    "key", "title", "authors", "year", "venue", "source_type",
    "doi", "url", "tags", "quotes", "notes", "thoughts",
    "status", "flag", "flag_note",
]


def load_registry() -> dict:
    if REGISTRY_FILE.exists():
        with open(REGISTRY_FILE) as f:
            return json.load(f)
    return {"active_project": None, "projects": []}


def save_registry(reg: dict):
    REGISTRY_FILE.write_text(json.dumps(reg, indent=2))


def get_project(reg: dict, pid: str):
    for p in reg.get("projects", []):
        if p["id"] == pid:
            return p
    return None


def parse_bib(bib_path: str) -> list[dict]:
    text = Path(bib_path).read_text(encoding="utf-8", errors="replace")
    entries = []
    blocks = re.split(r"(?=@\w+\{)", text)
    for block in blocks:
        block = block.strip()
        if not block.startswith("@"):
            continue
        m = re.match(r"@(\w+)\{([^,]+),", block)
        if not m:
            continue
        entry_type = m.group(1).lower()
        if entry_type in ("string", "preamble", "comment"):
            continue
        key = m.group(2).strip()

        def get_field(name: str) -> str:
            pat = rf'\b{name}\s*=\s*(?:\{{(.*?)\}}|"(.*?)"|(\w+))'
            fm = re.search(pat, block, re.DOTALL | re.IGNORECASE)
            if not fm:
                return ""
            val = fm.group(1) or fm.group(2) or fm.group(3) or ""
            val = re.sub(r"\{([^}]*)\}", r"\1", val)
            val = re.sub(r"\s+", " ", val).strip()
            return val

        authors_raw = get_field("author")
        if authors_raw:
            parts = [a.strip() for a in re.split(r"\s+and\s+", authors_raw, flags=re.IGNORECASE)]
            short = []
            for p in parts:
                last = p.split(",")[0].strip() if "," in p else p.split()[-1]
                short.append(last)
            authors = ", ".join(short)
            if len(parts) > 2:
                authors = short[0] + " et al."
        else:
            authors = ""

        venue = (
            get_field("journal")
            or get_field("booktitle")
            or get_field("publisher")
            or ""
        )

        # Map common bib entry types to our source_type vocabulary
        source_type_map = {
            "article": "journal",
            "inproceedings": "conference",
            "conference": "conference",
            "incollection": "book",
            "book": "book",
            "phdthesis": "thesis",
            "mastersthesis": "thesis",
            "techreport": "report",
            "misc": "preprint",
            "unpublished": "preprint",
        }
        source_type = source_type_map.get(entry_type, "")
        # If venue mentions arxiv, override to preprint
        if "arxiv" in venue.lower():
            source_type = "preprint"

        entry = {
            "key":         key,
            "title":       get_field("title"),
            "authors":     authors,
            "year":        get_field("year"),
            "venue":       venue,
            "source_type": source_type,
            "doi":         get_field("doi"),
            "url":         get_field("url") or get_field("doi"),
            "tags":        "",
            "quotes":      "",
            "notes":       "",
            "thoughts":    "",
            "status":      "not_started",
            "flag":        "",
            "flag_note":   "",
        }
        entries.append(entry)
    return entries


def default_setup(name: str) -> dict:
    return {
        "title": name,
        "thesis": "",
        "outline": [],
        "deadlines": [],
        "plans": "",
        "default_tags": [],
    }


def ensure_project(reg: dict, pid: str, create: bool) -> dict:
    proj = get_project(reg, pid)
    if proj:
        return proj
    if not create:
        print(f"Error: project '{pid}' not found. Use --create to create it.")
        print(f"Existing projects: {[p['id'] for p in reg.get('projects', [])]}")
        sys.exit(1)

    data_dir = PROJECTS_DIR / pid
    data_dir.mkdir(parents=True, exist_ok=True)
    proj = {
        "id": pid,
        "name": pid.replace("-", " ").title(),
        "data_dir": str(data_dir.relative_to(DATA_DIR)),
    }
    reg.setdefault("projects", []).append(proj)
    if not reg.get("active_project"):
        reg["active_project"] = pid

    (data_dir / "setup.json").write_text(json.dumps(default_setup(proj["name"]), indent=2))
    (data_dir / "draft.json").write_text("{}")
    (data_dir / "scratchpad.md").write_text("")
    (data_dir / "time_log.json").write_text("[]")
    pd.DataFrame(columns=SOURCE_COLS).to_excel(data_dir / "sources.xlsx", index=False)

    save_registry(reg)
    print(f"Created project '{pid}' at {data_dir.relative_to(DATA_DIR)}")
    return proj


def seed_sources(proj: dict, entries: list[dict]):
    data_dir = DATA_DIR / proj["data_dir"]
    sources_path = data_dir / "sources.xlsx"
    if sources_path.exists():
        existing = pd.read_excel(sources_path, dtype=str).fillna("")
        for c in SOURCE_COLS:
            if c not in existing.columns:
                existing[c] = ""
        existing = existing[SOURCE_COLS]
        existing_keys = set(existing["key"].tolist())
    else:
        existing = pd.DataFrame(columns=SOURCE_COLS)
        existing_keys = set()

    new_rows = [e for e in entries if e["key"] not in existing_keys]
    if not new_rows:
        print("No new entries — all keys already exist in sources.xlsx.")
        return

    new_df = pd.DataFrame(new_rows, columns=SOURCE_COLS)
    combined = pd.concat([existing, new_df], ignore_index=True)
    combined.to_excel(sources_path, index=False)
    print(f"Added {len(new_rows)} new sources → {sources_path.relative_to(DATA_DIR)}")
    print("New keys:", [r["key"] for r in new_rows])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed a project's sources.xlsx from a .bib file")
    parser.add_argument("bib", help="Path to .bib file")
    parser.add_argument("--project", required=True, help="Project ID to seed into")
    parser.add_argument("--create", action="store_true", help="Create the project if it doesn't exist")
    args = parser.parse_args()

    if not Path(args.bib).exists():
        print(f"Error: {args.bib} not found")
        sys.exit(1)

    reg = load_registry()
    proj = ensure_project(reg, args.project, create=args.create)
    entries = parse_bib(args.bib)
    print(f"Parsed {len(entries)} entries from {args.bib}")
    seed_sources(proj, entries)
