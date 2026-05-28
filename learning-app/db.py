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


def _ensure_course(cur):
    cur.execute(
        "insert into ln_courses (id, title, subtitle) values (%s, %s, %s) "
        "on conflict (id) do nothing",
        (COURSE_ID, "My Learning Notes", ""),
    )


# ── Public API: mirrors the old JSON file helpers ─────────────────────────────


@st.cache_data(show_spinner=False)
def _cached_load_data() -> dict:
    """Cached implementation. Cleared by save_data() and save_time().
    Returns the immutable cached object — callers should deep-copy if mutating."""
    return _load_data_uncached()


def load_data() -> dict:
    """Return the same nested dict the old load_data() returned.
    Always returns a fresh deep copy so the caller can mutate freely without
    corrupting the cached object."""
    import copy
    return copy.deepcopy(_cached_load_data())


def _load_data_uncached() -> dict:
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


def _resources_equal(a: list, b: list) -> bool:
    """Cheap deep-compare for a topic's resources list."""
    if len(a) != len(b):
        return False
    keys = ("title", "type", "authors", "url", "reviewed")
    for ra, rb in zip(a, b):
        for k in keys:
            if (ra.get(k) or "") != (rb.get(k) or "") and not (
                k == "reviewed" and bool(ra.get(k)) == bool(rb.get(k))
            ):
                return False
    return True


def save_data(data: dict) -> None:
    """Persist the nested tree via a minimal diff against current DB state.

    Only touches modules/sections/topics whose fields or positions actually
    changed, plus topics whose resources list changed. Was: full-tree
    DELETE-then-INSERT which made every checkbox click cost ~20-40 round
    trips on a project the size of Module 9.
    """
    with _conn() as c, c.cursor() as cur:
        _ensure_course(cur)
        cur.execute("select title, subtitle from ln_courses where id = %s", (COURSE_ID,))
        cur_title, cur_sub = cur.fetchone()
        new_title = data.get("course_title", "")
        new_sub = data.get("course_sub", "")
        if cur_title != new_title or cur_sub != new_sub:
            cur.execute(
                "update ln_courses set title = %s, subtitle = %s where id = %s",
                (new_title, new_sub, COURSE_ID),
            )

        # Load current module tree
        cur.execute(
            "select id, title, position from ln_modules where course_id = %s",
            (COURSE_ID,),
        )
        cur_modules = {r[0]: {"title": r[1], "position": r[2]} for r in cur.fetchall()}
        new_modules_by_id = {m["id"]: (mi, m) for mi, m in enumerate(data.get("modules", []))}

        # Load current sections/topics in one shot
        all_mod_ids = list(set(cur_modules.keys()) | set(new_modules_by_id.keys()))
        cur_sections = {}
        cur_topics = {}
        cur_resources_by_topic = {}
        if all_mod_ids:
            cur.execute(
                "select id, module_id, title, position from ln_sections "
                "where module_id = any(%s)",
                (all_mod_ids,),
            )
            cur_sections = {r[0]: {"module_id": r[1], "title": r[2], "position": r[3]}
                            for r in cur.fetchall()}

            sec_ids = list(cur_sections.keys())
            if sec_ids:
                cur.execute(
                    "select id, section_id, name, done, notes_html, position from ln_topics "
                    "where section_id = any(%s)",
                    (sec_ids,),
                )
                cur_topics = {r[0]: {"section_id": r[1], "name": r[2], "done": bool(r[3]),
                                      "notes_html": r[4] or "", "position": r[5]}
                              for r in cur.fetchall()}

                topic_ids = list(cur_topics.keys())
                if topic_ids:
                    cur.execute(
                        "select topic_id, title, type, authors, url, reviewed, position "
                        "from ln_resources where topic_id = any(%s) "
                        "order by topic_id, position, id",
                        (topic_ids,),
                    )
                    for tid, *vals in cur.fetchall():
                        cur_resources_by_topic.setdefault(tid, []).append({
                            "title": vals[0], "type": vals[1] or "", "authors": vals[2] or "",
                            "url": vals[3] or "", "reviewed": bool(vals[4]),
                        })

        # Diff modules
        for mid, (mi, mod) in new_modules_by_id.items():
            if mid not in cur_modules:
                cur.execute(
                    "insert into ln_modules (id, course_id, title, position) "
                    "values (%s, %s, %s, %s)",
                    (mid, COURSE_ID, mod["title"], mi),
                )
            else:
                cm = cur_modules[mid]
                changes = []
                if cm["title"] != mod["title"]:
                    changes.append(("title", mod["title"]))
                if cm["position"] != mi:
                    changes.append(("position", mi))
                if changes:
                    set_clause = ", ".join(f"{col} = %s" for col, _ in changes)
                    cur.execute(
                        f"update ln_modules set {set_clause} where id = %s",
                        [v for _, v in changes] + [mid],
                    )
        # Delete modules absent from new
        gone_mods = [m for m in cur_modules if m not in new_modules_by_id]
        if gone_mods:
            cur.execute("delete from ln_modules where id = any(%s)", (gone_mods,))

        # Diff sections
        new_sections_by_id = {}
        for mid, (_, mod) in new_modules_by_id.items():
            for si, sec in enumerate(mod.get("sections", [])):
                new_sections_by_id[sec["id"]] = (mid, si, sec)
        for sid, (mid, si, sec) in new_sections_by_id.items():
            if sid not in cur_sections:
                cur.execute(
                    "insert into ln_sections (id, module_id, title, position) "
                    "values (%s, %s, %s, %s)",
                    (sid, mid, sec["title"], si),
                )
            else:
                cs = cur_sections[sid]
                changes = []
                if cs["module_id"] != mid:
                    changes.append(("module_id", mid))
                if cs["title"] != sec["title"]:
                    changes.append(("title", sec["title"]))
                if cs["position"] != si:
                    changes.append(("position", si))
                if changes:
                    set_clause = ", ".join(f"{col} = %s" for col, _ in changes)
                    cur.execute(
                        f"update ln_sections set {set_clause} where id = %s",
                        [v for _, v in changes] + [sid],
                    )
        gone_secs = [s for s in cur_sections if s not in new_sections_by_id and cur_sections[s]["module_id"] not in gone_mods]
        if gone_secs:
            cur.execute("delete from ln_sections where id = any(%s)", (gone_secs,))

        # Diff topics
        new_topics_by_id = {}
        for sid, (_, _, sec) in new_sections_by_id.items():
            for ti, topic in enumerate(sec.get("topics", [])):
                new_topics_by_id[topic["id"]] = (sid, ti, topic)
        for tid, (sid, ti, topic) in new_topics_by_id.items():
            done = bool(topic.get("done", False))
            notes_html = topic.get("notes_html", "") or ""
            if tid not in cur_topics:
                cur.execute(
                    "insert into ln_topics "
                    "(id, section_id, name, done, notes_html, position) "
                    "values (%s, %s, %s, %s, %s, %s)",
                    (tid, sid, topic["name"], done, notes_html, ti),
                )
            else:
                ct = cur_topics[tid]
                changes = []
                if ct["section_id"] != sid:
                    changes.append(("section_id", sid))
                if ct["name"] != topic["name"]:
                    changes.append(("name", topic["name"]))
                if ct["done"] != done:
                    changes.append(("done", done))
                if ct["notes_html"] != notes_html:
                    changes.append(("notes_html", notes_html))
                if ct["position"] != ti:
                    changes.append(("position", ti))
                if changes:
                    set_clause = ", ".join(f"{col} = %s" for col, _ in changes)
                    cur.execute(
                        f"update ln_topics set {set_clause} where id = %s",
                        [v for _, v in changes] + [tid],
                    )
        gone_topics = [t for t in cur_topics if t not in new_topics_by_id and cur_topics[t]["section_id"] not in gone_secs]
        if gone_topics:
            cur.execute("delete from ln_topics where id = any(%s)", (gone_topics,))

        # Resources: per-topic replace only if list changed
        for tid, (_, _, topic) in new_topics_by_id.items():
            new_res = topic.get("resources", []) or []
            cur_res = cur_resources_by_topic.get(tid, [])
            if _resources_equal(cur_res, new_res):
                continue
            cur.execute("delete from ln_resources where topic_id = %s", (tid,))
            for ri, res in enumerate(new_res):
                cur.execute(
                    "insert into ln_resources "
                    "(topic_id, title, type, authors, url, reviewed, position) "
                    "values (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        tid, res.get("title", ""), res.get("type", ""),
                        res.get("authors", ""), res.get("url", ""),
                        bool(res.get("reviewed", False)), ri,
                    ),
                )
    _cached_load_data.clear()


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
