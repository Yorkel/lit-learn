"""
lit_review_app.py
-----------------
Multi-project literature review tool. Each project = one paper you're writing.

Data lives in the current working directory: ./projects/<project-id>/.
Run the app from inside a paper repo so each paper's lit-review data lives
alongside the paper:

    cd ~/repos/my-paper
    streamlit run /path/to/lit-review-tool/lit_review_app.py

Inside ./projects/<project-id>/:
  setup.json      paper title, thesis, structured outline, deadlines,
                  plans, default tags, target_word_count, formatting_guidelines
  sources.xlsx    one row per cited paper
  draft.json      draft prose keyed by section (or "Section > Subsection")
  scratchpad.md   per-project free-form scratchpad
  time_log.json   pomodoro sessions

LLM features (Clean, Suggest tags, Generate summary, Explain) are enabled
when ANTHROPIC_API_KEY is set — read from ./env, ./../.env, or the tool dir's
.env on startup.
"""

import datetime
import io
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

# ── Config ────────────────────────────────────────────────────────────────────

APP_DIR = Path(__file__).parent.resolve()  # tool install location (for .env fallback)
DATA_DIR = Path.cwd().resolve()             # where projects.json + projects/ live (defaults to cwd)
REGISTRY_FILE = DATA_DIR / "projects.json"
PROJECTS_DIR = DATA_DIR / "projects"

POMODORO_MINUTES = 25
LLM_CLEAN_MODEL = "claude-haiku-4-5-20251001"


def _load_dotenv():
    """Read .env files from cwd, cwd's parent, and the tool install dir."""
    for env_path in [DATA_DIR / ".env", DATA_DIR.parent / ".env", APP_DIR / ".env"]:
        if not env_path.exists():
            continue
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


_load_dotenv()
HAS_API_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))

SOURCE_TYPES = [
    "", "journal", "conference", "workshop", "preprint",
    "blog", "book", "thesis", "talk", "report", "other",
]
STATUS_OPTIONS = ["not_started", "partial", "reviewed"]
STATUS_LABELS = {
    "not_started": "Not started",
    "partial": "Partial",
    "reviewed": "Reviewed",
}

SOURCE_COLS = [
    "key", "title", "authors", "year", "venue", "source_type",
    "doi", "url", "tags", "quotes", "notes", "thoughts", "summary",
    "status", "drafted", "flag", "flag_note",
]

# ── Project registry ──────────────────────────────────────────────────────────


def load_registry() -> dict:
    if REGISTRY_FILE.exists():
        with open(REGISTRY_FILE) as f:
            return json.load(f)
    return {"active_project": None, "projects": []}


def save_registry(reg: dict):
    REGISTRY_FILE.write_text(json.dumps(reg, indent=2))


def get_project(reg: dict, pid: Optional[str]) -> Optional[dict]:
    if not pid:
        return None
    for p in reg.get("projects", []):
        if p["id"] == pid:
            return p
    return None


def project_data_dir(project: dict) -> Path:
    d = Path(project["data_dir"])
    if not d.is_absolute():
        d = DATA_DIR / d
    return d


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


def create_project(reg: dict, name: str) -> dict:
    pid = name.lower().strip().replace(" ", "-").replace("/", "-")
    pid = "".join(c for c in pid if c.isalnum() or c == "-")
    if not pid:
        pid = "untitled"
    existing = {p["id"] for p in reg.get("projects", [])}
    base = pid
    n = 1
    while pid in existing:
        n += 1
        pid = f"{base}-{n}"
    data_dir = PROJECTS_DIR / pid
    data_dir.mkdir(parents=True, exist_ok=True)
    project = {
        "id": pid,
        "name": name,
        "data_dir": str(data_dir.relative_to(DATA_DIR)),
    }
    reg.setdefault("projects", []).append(project)
    save_setup(project, default_setup(name))
    save_sources(project, pd.DataFrame(columns=SOURCE_COLS))
    save_draft(project, {})
    (data_dir / "scratchpad.md").write_text("")
    (data_dir / "time_log.json").write_text("[]")
    return project


# ── Project data I/O ─────────────────────────────────────────────────────────


def load_setup(project: dict) -> dict:
    p = project_data_dir(project) / "setup.json"
    if p.exists():
        with open(p) as f:
            data = json.load(f)
        merged = default_setup(project.get("name", ""))
        merged.update(data)
        # Migrate outline: flat string list → structured; add stable IDs
        out = merged.get("outline", [])
        new_out = []
        for s in out:
            if isinstance(s, str):
                sec = {"id": _new_id(), "title": s, "written": False, "subsections": []}
            elif isinstance(s, dict):
                sec = {
                    "id": s.get("id") or _new_id(),
                    "title": s.get("title", ""),
                    "written": bool(s.get("written", False)),
                    "subsections": [],
                }
                for sub in s.get("subsections", []):
                    if isinstance(sub, str):
                        sec["subsections"].append({"id": _new_id(), "title": sub, "written": False})
                    elif isinstance(sub, dict):
                        sec["subsections"].append({
                            "id": sub.get("id") or _new_id(),
                            "title": sub.get("title", ""),
                            "written": bool(sub.get("written", False)),
                        })
            else:
                continue
            new_out.append(sec)
        merged["outline"] = new_out
        return merged
    return default_setup(project.get("name", ""))


def _new_id() -> str:
    import uuid
    return uuid.uuid4().hex[:8]


def save_setup(project: dict, setup: dict):
    p = project_data_dir(project) / "setup.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(setup, indent=2))


def load_sources(project: dict) -> pd.DataFrame:
    p = project_data_dir(project) / "sources.xlsx"
    if p.exists():
        df = pd.read_excel(p, dtype=str).fillna("")
        for c in SOURCE_COLS:
            if c not in df.columns:
                df[c] = ""
        return df[SOURCE_COLS]
    return pd.DataFrame(columns=SOURCE_COLS)


def save_sources(project: dict, df: pd.DataFrame):
    p = project_data_dir(project) / "sources.xlsx"
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(p, index=False)


def load_draft(project: dict) -> dict:
    p = project_data_dir(project) / "draft.json"
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return {}


def save_draft(project: dict, draft: dict):
    p = project_data_dir(project) / "draft.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(draft, indent=2))


def load_scratchpad(project: dict) -> str:
    p = project_data_dir(project) / "scratchpad.md"
    return p.read_text() if p.exists() else ""


def save_scratchpad(project: dict, text: str):
    p = project_data_dir(project) / "scratchpad.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def load_time_log(project: dict) -> list:
    p = project_data_dir(project) / "time_log.json"
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return []


def save_time_log(project: dict, log: list):
    p = project_data_dir(project) / "time_log.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(log, indent=2))


def log_session(project: dict, minutes: float):
    log = load_time_log(project)
    log.append({"date": str(datetime.date.today()), "minutes": minutes})
    save_time_log(project, log)


def time_by_day(log: list) -> dict:
    totals: dict = {}
    for entry in log:
        d = entry.get("date", "")
        totals[d] = totals.get(d, 0) + entry.get("minutes", 0)
    return totals


# ── Source helpers ───────────────────────────────────────────────────────────


def row_to_dict(df: pd.DataFrame, key: str) -> dict:
    rows = df[df["key"] == key]
    if rows.empty:
        return {c: "" for c in SOURCE_COLS}
    return rows.iloc[0].to_dict()


def update_row(df: pd.DataFrame, key: str, updates: dict) -> pd.DataFrame:
    idx = df.index[df["key"] == key]
    if len(idx) == 0:
        new_row = {c: "" for c in SOURCE_COLS}
        new_row["key"] = key
        new_row.update(updates)
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    else:
        for k, v in updates.items():
            df.at[idx[0], k] = v
    return df


def parse_tags(tag_str: str) -> list:
    return [t.strip() for t in (tag_str or "").split(",") if t.strip()]


def format_tags(tags: list) -> str:
    return ", ".join(t.strip() for t in tags if t.strip())


def all_tags(df: pd.DataFrame) -> list:
    seen = set()
    for t in df["tags"].fillna(""):
        for tag in parse_tags(t):
            seen.add(tag)
    return sorted(seen)


def paper_index_of(df: pd.DataFrame, key: str) -> int:
    """1-based row index of a paper in sources.xlsx."""
    idx = df.index[df["key"] == key]
    return int(idx[0]) + 1 if len(idx) else 0


# ── Display helpers (title-case, paper label, date formats) ──────────────────


_LOWER_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "if",
    "in", "into", "of", "on", "or", "the", "to", "vs", "via", "with",
}
_KEEP_UPPER = {
    "AI", "LLM", "LLMS", "LLM-AS-A-JUDGE", "LLM-AS-JUDGE", "GPT", "NLP", "ML",
    "RAG", "FMRI", "RL", "RLHF", "RLAIF", "MT", "QA", "API", "CI", "SDK",
    "JSON", "XML", "HTML", "URL", "DOI", "PDF", "HELM", "TACL", "ACL", "EMNLP",
    "NEURIPS", "ICLR", "ICML", "FACCT", "SIGIR", "EACL", "NAACL", "OS", "IO",
}


def smart_title_case(s: str) -> str:
    """Title-case with acronyms preserved and short connectors kept lowercase."""
    if not s:
        return s
    words = s.split()
    out = []
    for i, raw in enumerate(words):
        m = re.match(r"^(.*?)([.,:;!?)\]'\"]*)$", raw)
        core, tail = (m.group(1), m.group(2)) if m else (raw, "")
        upper = core.upper()
        if upper in _KEEP_UPPER:
            out.append(upper + tail)
            continue
        lc = core.lower()
        if i > 0 and lc in _LOWER_WORDS:
            out.append(lc + tail)
            continue
        parts = re.split(r"([-/:])", core)
        cased = []
        for p in parts:
            if p in "-/:":
                cased.append(p)
            elif p.upper() in _KEEP_UPPER:
                cased.append(p.upper())
            else:
                cased.append(p[:1].upper() + p[1:].lower() if p else p)
        out.append("".join(cased) + tail)
    if out:
        first_core = re.match(r"^(.*?)([.,:;!?)\]'\"]*)$", out[0])
        head, tail = (first_core.group(1), first_core.group(2)) if first_core else (out[0], "")
        if head and head[0].islower() and head.upper() not in _KEEP_UPPER:
            out[0] = head[:1].upper() + head[1:] + tail
    return " ".join(out)


def paper_label(row: dict, max_chars: int = 70, index: Optional[int] = None) -> str:
    """`NN - Authors (Year) — Title` for use in pickers / lists."""
    authors = (row.get("authors") or "").strip()
    year = (row.get("year") or "").strip()
    title = (row.get("title") or "").strip()
    title_disp = smart_title_case(title) if title else ""
    head = ""
    if authors and year:
        head = f"{authors} ({year})"
    elif authors:
        head = authors
    elif year:
        head = f"({year})"
    if head and title_disp:
        s = f"{head} — {title_disp}"
    elif head or title_disp:
        s = head or title_disp
    else:
        s = row.get("key", "") or "(untitled)"
    if index is not None:
        prefix = f"{index:02d} - "
        s = prefix + s
    if len(s) > max_chars:
        s = s[: max_chars - 1] + "…"
    return s


def to_dmy(iso: str) -> str:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", (iso or "").strip())
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return iso or ""


def to_iso(dmy: str) -> str:
    m = re.match(r"^(\d{2})-(\d{2})-(\d{4})$", (dmy or "").strip())
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return dmy or ""


# ── Outline helpers ──────────────────────────────────────────────────────────


def flatten_outline(outline: list) -> list[str]:
    """Convert structured outline to flat list of 'Section' / 'Section > Subsection'."""
    out = []
    for sec in outline:
        if not isinstance(sec, dict):
            if isinstance(sec, str) and sec:
                out.append(sec)
            continue
        t = sec.get("title", "")
        if not t:
            continue
        out.append(t)
        for sub in sec.get("subsections", []):
            if isinstance(sub, dict):
                st_t = sub.get("title", "")
                if st_t:
                    out.append(f"{t} > {st_t}")
            elif isinstance(sub, str) and sub:
                out.append(f"{t} > {sub}")
    return out


# ── LLM helper (optional) ────────────────────────────────────────────────────


def llm_clean(raw: str) -> str:
    from anthropic import Anthropic
    client = Anthropic()
    resp = client.messages.create(
        model=LLM_CLEAN_MODEL,
        max_tokens=4000,
        messages=[{
            "role": "user",
            "content": (
                "Clean this pasted text from a PDF or web source. "
                "Fix line-break artefacts, join words broken across line breaks, "
                "remove page-header / footer noise, fix hyphenation. "
                "Preserve all substantive content. Do not paraphrase. "
                "Return only the cleaned text — no preamble.\n\n"
                f"{raw}"
            ),
        }],
    )
    return resp.content[0].text.strip()


def llm_suggest_tags(paper_meta: dict, quotes: str, notes: str, thoughts: str, thesis: str) -> str:
    """Ask LLM for 3-6 short topic tags. Returns comma-separated string."""
    from anthropic import Anthropic
    client = Anthropic()
    parts = []
    if paper_meta.get("title"):
        parts.append(f"Title: {paper_meta['title']}")
    if paper_meta.get("authors"):
        parts.append(f"Authors: {paper_meta['authors']}")
    if paper_meta.get("year"):
        parts.append(f"Year: {paper_meta['year']}")
    if paper_meta.get("venue"):
        parts.append(f"Venue: {paper_meta['venue']}")
    if quotes:
        parts.append(f"Direct quotes:\n{quotes[:2000]}")
    if notes:
        parts.append(f"Notes:\n{notes[:2000]}")
    if thoughts:
        parts.append(f"My thoughts:\n{thoughts[:2000]}")
    paper_block = "\n\n".join(parts) or "(no metadata)"
    prompt = (
        f"My research paper's thesis: {thesis or '(not set)'}\n\n"
        "I'm tagging a source paper in my literature review. Suggest 3 to 6 short "
        "topic tags for it, comma-separated, lowercase, no period at end. "
        "Tags should describe the *subject matter* (e.g. 'specification sensitivity', "
        "'judge bias', 'multiverse analysis') — not generic labels like 'AI' or 'research'. "
        "Return only the comma-separated list. No preamble.\n\n"
        f"Source paper:\n{paper_block}"
    )
    resp = client.messages.create(
        model=LLM_CLEAN_MODEL,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip().strip(".")


def llm_summarise_paper(paper_meta: dict, quotes: str, notes: str, thoughts: str, thesis: str) -> str:
    """Two-paragraph summary: (1) what the paper is about, (2) relevance to user's paper."""
    from anthropic import Anthropic
    client = Anthropic()
    parts = []
    if paper_meta.get("title"):
        parts.append(f"Title: {paper_meta['title']}")
    if paper_meta.get("authors"):
        parts.append(f"Authors: {paper_meta['authors']}")
    if paper_meta.get("year"):
        parts.append(f"Year: {paper_meta['year']}")
    if paper_meta.get("venue"):
        parts.append(f"Venue: {paper_meta['venue']}")
    if quotes:
        parts.append(f"Direct quotes:\n{quotes[:4000]}")
    if notes:
        parts.append(f"My notes:\n{notes[:4000]}")
    if thoughts:
        parts.append(f"My thoughts:\n{thoughts[:4000]}")
    paper_block = "\n\n".join(parts) or "(no content yet)"
    prompt = (
        f"My research paper's thesis: {thesis or '(not set)'}\n\n"
        "Below is a source paper I'm reviewing. Produce exactly two short paragraphs.\n\n"
        "Paragraph 1: Summary of the source paper — what it argues / shows. 3-5 sentences. "
        "Use plain language; do not pad.\n\n"
        "Paragraph 2: How this relates to my paper — concrete contrast or alignment points, "
        "specific to my thesis. 2-3 sentences.\n\n"
        "No headers. No preamble. Just the two paragraphs separated by a blank line.\n\n"
        f"Source paper:\n{paper_block}"
    )
    resp = client.messages.create(
        model=LLM_CLEAN_MODEL,
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


def llm_explain(text: str) -> str:
    """Plain-language explanation of pasted text. One or two short paragraphs."""
    from anthropic import Anthropic
    client = Anthropic()
    prompt = (
        "Explain the following text clearly in plain language. "
        "Aim for a reader who is generally educated but not a specialist in this field — "
        "not a child, not a peer. Skip jargon, define unavoidable terms briefly. "
        "Do not paraphrase line by line; tell the reader what the passage actually means. "
        "One or two short paragraphs. No preamble.\n\n"
        f"Text:\n{text}"
    )
    resp = client.messages.create(
        model=LLM_CLEAN_MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


# ── Bib parser ───────────────────────────────────────────────────────────────


def parse_bib_text(text: str) -> list[dict]:
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

        def gf(name: str) -> str:
            pat = rf'\b{name}\s*=\s*(?:\{{(.*?)\}}|"(.*?)"|(\w+))'
            fm = re.search(pat, block, re.DOTALL | re.IGNORECASE)
            if not fm:
                return ""
            val = fm.group(1) or fm.group(2) or fm.group(3) or ""
            val = re.sub(r"\{([^}]*)\}", r"\1", val)
            val = re.sub(r"\s+", " ", val).strip()
            return val

        authors_raw = gf("author")
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

        venue = gf("journal") or gf("booktitle") or gf("publisher") or ""

        type_map = {
            "article": "journal", "inproceedings": "conference",
            "conference": "conference", "incollection": "book",
            "book": "book", "phdthesis": "thesis",
            "mastersthesis": "thesis", "techreport": "report",
            "misc": "preprint", "unpublished": "preprint",
        }
        source_type = type_map.get(entry_type, "")
        if "arxiv" in venue.lower():
            source_type = "preprint"

        entries.append({
            "key": key,
            "title": gf("title"),
            "authors": authors,
            "year": gf("year"),
            "venue": venue,
            "source_type": source_type,
            "doi": gf("doi"),
            "url": gf("url") or gf("doi"),
            "tags": "",
            "quotes": "",
            "notes": "",
            "thoughts": "",
            "status": "not_started",
            "drafted": "",
            "flag": "",
            "flag_note": "",
        })
    return entries


# ── Session state ────────────────────────────────────────────────────────────


def init_state(reg: dict):
    if "active_project_id" not in st.session_state:
        st.session_state.active_project_id = reg.get("active_project")
    if "pom_running" not in st.session_state:
        st.session_state.pom_running = False
    if "pom_start_ts" not in st.session_state:
        st.session_state.pom_start_ts = None
    if "pom_elapsed" not in st.session_state:
        st.session_state.pom_elapsed = 0
    if "pom_done" not in st.session_state:
        st.session_state.pom_done = False
    if "current_key" not in st.session_state:
        st.session_state.current_key = None


# ── Pomodoro fragment ────────────────────────────────────────────────────────


def render_pomodoro(project: dict):
    """Pomodoro UI. Inner timer is a fragment that reruns every second."""
    st.markdown("**Pomodoro**")

    @st.fragment(run_every=1)
    def _ticker():
        total_secs = POMODORO_MINUTES * 60
        if st.session_state.pom_running and st.session_state.pom_start_ts:
            elapsed = time.time() - st.session_state.pom_start_ts + st.session_state.pom_elapsed
        else:
            elapsed = st.session_state.pom_elapsed
        remaining = max(0, total_secs - elapsed)
        mins = int(remaining // 60)
        secs = int(remaining % 60)
        st.markdown(f"## {mins:02d}:{secs:02d}")

        c1, c2 = st.columns(2)
        with c1:
            if st.session_state.pom_running:
                if st.button("⏸ Pause", width="stretch", key="pom_pause_btn"):
                    st.session_state.pom_elapsed = elapsed
                    st.session_state.pom_running = False
                    st.session_state.pom_start_ts = None
                    st.rerun(scope="fragment")
            else:
                if st.button("▶ Start", width="stretch", key="pom_play_btn"):
                    st.session_state.pom_start_ts = time.time()
                    st.session_state.pom_running = True
                    st.rerun(scope="fragment")
        with c2:
            if st.button("↺ Reset", width="stretch", key="pom_reset_btn"):
                if elapsed > 60:
                    log_session(project, elapsed / 60)
                st.session_state.pom_running = False
                st.session_state.pom_start_ts = None
                st.session_state.pom_elapsed = 0
                st.session_state.pom_done = False
                st.rerun(scope="fragment")

        if remaining == 0 and not st.session_state.pom_done and elapsed > 0:
            log_session(project, POMODORO_MINUTES)
            st.session_state.pom_done = True
            st.session_state.pom_running = False
            st.success("🎉 Session complete!")

    _ticker()

    # Daily chart — refreshed only on full reruns, not every second
    log = load_time_log(project)
    if log:
        totals = time_by_day(log)
        today = str(datetime.date.today())
        today_mins = int(totals.get(today, 0))
        st.caption(f"Today: {today_mins} mins")
        last7 = sorted(totals.items())[-7:]
        if last7:
            chart_df = pd.DataFrame(last7, columns=["date", "minutes"])
            chart_df["date"] = chart_df["date"].str[-5:]
            st.bar_chart(chart_df.set_index("date"), height=80, width="stretch")


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    st.set_page_config(page_title="Lit Review", layout="wide", initial_sidebar_state="expanded")
    reg = load_registry()
    init_state(reg)

    # ── Sidebar ──
    with st.sidebar:
        st.markdown("### Project")
        project_ids = [p["id"] for p in reg.get("projects", [])]
        project_names = {p["id"]: p["name"] for p in reg.get("projects", [])}

        if not project_ids:
            st.warning("No projects yet. Create one to get started.")
            new_name = st.text_input("New project name", key="first_proj_name")
            if st.button("Create project", key="create_first_btn"):
                if new_name:
                    proj = create_project(reg, new_name)
                    reg["active_project"] = proj["id"]
                    save_registry(reg)
                    st.session_state.active_project_id = proj["id"]
                    st.rerun()
            return

        cur_idx = (
            project_ids.index(st.session_state.active_project_id)
            if st.session_state.active_project_id in project_ids
            else 0
        )
        chosen_pid = st.selectbox(
            "Active project",
            project_ids,
            format_func=lambda x: project_names.get(x, x),
            index=cur_idx,
            key="project_picker",
            label_visibility="collapsed",
        )
        if chosen_pid != st.session_state.active_project_id:
            st.session_state.active_project_id = chosen_pid
            reg["active_project"] = chosen_pid
            save_registry(reg)
            st.session_state.current_key = None
            st.session_state.pom_running = False
            st.session_state.pom_start_ts = None
            st.session_state.pom_elapsed = 0
            st.session_state.pom_done = False
            st.rerun()

        project = get_project(reg, st.session_state.active_project_id)

        with st.expander("+ New project"):
            np_name = st.text_input("Name", key="np_name_in")
            if st.button("Create", key="create_proj_btn"):
                if np_name:
                    proj = create_project(reg, np_name)
                    reg["active_project"] = proj["id"]
                    save_registry(reg)
                    st.session_state.active_project_id = proj["id"]
                    st.rerun()

        df = load_sources(project)
        setup = load_setup(project)

        st.divider()
        render_pomodoro(project)

        # Scratchpad
        st.divider()
        st.markdown("**💭 Scratchpad**")
        st.caption("Per-project. Random thoughts, ideas, things to look up.")
        scratch_key = f"scratch_{project['id']}"
        if scratch_key not in st.session_state:
            st.session_state[scratch_key] = load_scratchpad(project)

        def _save_scratch():
            save_scratchpad(project, st.session_state[scratch_key])

        st.text_area(
            "scratchpad",
            height=180,
            key=scratch_key,
            on_change=_save_scratch,
            placeholder="Dump anything here. Autosaves.",
            label_visibility="collapsed",
        )

        # 🤔 Explain to me
        if HAS_API_KEY:
            st.divider()
            with st.expander("🤔 Explain to me"):
                st.caption("Paste any text. Get a clear plain-language explanation.")
                explain_in_key = f"explain_in_{project['id']}"
                explain_out_key = f"explain_out_{project['id']}"
                explain_err_key = f"explain_err_{project['id']}"
                explain_pending_key = f"explain_pending_{project['id']}"
                if explain_in_key not in st.session_state:
                    st.session_state[explain_in_key] = ""
                if explain_out_key not in st.session_state:
                    st.session_state[explain_out_key] = ""

                # Run pending LLM call BEFORE rendering
                if st.session_state.get(explain_pending_key):
                    with st.spinner("🤔 Explaining…"):
                        try:
                            st.session_state[explain_out_key] = llm_explain(
                                st.session_state.get(explain_in_key, "")
                            )
                        except Exception as e:
                            st.session_state[explain_err_key] = str(e)
                    st.session_state[explain_pending_key] = False
                    st.rerun()

                st.text_area(
                    "Text to explain",
                    key=explain_in_key,
                    height=150,
                    placeholder="Paste a tricky paragraph here.",
                    label_visibility="collapsed",
                )

                def _request_explain():
                    if st.session_state.get(explain_in_key, "").strip():
                        st.session_state[explain_pending_key] = True

                bcol1, bcol2 = st.columns([1, 1])
                with bcol1:
                    st.button("Explain", key=f"explain_btn_{project['id']}",
                              on_click=_request_explain, width="stretch")
                with bcol2:
                    if st.button("Clear", key=f"explain_clr_{project['id']}", width="stretch"):
                        st.session_state[explain_in_key] = ""
                        st.session_state[explain_out_key] = ""
                        st.rerun()

                err = st.session_state.pop(explain_err_key, None)
                if err:
                    st.warning(f"Explain failed: {err}")
                if st.session_state.get(explain_out_key):
                    st.markdown(st.session_state[explain_out_key])

        # LLM status indicator
        st.divider()
        if HAS_API_KEY:
            st.caption("✨ LLM features enabled (Claude Haiku)")
        else:
            st.caption("LLM features disabled — set ANTHROPIC_API_KEY")

    # ── Main tabs ──
    tab_setup, tab_review, tab_draft, tab_compiled = st.tabs(
        ["Setup", "Review", "Draft", "Compiled notes"]
    )

    with tab_setup:
        render_setup(project, setup, df)
    with tab_review:
        render_review(project, df, setup)
    with tab_draft:
        render_draft(project, df, setup)
    with tab_compiled:
        render_compiled(project, df)


# ── SETUP TAB ────────────────────────────────────────────────────────────────


def render_setup(project: dict, setup: dict, df: pd.DataFrame):
    pid = project["id"]
    st.markdown("### Setup")
    st.caption("Project-level configuration: paper title, outline, deadlines, plans, formatting, and the paper list.")

    new_title = st.text_input(
        "Paper title", value=setup.get("title", ""), key=f"setup_title_{pid}"
    )
    new_thesis = st.text_area(
        "One-sentence thesis",
        value=setup.get("thesis", ""),
        height=80,
        key=f"setup_thesis_{pid}",
    )

    # ── Outline editor (structured, reorderable) ──
    st.markdown("**Outline**")
    st.caption("Reorder with ↑ / ↓. Tick when written. ✕ to remove. Click `+ subsection` / `+ section` to add empty rows.")
    outline = setup.get("outline", [])
    new_outline = []

    def _commit_and_mutate(mutator):
        """Read current widget values into a fresh outline list, then apply mutator and save."""
        cur = []
        for sec_ in outline:
            sec_id = sec_.get("id") or _new_id()
            title = st.session_state.get(f"sec_title_{pid}_{sec_id}", sec_.get("title", ""))
            written = st.session_state.get(f"sec_written_{pid}_{sec_id}", sec_.get("written", False))
            subs = []
            for sub_ in sec_.get("subsections", []):
                sub_id = sub_.get("id") or _new_id()
                sub_title = st.session_state.get(f"sub_title_{pid}_{sec_id}_{sub_id}", sub_.get("title", ""))
                sub_written = st.session_state.get(f"sub_written_{pid}_{sec_id}_{sub_id}", sub_.get("written", False))
                subs.append({"id": sub_id, "title": sub_title, "written": sub_written})
            cur.append({"id": sec_id, "title": title, "written": written, "subsections": subs})
        mutator(cur)
        s = load_setup(project)
        s["outline"] = cur
        save_setup(project, s)

    for i, sec in enumerate(outline):
        sec_id = sec.get("id") or _new_id()
        with st.container(border=True):
            # Visual badge: green pill with "Section N"
            st.markdown(
                f'<div style="background:#2D6E47;color:white;display:inline-block;'
                f'padding:3px 14px;border-radius:6px;font-weight:600;font-size:0.95em;'
                f'margin-bottom:8px;">Section {i + 1}</div>',
                unsafe_allow_html=True,
            )
            # Section header row: [title] [✓] [↑] [↓] [✕]
            c1, c2, c3, c4, c5 = st.columns([7.4, 1.2, 0.5, 0.5, 0.5])
            with c1:
                st.text_input(
                    "Section title",
                    value=sec.get("title", ""),
                    key=f"sec_title_{pid}_{sec_id}",
                    label_visibility="collapsed",
                    placeholder="Section title",
                )
            with c2:
                st.checkbox(
                    "Written",
                    value=bool(sec.get("written", False)),
                    key=f"sec_written_{pid}_{sec_id}",
                )
            with c3:
                if st.button("↑", key=f"sec_up_{pid}_{sec_id}", disabled=(i == 0), help="Move up"):
                    def _up(cur, _i=i):
                        cur[_i], cur[_i - 1] = cur[_i - 1], cur[_i]
                    _commit_and_mutate(_up)
                    st.rerun()
            with c4:
                if st.button("↓", key=f"sec_dn_{pid}_{sec_id}", disabled=(i == len(outline) - 1), help="Move down"):
                    def _dn(cur, _i=i):
                        cur[_i], cur[_i + 1] = cur[_i + 1], cur[_i]
                    _commit_and_mutate(_dn)
                    st.rerun()
            with c5:
                if st.button("✕", key=f"sec_del_{pid}_{sec_id}", help="Remove section"):
                    def _del(cur, _i=i):
                        cur.pop(_i)
                    _commit_and_mutate(_del)
                    st.rerun()

            # Subsection rows — only render those that exist
            subs = sec.get("subsections", [])
            new_subs = []
            for j, sub in enumerate(subs):
                sub_id = sub.get("id") or _new_id()
                sc0, sc1, sc2, sc3, sc4, sc5 = st.columns([0.5, 6.9, 1.2, 0.5, 0.5, 0.5])
                with sc0:
                    st.markdown(f"<small>↳ {i + 1}.{j + 1}</small>", unsafe_allow_html=True)
                with sc1:
                    st.text_input(
                        "Subsection title",
                        value=sub.get("title", ""),
                        key=f"sub_title_{pid}_{sec_id}_{sub_id}",
                        label_visibility="collapsed",
                        placeholder="Subsection title",
                    )
                with sc2:
                    st.checkbox(
                        "Written",
                        value=bool(sub.get("written", False)),
                        key=f"sub_written_{pid}_{sec_id}_{sub_id}",
                        label_visibility="collapsed",
                    )
                with sc3:
                    if st.button("↑", key=f"sub_up_{pid}_{sec_id}_{sub_id}", disabled=(j == 0)):
                        def _up(cur, _i=i, _j=j):
                            s = cur[_i]["subsections"]
                            s[_j], s[_j - 1] = s[_j - 1], s[_j]
                        _commit_and_mutate(_up)
                        st.rerun()
                with sc4:
                    if st.button("↓", key=f"sub_dn_{pid}_{sec_id}_{sub_id}", disabled=(j == len(subs) - 1)):
                        def _dn(cur, _i=i, _j=j):
                            s = cur[_i]["subsections"]
                            s[_j], s[_j + 1] = s[_j + 1], s[_j]
                        _commit_and_mutate(_dn)
                        st.rerun()
                with sc5:
                    if st.button("✕", key=f"sub_del_{pid}_{sec_id}_{sub_id}"):
                        def _del(cur, _i=i, _j=j):
                            cur[_i]["subsections"].pop(_j)
                        _commit_and_mutate(_del)
                        st.rerun()
                new_subs.append({
                    "id": sub_id,
                    "title": st.session_state.get(f"sub_title_{pid}_{sec_id}_{sub_id}", sub.get("title", "")),
                    "written": st.session_state.get(f"sub_written_{pid}_{sec_id}_{sub_id}", sub.get("written", False)),
                })

            # + Subsection button (adds an empty subsection on click)
            if st.button("+ subsection", key=f"add_sub_btn_{pid}_{sec_id}"):
                def _add(cur, _i=i):
                    cur[_i]["subsections"].append({"id": _new_id(), "title": "", "written": False})
                _commit_and_mutate(_add)
                st.rerun()

            new_outline.append({
                "id": sec_id,
                "title": st.session_state.get(f"sec_title_{pid}_{sec_id}", sec.get("title", "")),
                "written": st.session_state.get(f"sec_written_{pid}_{sec_id}", sec.get("written", False)),
                "subsections": new_subs,
            })

    # + Section button (adds an empty section on click)
    if st.button("+ section", key=f"add_sec_btn_{pid}"):
        def _add(cur):
            cur.append({"id": _new_id(), "title": "", "written": False, "subsections": []})
        _commit_and_mutate(_add)
        st.rerun()

    # ── Deadlines ──
    st.markdown("**Deadlines**")
    deadlines = setup.get("deadlines", [])
    new_deadlines = []
    if deadlines:
        for i, d in enumerate(deadlines):
            c1, c2, c3 = st.columns([2, 5, 1])
            iso_date = d.get("date", "")
            with c1:
                dt = st.text_input(
                    "Date",
                    value=to_dmy(iso_date),
                    key=f"dl_date_{pid}_{i}",
                    label_visibility="collapsed",
                    placeholder="DD-MM-YYYY",
                )
            with c2:
                lbl = st.text_input(
                    "Label",
                    value=d.get("label", ""),
                    key=f"dl_label_{pid}_{i}",
                    label_visibility="collapsed",
                    placeholder="label",
                )
            with c3:
                drop = st.button("✕", key=f"dl_del_{pid}_{i}", help="Remove")
            if not drop and (dt or lbl):
                new_deadlines.append({"date": to_iso(dt), "label": lbl})
    c1, c2 = st.columns([2, 5])
    with c1:
        add_dt = st.text_input(
            "Add date",
            key=f"dl_new_date_{pid}",
            label_visibility="collapsed",
            placeholder="DD-MM-YYYY",
        )
    with c2:
        add_lbl = st.text_input(
            "Add label",
            key=f"dl_new_label_{pid}",
            label_visibility="collapsed",
            placeholder="new deadline label",
        )

    # ── Plans / next steps ──
    new_plans = st.text_area(
        "Plans / next steps",
        value=setup.get("plans", ""),
        height=140,
        key=f"setup_plans_{pid}",
    )

    # ── Target word count ──
    c1, c2 = st.columns([1, 4])
    with c1:
        new_target = st.number_input(
            "Target word count",
            min_value=0,
            value=int(setup.get("target_word_count", 0) or 0),
            step=500,
            key=f"setup_target_{pid}",
        )
    with c2:
        st.caption("Total target across all draft sections. 0 = no target.")

    # ── Formatting guidelines ──
    new_format = st.text_area(
        "Formatting guidelines",
        value=setup.get("formatting_guidelines", ""),
        height=140,
        key=f"setup_format_{pid}",
        placeholder="e.g. ACL 8-page main + appendix; A4; line numbers under review; ABBR conventions; British spelling…",
    )

    # ── Default tags ──
    st.markdown("**Default tags** — suggested when adding new papers (comma-separated)")
    new_default_tags_str = st.text_input(
        "default tags",
        value=", ".join(setup.get("default_tags", [])),
        key=f"setup_default_tags_{pid}",
        label_visibility="collapsed",
    )
    new_default_tags = parse_tags(new_default_tags_str)

    if st.button("Save setup", key=f"save_setup_{pid}", type="primary"):
        if add_dt or add_lbl:
            new_deadlines.append({"date": to_iso(add_dt), "label": add_lbl})
        save_setup(project, {
            "title": new_title,
            "thesis": new_thesis,
            "outline": new_outline,
            "deadlines": new_deadlines,
            "plans": new_plans,
            "default_tags": new_default_tags,
            "target_word_count": int(new_target),
            "formatting_guidelines": new_format,
        })
        st.success("Setup saved.")
        st.rerun()

    # ── Papers section ──
    st.divider()
    st.markdown("### Papers")
    total = len(df)
    reviewed = (df["status"] == "reviewed").sum()
    partial = (df["status"] == "partial").sum()
    st.caption(f"{total} papers · {reviewed} reviewed · {partial} partial · {total - reviewed - partial} not started")

    with st.expander("Bulk import from .bib"):
        bib_text = st.text_area(
            "Paste .bib text here",
            height=200,
            key=f"bulk_bib_{pid}",
            placeholder="@article{...} @inproceedings{...} ...",
        )
        if st.button("Import bib entries", key=f"bulk_import_{pid}"):
            if bib_text.strip():
                entries = parse_bib_text(bib_text)
                existing = set(df["key"].tolist())
                added = 0
                for e in entries:
                    if e["key"] and e["key"] not in existing:
                        df = update_row(df, e["key"], {k: v for k, v in e.items() if k != "key"})
                        existing.add(e["key"])
                        added += 1
                save_sources(project, df)
                st.success(f"Added {added} new paper(s) (skipped {len(entries) - added} already present).")
                st.rerun()
            else:
                st.warning("Paste some bib text first.")

    with st.expander("Add one paper"):
        col1, col2 = st.columns(2)
        with col1:
            o_key = st.text_input("Citation key", key=f"oadd_key_{pid}")
            o_title = st.text_input("Title", key=f"oadd_title_{pid}")
            o_authors = st.text_input("Authors", key=f"oadd_authors_{pid}")
            o_year = st.text_input("Year", key=f"oadd_year_{pid}")
        with col2:
            o_venue = st.text_input("Venue", key=f"oadd_venue_{pid}")
            o_type = st.selectbox("Source type", SOURCE_TYPES, key=f"oadd_type_{pid}")
            o_url = st.text_input("URL / DOI", key=f"oadd_url_{pid}")
            o_tags = st.text_input(
                "Tags (comma-separated)",
                value=", ".join(setup.get("default_tags", [])),
                key=f"oadd_tags_{pid}",
            )
        if st.button("Add this paper", key=f"oadd_btn_{pid}"):
            if o_key and o_key not in df["key"].tolist():
                df = update_row(df, o_key, {
                    "title": o_title, "authors": o_authors,
                    "year": o_year, "venue": o_venue,
                    "source_type": o_type, "url": o_url,
                    "tags": o_tags, "status": "not_started",
                })
                save_sources(project, df)
                st.success(f"Added {o_key}.")
                st.rerun()


# ── REVIEW TAB ───────────────────────────────────────────────────────────────


def render_review(project: dict, df: pd.DataFrame, setup: dict):
    pid = project["id"]

    if df.empty:
        st.info("No papers yet. Add some via the Setup tab or the sidebar.")
        return

    # Top: progress + tag filter
    total = len(df)
    reviewed = (df["status"] == "reviewed").sum()
    partial = (df["status"] == "partial").sum()
    pct = (reviewed + 0.5 * partial) / total if total else 0

    c1, c2 = st.columns([2, 3])
    with c1:
        st.markdown(f"**{reviewed} / {total} reviewed**")
        st.progress(min(1.0, pct))
        st.caption(f"{partial} partial · {total - reviewed - partial} not started")
    with c2:
        tags_in_use = all_tags(df)
        if tags_in_use:
            tag_filter = st.multiselect(
                "Filter by tag",
                tags_in_use,
                key=f"tag_filter_ms_{pid}",
            )
        else:
            tag_filter = []
            st.caption("No tags yet — add some on a paper or use ✨ Suggest.")

    # Filtered key list
    filtered = df
    if tag_filter:
        mask = df["tags"].apply(
            lambda s: any(t in parse_tags(s) for t in tag_filter)
        )
        filtered = df[mask]
    keys = filtered["key"].tolist()
    if not keys:
        st.warning("No papers matching that tag filter.")
        return

    # Build numbered label → key map for the picker
    labels = []
    key_of_label = {}
    for k in keys:
        row = row_to_dict(df, k)
        idx = paper_index_of(df, k)
        lbl = paper_label(row, index=idx)
        base = lbl
        suffix = 2
        while lbl in key_of_label:
            lbl = f"{base}  [{suffix}]"
            suffix += 1
        labels.append(lbl)
        key_of_label[lbl] = k

    if st.session_state.current_key not in keys:
        st.session_state.current_key = keys[0]
    cur_label = next((l for l, k in key_of_label.items() if k == st.session_state.current_key), labels[0])
    cur_idx = labels.index(cur_label)

    chosen_label = st.selectbox(
        "Pick paper",
        labels,
        index=cur_idx,
        key=f"paper_picker_{pid}",
    )
    if key_of_label[chosen_label] != st.session_state.current_key:
        st.session_state.current_key = key_of_label[chosen_label]
        st.rerun()

    cprev, cnext = st.columns(2)
    with cprev:
        if st.button("← Prev paper", width="stretch", key=f"prev_btn_{pid}"):
            i = keys.index(st.session_state.current_key)
            st.session_state.current_key = keys[max(0, i - 1)]
            st.rerun()
    with cnext:
        if st.button("Next paper →", width="stretch", key=f"next_btn_{pid}"):
            i = keys.index(st.session_state.current_key)
            st.session_state.current_key = keys[min(len(keys) - 1, i + 1)]
            st.rerun()

    # ⭐ High importance jump list
    flagged = df[df["flag"] == "yes"]["key"].tolist()
    if flagged:
        with st.expander(f"⭐ High importance ({len(flagged)})"):
            for fk in flagged:
                row = row_to_dict(df, fk)
                idx = paper_index_of(df, fk)
                note = row.get("flag_note", "")
                label = paper_label(row, index=idx, max_chars=55)
                if note:
                    label = f"{label}  · {note[:30]}"
                if st.button(label, key=f"flagj_{pid}_{fk}", width="stretch"):
                    st.session_state.current_key = fk
                    st.rerun()

    st.divider()

    # Paper card
    key = st.session_state.current_key
    row = row_to_dict(df, key)
    idx = paper_index_of(df, key)

    with st.container(border=True):
        st.markdown(f"#### Paper {idx:02d} · {smart_title_case(row['title']) if row.get('title') else key}")
        meta = [p for p in [row.get('authors'), row.get('year'), row.get('venue'), row.get('source_type')] if p]
        st.caption("  ·  ".join(meta) or "(no metadata)")

        c1, c2 = st.columns([3, 2])
        with c1:
            if row.get("url"):
                st.link_button(
                    f"📄 Open paper → {row['url'][:60]}",
                    row["url"],
                    width="stretch",
                )
            else:
                # Inline URL add
                url_in = st.text_input(
                    "Paper URL / DOI",
                    key=f"url_inline_{pid}_{key}",
                    placeholder="Paste URL or DOI here",
                    label_visibility="collapsed",
                )
                if url_in and st.button("Save URL", key=f"save_url_{pid}_{key}"):
                    df = update_row(df, key, {"url": url_in})
                    save_sources(project, df)
                    st.rerun()
        with c2:
            cur_status = row.get("status", "not_started") or "not_started"
            new_status = st.selectbox(
                "Status",
                STATUS_OPTIONS,
                format_func=lambda s: STATUS_LABELS.get(s, s),
                index=STATUS_OPTIONS.index(cur_status) if cur_status in STATUS_OPTIONS else 0,
                key=f"status_{pid}_{key}",
            )
            if new_status != cur_status:
                df = update_row(df, key, {"status": new_status})
                save_sources(project, df)
                st.rerun()

    # Tags
    current_tags = parse_tags(row.get("tags", ""))
    tags_key = f"tags_{pid}_{key}"
    tags_pending_key = f"_pending_tags_{tags_key}"
    if tags_key not in st.session_state:
        st.session_state[tags_key] = ", ".join(current_tags)

    # Run pending Suggest call BEFORE rendering the input
    if st.session_state.get(tags_pending_key):
        with st.spinner("✨ Suggesting tags…"):
            try:
                st.session_state[tags_key] = llm_suggest_tags(
                    paper_meta=row,
                    quotes=row.get("quotes", ""),
                    notes=row.get("notes", ""),
                    thoughts=row.get("thoughts", ""),
                    thesis=setup.get("thesis", ""),
                )
            except Exception as e:
                st.session_state[f"_tag_err_{tags_key}"] = str(e)
        st.session_state[tags_pending_key] = False
        st.rerun()

    if HAS_API_KEY:
        tcol1, tcol2 = st.columns([5, 1])
    else:
        tcol1, tcol2 = st.container(), None

    with tcol1:
        st.text_input(
            "Tags (comma-separated, free-form — or ✨ Suggest)",
            key=tags_key,
            placeholder="e.g. LLM-as-judge, multiverse, judge bias",
        )

    if HAS_API_KEY and tcol2 is not None:
        with tcol2:
            st.write("")

            def _request_suggest_tags():
                st.session_state[tags_pending_key] = True

            st.button(
                "✨ Suggest",
                key=f"tags_suggest_{pid}_{key}",
                on_click=_request_suggest_tags,
                help="Suggest tags via Claude Haiku based on title + your notes",
                width="stretch",
            )

    err = st.session_state.pop(f"_tag_err_{tags_key}", None)
    if err:
        st.warning(f"Suggest tags failed: {err}")

    new_tag_input = st.session_state[tags_key]
    new_tags_str = format_tags(parse_tags(new_tag_input))
    if new_tags_str != row.get("tags", ""):
        df = update_row(df, key, {"tags": new_tags_str})
        save_sources(project, df)

    st.divider()

    # ── 📝 Summary (LLM-generated, editable) ──
    st.markdown("### 📝 Summary")
    st.caption("Two paragraphs: what the paper says + how it relates to your paper. Editable; autosaves.")
    summary_key = f"summary_{pid}_{key}"
    summary_pending_key = f"_pending_sum_{summary_key}"
    if summary_key not in st.session_state:
        st.session_state[summary_key] = row.get("summary", "")

    # Run pending Generate call BEFORE rendering
    if st.session_state.get(summary_pending_key):
        with st.spinner("✨ Generating summary…"):
            try:
                st.session_state[summary_key] = llm_summarise_paper(
                    paper_meta=row,
                    quotes=row.get("quotes", ""),
                    notes=row.get("notes", ""),
                    thoughts=row.get("thoughts", ""),
                    thesis=setup.get("thesis", ""),
                )
            except Exception as e:
                st.session_state[f"_sum_err_{summary_key}"] = str(e)
        st.session_state[summary_pending_key] = False
        st.rerun()

    if HAS_API_KEY:
        sc1, sc2 = st.columns([5, 1])
    else:
        sc1, sc2 = st.container(), None

    with sc1:
        st.text_area(
            "summary_box",
            key=summary_key,
            height=200,
            placeholder="Click ✨ Generate to draft a summary, then edit freely.",
            label_visibility="collapsed",
        )

    if HAS_API_KEY and sc2 is not None:
        with sc2:
            st.write("")

            def _request_summary():
                st.session_state[summary_pending_key] = True

            st.button(
                "✨ Generate",
                key=f"sum_btn_{pid}_{key}",
                on_click=_request_summary,
                help="Two-paragraph summary via Claude Haiku, drawn from your saved quotes / notes / thoughts",
                width="stretch",
            )

    err = st.session_state.pop(f"_sum_err_{summary_key}", None)
    if err:
        st.warning(f"Summary failed: {err}")

    cur_summary = st.session_state[summary_key]
    if cur_summary != row.get("summary", ""):
        df = update_row(df, key, {"summary": cur_summary})
        save_sources(project, df)

    st.divider()

    # ── Staging-buffer text boxes ──
    quotes_key = f"stage_quotes_{pid}_{key}"
    notes_key = f"stage_notes_{pid}_{key}"
    thoughts_key = f"stage_thoughts_{pid}_{key}"
    for k in (quotes_key, notes_key, thoughts_key):
        if k not in st.session_state:
            st.session_state[k] = ""

    st.caption("Staging boxes — paste text, optionally ✨ Clean it via LLM, then click ✅ Add to Excel below to append + clear.")

    _staging_box(
        sk=quotes_key,
        label="Direct quotes — paste verbatim from the source",
        placeholder="Paste direct quotes here.",
    )
    _staging_box(
        sk=notes_key,
        label="Notes to summarise — paraphrases, summary points",
        placeholder="Paraphrase or summarise.",
    )
    _staging_box(
        sk=thoughts_key,
        label="My thoughts — how it relates to my paper",
        placeholder="Contrasts, alignments, arguments.",
    )

    # Add to Excel
    def _add_to_excel():
        q = st.session_state.get(quotes_key, "").strip()
        n = st.session_state.get(notes_key, "").strip()
        t = st.session_state.get(thoughts_key, "").strip()
        if not (q or n or t):
            return
        df_now = load_sources(project)
        row_now = row_to_dict(df_now, key)
        updates = {}
        for field, new in [("quotes", q), ("notes", n), ("thoughts", t)]:
            if not new:
                continue
            existing = row_now.get(field, "") or ""
            sep = "\n\n" if existing else ""
            updates[field] = existing + sep + new
        if updates:
            df_now = update_row(df_now, key, updates)
            save_sources(project, df_now)
        st.session_state[quotes_key] = ""
        st.session_state[notes_key] = ""
        st.session_state[thoughts_key] = ""

    c1, c2 = st.columns([1, 4])
    with c1:
        st.button(
            "✅ Add to Excel",
            key=f"commit_{pid}_{key}",
            on_click=_add_to_excel,
            type="primary",
            width="stretch",
        )
    with c2:
        st.caption("Appends each box to the paper's saved data and clears all three boxes.")

    # View saved
    with st.expander("View saved notes for this paper"):
        if row.get("quotes"):
            st.markdown("**Direct quotes**")
            st.markdown(row["quotes"])
        if row.get("notes"):
            st.markdown("**Notes**")
            st.markdown(row["notes"])
        if row.get("thoughts"):
            st.markdown("**My thoughts**")
            st.markdown(row["thoughts"])
        if not (row.get("quotes") or row.get("notes") or row.get("thoughts")):
            st.caption("(Nothing saved yet for this paper.)")

    # ⭐ High importance
    st.divider()
    c1, c2 = st.columns([1, 4])
    with c1:
        cur_flag = row.get("flag", "") == "yes"
        new_flag = st.checkbox(
            "⭐ High importance",
            value=cur_flag,
            key=f"flag_{pid}_{key}",
        )
        if new_flag != cur_flag:
            df = update_row(df, key, {"flag": "yes" if new_flag else ""})
            save_sources(project, df)
            st.rerun()
    with c2:
        cur_flag_note = row.get("flag_note", "")
        new_flag_note = st.text_input(
            "Why it's important",
            value=cur_flag_note,
            key=f"flag_note_{pid}_{key}",
            placeholder="e.g. closest precursor; foundational; reviewer favourite",
            label_visibility="collapsed",
        )
        if new_flag_note != cur_flag_note:
            df = update_row(df, key, {"flag_note": new_flag_note})
            save_sources(project, df)


def _staging_box(sk: str, label: str, placeholder: str):
    """Render a staging text_area with an optional ✨ Clean button next to it."""
    pending_key = f"_pending_clean_{sk}"
    err_key = f"_clean_err_{sk}"

    # Run pending LLM call BEFORE rendering widgets that use sk
    if st.session_state.get(pending_key):
        with st.spinner(f"✨ Cleaning {label.split(' — ')[0].lower()}…"):
            try:
                raw = st.session_state.get(sk, "")
                st.session_state[sk] = llm_clean(raw)
            except Exception as e:
                st.session_state[err_key] = str(e)
        st.session_state[pending_key] = False
        st.rerun()

    st.markdown(f"**{label}**")
    if HAS_API_KEY:
        cl, cr = st.columns([5, 1])
        with cl:
            st.text_area(
                f"box_{sk}",
                height=140,
                key=sk,
                placeholder=placeholder,
                label_visibility="collapsed",
            )
        with cr:
            st.write("")

            def _request_clean():
                if st.session_state.get(sk, "").strip():
                    st.session_state[pending_key] = True

            st.button(
                "✨ Clean",
                key=f"clean_btn_{sk}",
                on_click=_request_clean,
                help="Clean OCR / line-break noise via Claude Haiku",
                width="stretch",
            )
    else:
        st.text_area(
            f"box_{sk}",
            height=140,
            key=sk,
            placeholder=placeholder,
            label_visibility="collapsed",
        )
    err = st.session_state.pop(err_key, None)
    if err:
        st.warning(f"Clean failed: {err}")


# ── DRAFT TAB ────────────────────────────────────────────────────────────────


def render_draft(project: dict, df: pd.DataFrame, setup: dict):
    pid = project["id"]
    sections = flatten_outline(setup.get("outline", []))
    if not sections:
        st.info("No outline yet. Add sections in the Setup tab.")
        return

    draft = load_draft(project)
    target = int(setup.get("target_word_count", 0) or 0)
    current_words = sum(len((draft.get(s, "") or "").split()) for s in sections)

    st.markdown("### Draft")
    if target:
        st.caption(f"Words: **{current_words:,} / {target:,}** ({current_words / target * 100:.0f}%)")
    else:
        st.caption(f"Words: **{current_words:,}** (no target set — Setup tab)")

    section = st.selectbox("Section", sections, key=f"draft_section_{pid}")

    c_left, c_right = st.columns([3, 2])

    # ── LEFT: draft text ──
    with c_left:
        st.markdown(f"#### Drafting: {section}")
        current_text = draft.get(section, "")
        new_text = st.text_area(
            "draft_text",
            value=current_text,
            height=620,
            key=f"draft_{pid}_{section}",
            label_visibility="collapsed",
            placeholder="Start drafting here. Autosaves on each change.",
        )
        if new_text != current_text:
            draft[section] = new_text
            save_draft(project, draft)

        if st.button("Build draft.md", key=f"build_md_{pid}"):
            md_lines = [f"# {setup.get('title', 'Draft')}\n"]
            outline = setup.get("outline", [])
            for sec in outline:
                if not isinstance(sec, dict):
                    continue
                t = sec.get("title", "")
                if not t:
                    continue
                md_lines.append(f"\n## {t}\n\n{draft.get(t, '')}\n")
                for sub in sec.get("subsections", []):
                    if isinstance(sub, dict):
                        sub_t = sub.get("title", "")
                        if sub_t:
                            md_lines.append(f"\n### {sub_t}\n\n{draft.get(f'{t} > {sub_t}', '')}\n")
            md = "".join(md_lines)
            st.download_button(
                "Download draft.md",
                md,
                file_name="draft.md",
                mime="text/markdown",
                key=f"dl_md_{pid}",
            )

    # ── RIGHT: paper picker + checklist ──
    with c_right:
        st.markdown("#### Paper notes")
        if df.empty:
            st.caption("(No papers yet.)")
        else:
            picker_keys = df["key"].tolist()
            picker_labels = ["— (pick a paper) —"] + [
                paper_label(row_to_dict(df, k), index=paper_index_of(df, k), max_chars=80)
                for k in picker_keys
            ]
            chosen_label = st.selectbox(
                "Pull up notes for…",
                picker_labels,
                key=f"draft_paper_picker_{pid}",
            )
            if chosen_label and chosen_label != "— (pick a paper) —":
                # Find the key by matching the label
                chosen_key = None
                for k in picker_keys:
                    if paper_label(row_to_dict(df, k), index=paper_index_of(df, k), max_chars=80) == chosen_label:
                        chosen_key = k
                        break
                if chosen_key:
                    r = row_to_dict(df, chosen_key)
                    with st.container(border=True, height=400):
                        if r.get("tags"):
                            st.caption(f"tags: {r['tags']}")
                        if r.get("quotes"):
                            st.markdown("**Direct quotes**")
                            st.markdown(r["quotes"])
                        if r.get("notes"):
                            st.markdown("**Notes**")
                            st.markdown(r["notes"])
                        if r.get("thoughts"):
                            st.markdown("**My thoughts**")
                            st.markdown(r["thoughts"])
                        if not (r.get("quotes") or r.get("notes") or r.get("thoughts")):
                            st.caption("(No notes saved for this paper yet.)")

        st.divider()
        # Checklist
        reviewed_mask = df["status"].isin(["reviewed", "partial"])
        not_drafted = df[reviewed_mask & (df["drafted"] != "yes")]
        incorporated = df[df["drafted"] == "yes"]
        total_reviewed = int(reviewed_mask.sum())
        st.markdown(f"#### Incorporated: {len(incorporated)} / {total_reviewed}")

        st.caption("Tick when you've pulled material from this paper into the draft.")
        if not_drafted.empty:
            if total_reviewed == 0:
                st.caption("(No reviewed papers yet. Mark some as Reviewed/Partial on the Review tab.)")
            else:
                st.caption("All reviewed papers incorporated. 🎉")

        with st.container(height=280, border=True):
            for _, r in not_drafted.iterrows():
                idx = paper_index_of(df, r["key"])
                lbl = paper_label(r.to_dict(), index=idx, max_chars=60)
                cl, cr = st.columns([1, 8])
                with cl:
                    def _make_done(_k=r["key"]):
                        def cb():
                            df_now = load_sources(project)
                            df_now = update_row(df_now, _k, {"drafted": "yes"})
                            save_sources(project, df_now)
                        return cb
                    st.checkbox(
                        "",
                        value=False,
                        key=f"drafted_cb_{pid}_{r['key']}",
                        on_change=_make_done(),
                        label_visibility="collapsed",
                    )
                with cr:
                    st.write(lbl)

        if not incorporated.empty:
            with st.expander(f"Already incorporated ({len(incorporated)})"):
                for _, r in incorporated.iterrows():
                    idx = paper_index_of(df, r["key"])
                    lbl = paper_label(r.to_dict(), index=idx, max_chars=60)
                    cl, cr = st.columns([1, 8])
                    with cl:
                        def _make_undone(_k=r["key"]):
                            def cb():
                                df_now = load_sources(project)
                                df_now = update_row(df_now, _k, {"drafted": ""})
                                save_sources(project, df_now)
                            return cb
                        st.checkbox(
                            "",
                            value=True,
                            key=f"undone_cb_{pid}_{r['key']}",
                            on_change=_make_undone(),
                            label_visibility="collapsed",
                        )
                    with cr:
                        st.write(lbl)


# ── COMPILED NOTES TAB ───────────────────────────────────────────────────────


def render_compiled(project: dict, df: pd.DataFrame):
    pid = project["id"]
    st.markdown("### Compiled notes")
    st.caption("Edit metadata / tags / status / flag / drafted status inline. Click Save when done.")
    tag_filter = st.text_input(
        "Filter by tag substring (blank = show all)",
        key=f"compiled_tag_filter_{pid}",
    )
    flt = df
    if tag_filter:
        flt = df[df["tags"].str.contains(tag_filter, case=False, na=False)]

    show_cols = ["key", "title", "authors", "year", "venue", "source_type",
                 "url", "tags", "status", "drafted", "flag"]
    editable = flt[show_cols].copy()
    edited = st.data_editor(
        editable,
        width="stretch",
        num_rows="fixed",
        column_config={
            "source_type": st.column_config.SelectboxColumn(options=SOURCE_TYPES),
            "status": st.column_config.SelectboxColumn(options=STATUS_OPTIONS),
            "drafted": st.column_config.SelectboxColumn(options=["", "yes"]),
            "flag": st.column_config.SelectboxColumn(options=["", "yes"]),
            "url": st.column_config.LinkColumn(),
        },
        hide_index=True,
        key=f"compiled_editor_{pid}",
    )
    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("Save edits", key=f"save_compiled_{pid}", type="primary"):
            for _, r in edited.iterrows():
                k = r["key"]
                if k:
                    df = update_row(df, k, {c: r[c] for c in show_cols if c != "key"})
            save_sources(project, df)
            st.success("Saved.")
            st.rerun()
    with c2:
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        st.download_button(
            "Download sources.xlsx",
            buf.getvalue(),
            file_name="sources.xlsx",
            key=f"dl_xlsx_{pid}",
        )


if __name__ == "__main__":
    main()
