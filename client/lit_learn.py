"""
lit_learn.py — drop-in client to read/write the lit-learn Neon database
                from any other repo (paper repos, portfolio repos, anywhere).

Setup in the target repo:
    1. Copy this file to the repo root (or into a `tools/` folder).
    2. Make sure NEON_DATABASE_URL is set — in `.env`, in shell env, or
       passed explicitly to functions that need it.
    3. Install: pip install "psycopg[binary]>=3.2.0" pandas openpyxl

Quick examples (run from any repo with NEON_DATABASE_URL set):

    import lit_learn as ll

    # --- Lit-review ---
    ll.lr_projects()                          # list all paper projects
    df = ll.lr_sources("llm-judge")           # 17-column DataFrame of all sources
    df.to_excel("backup.xlsx", index=False)   # download a snapshot

    ll.lr_add_source("llm-judge",
        key="smith2026", title="A New Paper",
        authors="Smith et al.", year="2026", tags="evaluation, llm")

    ll.lr_update_source("llm-judge", "smith2026",
        status="reviewed", flag="⭐", flag_note="key precursor")

    ll.lr_get_setup("llm-judge")              # dict with title/thesis/outline/...
    ll.lr_save_draft_section("llm-judge", "Introduction", "Draft prose here...")
    ll.lr_get_draft("llm-judge")              # {section: text, ...}

    # --- Learning notes ---
    data = ll.ln_load()                       # full nested course→modules→sections→topics
    ll.ln_mark_topic_done(topic_id, True)
    ll.ln_append_module_from_payload({...})   # same JSON shape as the import UI

The functions intentionally cover the read / add / edit / download operations
described in the websites' UIs. If you need something niche, drop into raw
SQL via lit_learn.conn() — yields a psycopg connection in a `with` block.
"""
from __future__ import annotations

import json
import os
import random
import string
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    import psycopg
except ImportError as e:
    raise SystemExit(
        'lit_learn needs psycopg. Run: pip install "psycopg[binary]>=3.2.0" pandas openpyxl'
    ) from e

try:
    from psycopg.types.json import Jsonb
except ImportError:
    Jsonb = None  # type: ignore

try:
    import pandas as pd  # type: ignore
except ImportError:
    pd = None  # type: ignore


# ── Connection plumbing ───────────────────────────────────────────────────────

SOURCE_COLS = [
    "key", "title", "authors", "year", "venue", "source_type",
    "doi", "url", "tags", "quotes", "notes", "thoughts", "summary",
    "status", "drafted", "flag", "flag_note",
]


def _read_dotenv() -> None:
    """Load .env in cwd / cwd-parent / this file's dir into os.environ (no override)."""
    here = Path(__file__).resolve().parent
    for candidate in (Path.cwd() / ".env", Path.cwd().parent / ".env", here / ".env"):
        if not candidate.exists():
            continue
        for line in candidate.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


_read_dotenv()


def _url() -> str:
    url = os.environ.get("NEON_DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "NEON_DATABASE_URL is not set. Add it to .env in this repo "
            "(or shell env) before calling lit_learn functions."
        )
    return url


@contextmanager
def conn():
    """Open a Neon connection. Commits on clean exit, rolls back on error."""
    c = psycopg.connect(_url(), connect_timeout=15)
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def _uid() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


# ── LIT-REVIEW ────────────────────────────────────────────────────────────────


def lr_projects() -> list[dict]:
    """List all paper projects: [{id, name}, ...]."""
    with conn() as c, c.cursor() as cur:
        cur.execute("select id, name from lr_projects order by created_at, id")
        return [{"id": pid, "name": name} for pid, name in cur.fetchall()]


def lr_active_project() -> Optional[str]:
    with conn() as c, c.cursor() as cur:
        cur.execute("select active_project_id from lr_app_state where id = 'global'")
        row = cur.fetchone()
        return row[0] if row else None


def lr_set_active_project(project_id: str) -> None:
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "update lr_app_state set active_project_id = %s where id = 'global'",
            (project_id,),
        )


def lr_create_project(project_id: str, name: Optional[str] = None) -> None:
    """Create a new project. Idempotent — re-running just updates the name."""
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "insert into lr_projects (id, name) values (%s, %s) "
            "on conflict (id) do update set name = excluded.name",
            (project_id, name or project_id),
        )


def lr_sources(project_id: str):
    """Return the project's sources as a pandas DataFrame (17 columns)."""
    if pd is None:
        raise RuntimeError("pandas is required for lr_sources(). pip install pandas openpyxl")
    with conn() as c, c.cursor() as cur:
        cur.execute(
            f"select {', '.join(SOURCE_COLS)} from lr_sources "
            "where project_id = %s order by position, id",
            (project_id,),
        )
        rows = cur.fetchall()
    if not rows:
        return pd.DataFrame(columns=SOURCE_COLS)
    return pd.DataFrame(rows, columns=SOURCE_COLS).astype(str).fillna("")


def lr_add_source(project_id: str, **fields: str) -> int:
    """Append a source row. Returns its DB id. Unknown column names are ignored."""
    row = {c: "" for c in SOURCE_COLS}
    row.update({k: v for k, v in fields.items() if k in SOURCE_COLS})
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "select coalesce(max(position), -1) + 1 from lr_sources where project_id = %s",
            (project_id,),
        )
        pos = cur.fetchone()[0]
        cur.execute(
            "insert into lr_sources (project_id, " + ", ".join(SOURCE_COLS) + ", position) "
            "values (%s, " + ", ".join(["%s"] * len(SOURCE_COLS)) + ", %s) returning id",
            [project_id] + [row[c] for c in SOURCE_COLS] + [pos],
        )
        return cur.fetchone()[0]


def lr_update_source(project_id: str, key: str, **updates: str) -> int:
    """Update a source by its bibtex `key`. Returns the number of rows updated."""
    updates = {k: v for k, v in updates.items() if k in SOURCE_COLS}
    if not updates:
        return 0
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    with conn() as c, c.cursor() as cur:
        cur.execute(
            f"update lr_sources set {set_clause} where project_id = %s and key = %s",
            list(updates.values()) + [project_id, key],
        )
        return cur.rowcount


def lr_delete_source(project_id: str, key: str) -> int:
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "delete from lr_sources where project_id = %s and key = %s",
            (project_id, key),
        )
        return cur.rowcount


def lr_get_setup(project_id: str) -> dict:
    """Return the project's Setup-tab fields as a dict."""
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "select title, thesis, outline, deadlines, plans, default_tags, "
            "       target_word_count, formatting_guidelines "
            "from lr_setup where project_id = %s",
            (project_id,),
        )
        row = cur.fetchone()
    if not row:
        return {}
    title, thesis, outline, deadlines, plans, default_tags, twc, fmt = row
    return {
        "title": title or "",
        "thesis": thesis or "",
        "outline": outline or [],
        "deadlines": deadlines or [],
        "plans": plans or "",
        "default_tags": default_tags or [],
        "target_word_count": int(twc or 0),
        "formatting_guidelines": fmt or "",
    }


def lr_save_setup(project_id: str, **fields) -> None:
    """Patch the Setup-tab fields. Unknown keys are ignored. Only supplied keys are touched."""
    current = lr_get_setup(project_id) or {
        "title": "", "thesis": "", "outline": [], "deadlines": [], "plans": "",
        "default_tags": [], "target_word_count": 0, "formatting_guidelines": "",
    }
    current.update({k: v for k, v in fields.items() if k in current})

    if Jsonb is None:
        raise RuntimeError("psycopg.types.json.Jsonb not available — upgrade psycopg")

    with conn() as c, c.cursor() as cur:
        cur.execute(
            "insert into lr_setup (project_id, title, thesis, outline, deadlines, "
            "  plans, default_tags, target_word_count, formatting_guidelines) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "on conflict (project_id) do update set "
            "  title = excluded.title, thesis = excluded.thesis, "
            "  outline = excluded.outline, deadlines = excluded.deadlines, "
            "  plans = excluded.plans, default_tags = excluded.default_tags, "
            "  target_word_count = excluded.target_word_count, "
            "  formatting_guidelines = excluded.formatting_guidelines",
            (
                project_id,
                current["title"], current["thesis"],
                Jsonb(current["outline"]), Jsonb(current["deadlines"]),
                current["plans"], Jsonb(current["default_tags"]),
                int(current["target_word_count"] or 0),
                current["formatting_guidelines"],
            ),
        )


def lr_get_draft(project_id: str) -> dict:
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "select section_name, draft_text from lr_draft where project_id = %s",
            (project_id,),
        )
        return {name: text for name, text in cur.fetchall()}


def lr_save_draft_section(project_id: str, section_name: str, text: str) -> None:
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "insert into lr_draft (project_id, section_name, draft_text) "
            "values (%s, %s, %s) "
            "on conflict (project_id, section_name) do update set draft_text = excluded.draft_text",
            (project_id, section_name, text or ""),
        )


def lr_get_scratchpad(project_id: str) -> str:
    with conn() as c, c.cursor() as cur:
        cur.execute("select content from lr_scratchpad where project_id = %s", (project_id,))
        row = cur.fetchone()
    return row[0] if row else ""


def lr_save_scratchpad(project_id: str, text: str) -> None:
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "insert into lr_scratchpad (project_id, content) values (%s, %s) "
            "on conflict (project_id) do update set content = excluded.content",
            (project_id, text or ""),
        )


def lr_download_project(project_id: str, out_dir: str | Path) -> Path:
    """Snapshot a project to ./out_dir/<project_id>/ as setup.json + sources.xlsx + draft.json + scratchpad.md.
    Returns the path to the project folder."""
    out = Path(out_dir) / project_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "setup.json").write_text(json.dumps(lr_get_setup(project_id), indent=2))
    df = lr_sources(project_id)
    df.to_excel(out / "sources.xlsx", index=False)
    (out / "draft.json").write_text(json.dumps(lr_get_draft(project_id), indent=2))
    (out / "scratchpad.md").write_text(lr_get_scratchpad(project_id))
    return out


# ── LEARNING NOTES ────────────────────────────────────────────────────────────


def ln_load() -> dict:
    """Return the full nested {course_title, course_sub, modules: [...]}."""
    with conn() as c, c.cursor() as cur:
        cur.execute("select title, subtitle from ln_courses where id = 'main'")
        row = cur.fetchone()
        if not row:
            return {"course_title": "", "course_sub": "", "modules": []}
        course_title, course_sub = row

        cur.execute("select id, title from ln_modules where course_id = 'main' order by position, created_at")
        modules = [{"id": r[0], "title": r[1], "sections": []} for r in cur.fetchall()]
        if not modules:
            return {"course_title": course_title, "course_sub": course_sub, "modules": []}

        mod_by_id = {m["id"]: m for m in modules}
        cur.execute(
            "select id, module_id, title from ln_sections "
            "where module_id = any(%s) order by module_id, position, created_at",
            ([m["id"] for m in modules],),
        )
        sections = []
        for sid, mid, st_title in cur.fetchall():
            sec = {"id": sid, "title": st_title, "topics": []}
            mod_by_id[mid]["sections"].append(sec)
            sections.append(sec)

        if sections:
            sec_by_id = {s["id"]: s for s in sections}
            cur.execute(
                "select id, section_id, name, done, notes_html from ln_topics "
                "where section_id = any(%s) order by section_id, position, created_at",
                ([s["id"] for s in sections],),
            )
            topics = []
            for tid, secid, name, done, notes_html in cur.fetchall():
                topic = {"id": tid, "name": name, "done": bool(done),
                         "notes_html": notes_html or "", "resources": []}
                sec_by_id[secid]["topics"].append(topic)
                topics.append(topic)

            if topics:
                topic_by_id = {t["id"]: t for t in topics}
                cur.execute(
                    "select topic_id, title, type, authors, url, reviewed from ln_resources "
                    "where topic_id = any(%s) order by topic_id, position, created_at",
                    ([t["id"] for t in topics],),
                )
                for tid, title, rtype, authors, url, reviewed in cur.fetchall():
                    topic_by_id[tid]["resources"].append({
                        "title": title, "type": rtype or "", "authors": authors or "",
                        "url": url or "", "reviewed": bool(reviewed),
                    })

    return {"course_title": course_title, "course_sub": course_sub, "modules": modules}


def ln_mark_topic_done(topic_id: str, done: bool = True) -> None:
    with conn() as c, c.cursor() as cur:
        cur.execute("update ln_topics set done = %s where id = %s", (bool(done), topic_id))


def ln_set_topic_notes(topic_id: str, notes_html: str) -> None:
    with conn() as c, c.cursor() as cur:
        cur.execute("update ln_topics set notes_html = %s where id = %s", (notes_html or "", topic_id))


def ln_add_resource(topic_id: str, title: str, type: str = "", authors: str = "", url: str = "") -> None:
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "select coalesce(max(position), -1) + 1 from ln_resources where topic_id = %s",
            (topic_id,),
        )
        pos = cur.fetchone()[0]
        cur.execute(
            "insert into ln_resources (topic_id, title, type, authors, url, reviewed, position) "
            "values (%s, %s, %s, %s, %s, false, %s)",
            (topic_id, title, type, authors, url, pos),
        )


def ln_append_module_from_payload(payload: dict) -> tuple[int, int]:
    """Same JSON shape as the in-app Import UI: {module_title, sections: [{title, topics: [...]}]}.
    Appends to the existing modules; replaces by title if duplicate.
    Returns (n_sections, n_topics)."""
    data = ln_load()
    mod = {"id": _uid(), "title": payload.get("module_title", "Imported module"), "sections": []}
    for s_in in payload.get("sections", []) or []:
        sec = {"id": _uid(), "title": s_in.get("title", ""), "topics": []}
        for t_in in s_in.get("topics", []) or []:
            sec["topics"].append({
                "id": _uid(),
                "name": t_in.get("name", ""),
                "done": False,
                "notes_html": t_in.get("starter_notes", "") or "",
                "resources": [
                    {"title": r.get("title", ""), "type": r.get("type", ""),
                     "authors": r.get("authors", ""), "url": r.get("url", ""),
                     "reviewed": False}
                    for r in (t_in.get("resources") or [])
                ],
            })
        mod["sections"].append(sec)
    same = [i for i, m in enumerate(data["modules"]) if m["title"] == mod["title"]]
    if same:
        mod["id"] = data["modules"][same[0]]["id"]
        data["modules"][same[0]] = mod
    else:
        data["modules"].append(mod)
    _ln_save(data)
    return len(mod["sections"]), sum(len(s["topics"]) for s in mod["sections"])


def ln_download(path: str | Path) -> Path:
    """Snapshot all learning notes to a single JSON file. Returns the path."""
    p = Path(path)
    p.write_text(json.dumps(ln_load(), indent=2))
    return p


def _ln_save(data: dict) -> None:
    """Persist the full nested learning-notes tree (used by ln_append_module_from_payload).
    Full-replace semantics matching the app's save_data()."""
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "insert into ln_courses (id, title, subtitle) values ('main', %s, %s) "
            "on conflict (id) do update set title = excluded.title, subtitle = excluded.subtitle",
            (data.get("course_title", ""), data.get("course_sub", "")),
        )
        cur.execute("delete from ln_modules where course_id = 'main'")
        for mi, mod in enumerate(data.get("modules", [])):
            cur.execute(
                "insert into ln_modules (id, course_id, title, position) values (%s, 'main', %s, %s)",
                (mod["id"], mod["title"], mi),
            )
            for si, sec in enumerate(mod.get("sections", [])):
                cur.execute(
                    "insert into ln_sections (id, module_id, title, position) values (%s, %s, %s, %s)",
                    (sec["id"], mod["id"], sec["title"], si),
                )
                for ti, topic in enumerate(sec.get("topics", [])):
                    cur.execute(
                        "insert into ln_topics (id, section_id, name, done, notes_html, position) "
                        "values (%s, %s, %s, %s, %s, %s)",
                        (topic["id"], sec["id"], topic["name"],
                         bool(topic.get("done", False)), topic.get("notes_html", ""), ti),
                    )
                    for ri, res in enumerate(topic.get("resources", []) or []):
                        cur.execute(
                            "insert into ln_resources (topic_id, title, type, authors, url, reviewed, position) "
                            "values (%s, %s, %s, %s, %s, %s, %s)",
                            (topic["id"], res.get("title", ""), res.get("type", ""),
                             res.get("authors", ""), res.get("url", ""),
                             bool(res.get("reviewed", False)), ri),
                        )
