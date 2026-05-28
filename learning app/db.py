"""
db.py — Postgres (Neon) data layer for the Learning Notes app.

Replaces the JSON-file persistence in app.py with a Neon-backed implementation.
Public API is identical to the old file-based helpers, so app.py needs only
a one-line import change.

Connection URL comes from NEON_DATABASE_URL: set it in .env locally, or in
.streamlit/secrets.toml / the Streamlit Cloud secrets panel when deployed.
"""
import datetime
import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
import streamlit as st


COURSE_ID = "main"  # singleton course; the app's UI assumes one course


def _read_dotenv():
    """Load .env into os.environ (no python-dotenv dependency)."""
    here = Path(__file__).parent
    for candidate in (here / ".env", here.parent / ".env"):
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


def _ensure_course(cur):
    cur.execute(
        "insert into ln_courses (id, title, subtitle) values (%s, %s, %s) "
        "on conflict (id) do nothing",
        (COURSE_ID, "My Learning Notes", ""),
    )


# ── Public API: mirrors the old JSON file helpers ─────────────────────────────


def load_data() -> dict:
    """Return the same nested dict the old load_data() returned."""
    with _conn() as c, c.cursor() as cur:
        _ensure_course(cur)

        cur.execute("select title, subtitle from ln_courses where id = %s", (COURSE_ID,))
        course_title, course_sub = cur.fetchone()

        cur.execute(
            "select id, title from ln_modules where course_id = %s "
            "order by position, created_at",
            (COURSE_ID,),
        )
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
        for sid, mid, title in cur.fetchall():
            sec = {"id": sid, "title": title, "topics": []}
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
                topic = {
                    "id": tid,
                    "name": name,
                    "done": bool(done),
                    "notes_html": notes_html or "",
                    "resources": [],
                }
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
                        "title": title,
                        "type": rtype or "",
                        "authors": authors or "",
                        "url": url or "",
                        "reviewed": bool(reviewed),
                    })

    return {"course_title": course_title, "course_sub": course_sub, "modules": modules}


def save_data(data: dict) -> None:
    """Write the full nested dict back to the database in one transaction."""
    with _conn() as c, c.cursor() as cur:
        _ensure_course(cur)
        cur.execute(
            "update ln_courses set title = %s, subtitle = %s where id = %s",
            (data.get("course_title", ""), data.get("course_sub", ""), COURSE_ID),
        )
        # Wipe the module tree and reinsert. Cascades drop sections/topics/resources.
        cur.execute("delete from ln_modules where course_id = %s", (COURSE_ID,))
        for mi, mod in enumerate(data.get("modules", [])):
            cur.execute(
                "insert into ln_modules (id, course_id, title, position) "
                "values (%s, %s, %s, %s)",
                (mod["id"], COURSE_ID, mod["title"], mi),
            )
            for si, sec in enumerate(mod.get("sections", [])):
                cur.execute(
                    "insert into ln_sections (id, module_id, title, position) "
                    "values (%s, %s, %s, %s)",
                    (sec["id"], mod["id"], sec["title"], si),
                )
                for ti, topic in enumerate(sec.get("topics", [])):
                    cur.execute(
                        "insert into ln_topics "
                        "(id, section_id, name, done, notes_html, position) "
                        "values (%s, %s, %s, %s, %s, %s)",
                        (
                            topic["id"], sec["id"], topic["name"],
                            bool(topic.get("done", False)),
                            topic.get("notes_html", ""), ti,
                        ),
                    )
                    for ri, res in enumerate(topic.get("resources", []) or []):
                        cur.execute(
                            "insert into ln_resources "
                            "(topic_id, title, type, authors, url, reviewed, position) "
                            "values (%s, %s, %s, %s, %s, %s, %s)",
                            (
                                topic["id"], res.get("title", ""), res.get("type", ""),
                                res.get("authors", ""), res.get("url", ""),
                                bool(res.get("reviewed", False)), ri,
                            ),
                        )


def load_time() -> list:
    with _conn() as c, c.cursor() as cur:
        cur.execute("select log_date, minutes from ln_time_log order by id")
        return [{"date": str(d), "minutes": float(m)} for d, m in cur.fetchall()]


def save_time(log: list) -> None:
    """Replace the full time log (matches old JSON-file semantics)."""
    with _conn() as c, c.cursor() as cur:
        cur.execute("delete from ln_time_log")
        for entry in log:
            cur.execute(
                "insert into ln_time_log (log_date, minutes) values (%s, %s)",
                (entry.get("date") or str(datetime.date.today()), entry.get("minutes", 0)),
            )


def log_pomo(mins: float) -> None:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "insert into ln_time_log (log_date, minutes) values (current_date, %s)",
            (float(mins),),
        )


def today_mins() -> int:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "select coalesce(sum(minutes), 0) from ln_time_log where log_date = current_date"
        )
        return int(cur.fetchone()[0])
