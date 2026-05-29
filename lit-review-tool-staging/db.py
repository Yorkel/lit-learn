"""
db.py — Postgres (Neon) data layer for the Literature Review tool.

Replaces the per-project filesystem persistence (projects.json, setup.json,
sources.xlsx, draft.json, scratchpad.md, time_log.json) with a single Neon
database. Public function signatures match the originals so lit_review_app.py
only needs a one-line import change.

Connection URL comes from NEON_DATABASE_URL — set it in .env locally or in
the Streamlit Cloud secrets panel when deployed.
"""
import datetime
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import pandas as pd
import psycopg
from psycopg.types.json import Jsonb
import streamlit as st


SOURCE_COLS = [
    "key", "title", "authors", "year", "venue", "source_type", "category",
    "doi", "url", "abstract", "tags", "quotes", "notes", "thoughts", "summary",
    "status", "drafted", "flag", "flag_note",
]


# ── Connection plumbing ───────────────────────────────────────────────────────


def _read_dotenv():
    here = Path(__file__).parent
    for candidate in (here / ".env", here.parent / ".env", Path.cwd() / ".env"):
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
        try:
            url = st.secrets["NEON_DATABASE_URL"]
        except Exception:
            pass
    url = (url or "").strip()
    if not url:
        raise RuntimeError(
            "NEON_DATABASE_URL is not set. Add it to .env (local) or to the "
            "Streamlit Cloud secrets panel (deployed)."
        )
    return url


@contextmanager
def _conn():
    c = psycopg.connect(_url(), connect_timeout=15)
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


# ── Project registry (was projects.json) ──────────────────────────────────────


def load_registry() -> dict:
    """Return {active_project, projects: [{id, name, data_dir}]}."""
    with _conn() as c, c.cursor() as cur:
        cur.execute("select active_project_id from lr_app_state where id = 'global'")
        row = cur.fetchone()
        active = row[0] if row else None
        cur.execute("select id, name from lr_projects order by created_at, id")
        projects = [
            {"id": pid, "name": name, "data_dir": pid}  # data_dir kept for compat
            for pid, name in cur.fetchall()
        ]
    return {"active_project": active, "projects": projects}


def save_registry(reg: dict) -> None:
    """Persist active_project + the project list. (Projects themselves are managed by create_project / delete_project.)"""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "update lr_app_state set active_project_id = %s where id = 'global'",
            (reg.get("active_project"),),
        )


def get_project(reg: dict, pid: Optional[str]) -> Optional[dict]:
    if not pid:
        return None
    for p in reg.get("projects", []):
        if p["id"] == pid:
            return p
    return None


def project_data_dir(project: dict) -> Path:
    """Kept for compat. Not used for storage anymore — returns a notional path."""
    return Path(project.get("data_dir") or project["id"])


# ── Project setup (was setup.json) ────────────────────────────────────────────


def default_setup(title: str) -> dict:
    return {
        "title": title,
        "thesis": "",
        "outline": [],
        "deadlines": [],
        "plans": "",
        "default_tags": [],
        "target_word_count": 0,
        "formatting_guidelines": "",
    }


def _migrate_outline(out) -> list:
    """Same logic as the old load_setup: normalize legacy outline shapes + add stable IDs."""
    import uuid
    def _new_id() -> str:
        return uuid.uuid4().hex[:8]
    new_out = []
    for s in (out or []):
        if isinstance(s, str):
            new_out.append({"id": _new_id(), "title": s, "written": False, "subsections": []})
        elif isinstance(s, dict):
            sec = {
                "id": s.get("id") or _new_id(),
                "title": s.get("title", ""),
                "written": bool(s.get("written", False)),
                "subsections": [],
            }
            for sub in s.get("subsections", []) or []:
                if isinstance(sub, str):
                    sec["subsections"].append({"id": _new_id(), "title": sub, "written": False})
                elif isinstance(sub, dict):
                    sec["subsections"].append({
                        "id": sub.get("id") or _new_id(),
                        "title": sub.get("title", ""),
                        "written": bool(sub.get("written", False)),
                    })
            new_out.append(sec)
    return new_out


@st.cache_data(show_spinner=False)
def _cached_load_setup(project_id: str, project_name: str) -> dict:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "select title, thesis, outline, deadlines, plans, default_tags, "
            "       target_word_count, formatting_guidelines "
            "from lr_setup where project_id = %s",
            (project_id,),
        )
        row = cur.fetchone()
    if not row:
        return default_setup(project_name or "")
    title, thesis, outline, deadlines, plans, default_tags, target_wc, fmt = row
    return {
        "title": title or project_name or "",
        "thesis": thesis or "",
        "outline": _migrate_outline(outline),
        "deadlines": deadlines or [],
        "plans": plans or "",
        "default_tags": default_tags or [],
        "target_word_count": int(target_wc or 0),
        "formatting_guidelines": fmt or "",
    }


def load_setup(project: dict) -> dict:
    """Cached read of the project's setup. Cache is invalidated on save_setup."""
    return _cached_load_setup(project["id"], project.get("name", ""))


def save_setup(project: dict, setup: dict) -> None:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "insert into lr_setup (project_id, title, thesis, outline, deadlines, "
            "                      plans, default_tags, target_word_count, formatting_guidelines) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "on conflict (project_id) do update set "
            "  title = excluded.title, thesis = excluded.thesis, "
            "  outline = excluded.outline, deadlines = excluded.deadlines, "
            "  plans = excluded.plans, default_tags = excluded.default_tags, "
            "  target_word_count = excluded.target_word_count, "
            "  formatting_guidelines = excluded.formatting_guidelines",
            (
                project["id"],
                setup.get("title", ""),
                setup.get("thesis", ""),
                Jsonb(setup.get("outline", [])),
                Jsonb(setup.get("deadlines", [])),
                setup.get("plans", ""),
                Jsonb(setup.get("default_tags", [])),
                int(setup.get("target_word_count", 0) or 0),
                setup.get("formatting_guidelines", ""),
            ),
        )
    _cached_load_setup.clear()


# ── Project lifecycle ─────────────────────────────────────────────────────────


def create_project(reg: dict, name: str) -> dict:
    """Create a new project with a slugged id; return its row."""
    base = name.lower().strip().replace(" ", "-").replace("/", "-")
    base = "".join(c for c in base if c.isalnum() or c == "-") or "untitled"
    existing = {p["id"] for p in reg.get("projects", [])}
    pid, n = base, 1
    while pid in existing:
        n += 1
        pid = f"{base}-{n}"

    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "insert into lr_projects (id, name) values (%s, %s) "
            "on conflict (id) do nothing",
            (pid, name),
        )
    project = {"id": pid, "name": name, "data_dir": pid}
    save_setup(project, default_setup(name))
    save_draft(project, {})
    save_scratchpad(project, "")
    reg.setdefault("projects", []).append(project)
    return project


def rename_project(pid: str, new_name: str) -> None:
    """Change a project's display name. The id is immutable, so all its
    sources / setup / draft stay attached."""
    with _conn() as c, c.cursor() as cur:
        cur.execute("update lr_projects set name = %s where id = %s", (new_name, pid))


def delete_project(reg: dict, pid: str) -> None:
    """Remove a project and all its data."""
    with _conn() as c, c.cursor() as cur:
        cur.execute("delete from lr_projects where id = %s", (pid,))
    reg["projects"] = [p for p in reg.get("projects", []) if p["id"] != pid]
    if reg.get("active_project") == pid:
        reg["active_project"] = reg["projects"][0]["id"] if reg["projects"] else None
        save_registry(reg)
    # Wipe caches that referenced this project
    _cached_load_sources.clear()
    _cached_load_setup.clear()
    _cached_load_draft.clear()
    _cached_load_scratchpad.clear()


# ── Sources (was sources.xlsx) ────────────────────────────────────────────────


@st.cache_data(show_spinner=False)
def _cached_load_sources(project_id: str) -> pd.DataFrame:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "select " + ", ".join(SOURCE_COLS) + " from lr_sources "
            "where project_id = %s order by position, id",
            (project_id,),
        )
        rows = cur.fetchall()
    if not rows:
        return pd.DataFrame(columns=SOURCE_COLS)
    return pd.DataFrame(rows, columns=SOURCE_COLS).astype(str).fillna("")


def load_sources(project: dict) -> pd.DataFrame:
    """Cached read of the project's sources. Cache is invalidated on save."""
    # Return a copy so the caller can mutate without poisoning the cache
    return _cached_load_sources(project["id"]).copy()


def save_sources(project: dict, df: pd.DataFrame) -> None:
    """Persist the project's sources via a minimal diff against the current
    DB state — only touches rows that were added, removed, or changed.

    Previously this did DELETE-then-INSERT-all which made every per-cell
    edit cost ~40 round-trips for a project the size of llm-judge.
    """
    df = df.copy()
    for col in SOURCE_COLS:
        if col not in df.columns:
            df[col] = ""
    df = df[SOURCE_COLS].fillna("").astype(str)
    pid = project["id"]

    # Pull current rows from DB into a {key: dict} index
    current = {}
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            f"select id, position, {', '.join(SOURCE_COLS)} "
            f"from lr_sources where project_id = %s",
            (pid,),
        )
        for row in cur.fetchall():
            row_id, pos, *vals = row
            current[vals[0]] = {  # vals[0] is `key` since SOURCE_COLS[0] = 'key'
                "_id": row_id, "_pos": pos,
                **{col: (v or "") for col, v in zip(SOURCE_COLS, vals)},
            }

        new_keys = []
        for i, row in enumerate(df.itertuples(index=False)):
            row_dict = {col: getattr(row, col) for col in SOURCE_COLS}
            key = row_dict["key"]
            new_keys.append(key)

            if key not in current:
                # New row → INSERT
                cur.execute(
                    "insert into lr_sources (project_id, "
                    + ", ".join(SOURCE_COLS) + ", position) "
                    "values (%s, " + ", ".join(["%s"] * len(SOURCE_COLS)) + ", %s)",
                    [pid] + [row_dict[col] for col in SOURCE_COLS] + [i],
                )
                continue

            # Existing row → diff and UPDATE only changed columns (+ position)
            existing = current[key]
            changed_cols = [
                col for col in SOURCE_COLS
                if (row_dict[col] or "") != (existing[col] or "")
            ]
            if existing["_pos"] != i:
                changed_cols.append("position")
                row_dict["position"] = i
            if changed_cols:
                set_clause = ", ".join(f"{col} = %s" for col in changed_cols)
                values = [row_dict[col] for col in changed_cols] + [existing["_id"]]
                cur.execute(
                    f"update lr_sources set {set_clause} where id = %s",
                    values,
                )

        # Rows in DB but not in df → DELETE
        removed = set(current.keys()) - set(new_keys)
        if removed:
            cur.execute(
                "delete from lr_sources where project_id = %s and key = any(%s)",
                (pid, list(removed)),
            )
    _cached_load_sources.clear()


# ── Draft (was draft.json) ────────────────────────────────────────────────────


@st.cache_data(show_spinner=False)
def _cached_load_draft(project_id: str) -> dict:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "select section_name, draft_text from lr_draft where project_id = %s",
            (project_id,),
        )
        return {name: text for name, text in cur.fetchall()}


def load_draft(project: dict) -> dict:
    return dict(_cached_load_draft(project["id"]))


def save_draft(project: dict, draft: dict) -> None:
    with _conn() as c, c.cursor() as cur:
        cur.execute("delete from lr_draft where project_id = %s", (project["id"],))
        for name, text in (draft or {}).items():
            cur.execute(
                "insert into lr_draft (project_id, section_name, draft_text) "
                "values (%s, %s, %s)",
                (project["id"], name, text or ""),
            )
    _cached_load_draft.clear()


# ── Scratchpad (was scratchpad.md) ────────────────────────────────────────────


@st.cache_data(show_spinner=False)
def _cached_load_scratchpad(project_id: str) -> str:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "select content from lr_scratchpad where project_id = %s",
            (project_id,),
        )
        row = cur.fetchone()
    return row[0] if row else ""


def load_scratchpad(project: dict) -> str:
    return _cached_load_scratchpad(project["id"])


def save_scratchpad(project: dict, text: str) -> None:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "insert into lr_scratchpad (project_id, content) values (%s, %s) "
            "on conflict (project_id) do update set content = excluded.content",
            (project["id"], text or ""),
        )
    _cached_load_scratchpad.clear()


# ── Time log (was time_log.json) ──────────────────────────────────────────────


def load_time_log(project: dict) -> list:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "select log_date, minutes from lr_time_log "
            "where project_id = %s order by id",
            (project["id"],),
        )
        return [{"date": str(d), "minutes": float(m)} for d, m in cur.fetchall()]


def save_time_log(project: dict, log: list) -> None:
    """Replace the project's full time log (matches old JSON-file semantics)."""
    with _conn() as c, c.cursor() as cur:
        cur.execute("delete from lr_time_log where project_id = %s", (project["id"],))
        for entry in log or []:
            cur.execute(
                "insert into lr_time_log (project_id, log_date, minutes) "
                "values (%s, %s, %s)",
                (
                    project["id"],
                    entry.get("date") or str(datetime.date.today()),
                    entry.get("minutes", 0),
                ),
            )


def log_session(project: dict, minutes: float) -> None:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "insert into lr_time_log (project_id, log_date, minutes) "
            "values (%s, current_date, %s)",
            (project["id"], float(minutes)),
        )


def time_by_day(log: list) -> dict:
    """Same shape as the original (kept for compat)."""
    totals: dict = {}
    for entry in log:
        d = entry.get("date", "")
        totals[d] = totals.get(d, 0) + entry.get("minutes", 0)
    return totals


# ── Cross-project duplicate detection ─────────────────────────────────────────
#
# A paper (identified by bibtex key) can live in multiple projects, but each
# project gets its own row with its own notes. These helpers let the UI alert
# the user when they're re-adding a paper that's already been reviewed
# elsewhere, and copy across the prose fields (quotes / notes / thoughts /
# summary / tags) without disturbing per-project metadata (status / flag /
# drafted / position).

# Fields that represent the user's *thinking* about the paper — safe to copy
# between projects on request. Excludes metadata that's project-specific:
# status, flag, flag_note, drafted, position.
COPYABLE_REVIEW_FIELDS = ["quotes", "notes", "thoughts", "summary", "tags"]


def find_paper_in_other_projects(current_project_id: str, key: str) -> list[dict]:
    """Return a list of other projects that contain a source row with this bib key.

    Each dict has: {project_id, project_name, has_notes (bool), <fields...>}
    where the COPYABLE_REVIEW_FIELDS are included as keys. Excludes the
    current project. Empty list if the paper is unique.
    """
    if not key:
        return []
    cols = ", ".join(COPYABLE_REVIEW_FIELDS)
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            f"select s.project_id, p.name, {cols} "
            f"from lr_sources s join lr_projects p on p.id = s.project_id "
            f"where s.key = %s and s.project_id <> %s",
            (key, current_project_id),
        )
        rows = cur.fetchall()
    results = []
    for row in rows:
        pid, pname, *field_vals = row
        rec = {"project_id": pid, "project_name": pname}
        for field, val in zip(COPYABLE_REVIEW_FIELDS, field_vals):
            rec[field] = val or ""
        rec["has_notes"] = any(rec[f].strip() for f in COPYABLE_REVIEW_FIELDS)
        results.append(rec)
    return results


def copy_review_fields(src_project_id: str, dst_project_id: str, key: str,
                       fields: list | None = None) -> bool:
    """Copy review prose for a single paper from one project to another.

    Only the fields in COPYABLE_REVIEW_FIELDS (default) are touched. Status,
    flag, drafted, position are left alone on the destination row.
    Returns True if a row was updated, False if either side is missing.
    """
    fields = fields or COPYABLE_REVIEW_FIELDS
    fields = [f for f in fields if f in COPYABLE_REVIEW_FIELDS]
    if not fields:
        return False
    cols = ", ".join(fields)
    set_clause = ", ".join(f"{f} = src.{f}" for f in fields)
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            f"update lr_sources dst set {set_clause} "
            f"from lr_sources src "
            f"where dst.project_id = %s and dst.key = %s "
            f"  and src.project_id = %s and src.key = %s",
            (dst_project_id, key, src_project_id, key),
        )
        return cur.rowcount > 0
