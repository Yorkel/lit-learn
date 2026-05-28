"""
app.py — Learning Notes
=======================
Streamlit app matching the v7 mockup design.

Two screens:
  1. Overview — all modules, expandable to sections/topics, progress bars, add module/section/topic
  2. Topic — left map (full topic list), right rich-text notes editor, chat panel, resources table

Data: learning_notes_data.json (one file, version-controlled)
Time: learning_notes_time.json (pomodoro session log)
LLM:  ANTHROPIC_API_KEY env var (optional — enables Clean up, Chat, Summarise)
Auth: NOTES_PASSWORD env var (optional — password gate)

Run locally:
    streamlit run app.py

Deploy (Streamlit Community Cloud):
    Set NOTES_PASSWORD and ANTHROPIC_API_KEY in Secrets panel
"""

import os, time, datetime, random, string
import streamlit as st

from db import load_data, save_data, load_time, save_time, log_pomo, today_mins

# ── Config ────────────────────────────────────────────────────────────────────
def _secret(key: str) -> str:
    val = os.environ.get(key, "")
    if val:
        return val
    try:
        return st.secrets.get(key, "")
    except Exception:
        return ""

PASSWORD   = _secret("NOTES_PASSWORD")
HAS_LLM    = bool(_secret("ANTHROPIC_API_KEY"))
POMO_MINS  = 25
# Ensure ANTHROPIC_API_KEY is in env so the anthropic SDK picks it up
if HAS_LLM and not os.environ.get("ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = _secret("ANTHROPIC_API_KEY")

# ── Data schema ───────────────────────────────────────────────────────────────
# {
#   "course_title": str,
#   "course_sub": str,
#   "modules": [
#     {
#       "id": str,
#       "title": str,
#       "sections": [
#         {
#           "id": str,
#           "title": str,
#           "topics": [
#             {
#               "id": str,
#               "name": str,
#               "done": bool,
#               "notes_html": str,       # rich text stored as HTML
#               "resources": [           # list of resource dicts
#                 {"title": str, "type": str, "authors": str, "url": str, "reviewed": bool}
#               ]
#             }
#           ]
#         }
#       ]
#     }
#   ]
# }

def uid():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))

def find_mod(data, mid):
    return next((m for m in data["modules"] if m["id"] == mid), None)

def find_sec(mod, sid):
    return next((s for s in mod["sections"] if s["id"] == sid), None)

def find_topic(sec, tid):
    return next((t for t in sec["topics"] if t["id"] == tid), None)

def mod_counts(mod):
    done  = sum(1 for s in mod["sections"] for t in s["topics"] if t.get("done"))
    total = sum(len(s["topics"]) for s in mod["sections"])
    return done, total

def overall_counts(data):
    done  = sum(1 for m in data["modules"] for s in m["sections"] for t in s["topics"] if t.get("done"))
    total = sum(len(s["topics"]) for m in data["modules"] for s in m["sections"])
    return done, total

# ── LLM ──────────────────────────────────────────────────────────────────────
def llm_clean(text):
    import anthropic
    c = anthropic.Anthropic()
    r = c.messages.create(model="claude-sonnet-4-20250514", max_tokens=1000, messages=[{
        "role": "user",
        "content": ("Clean up these pasted course notes. Fix hyphenation artefacts, "
                    "broken line breaks, stray page numbers. Preserve structure. "
                    "Return only cleaned text.\n\n" + text)
    }])
    return r.content[0].text

def llm_chat(topic_name, notes_text, user_msg, history):
    import anthropic
    c = anthropic.Anthropic()
    system = (f"You are a helpful study assistant. The user is studying '{topic_name}'. "
              f"Their current notes are:\n\n{notes_text}\n\n"
              "Answer questions about this topic concisely and clearly.")
    msgs = history + [{"role": "user", "content": user_msg}]
    r = c.messages.create(model="claude-sonnet-4-20250514", max_tokens=500,
                           system=system, messages=msgs)
    return r.content[0].text

# ── Session state ─────────────────────────────────────────────────────────────
def init():
    if "data"       not in st.session_state: st.session_state.data = load_data()
    if "screen"     not in st.session_state: st.session_state.screen = "overview"
    if "mod_id"     not in st.session_state: st.session_state.mod_id = None
    if "sec_id"     not in st.session_state: st.session_state.sec_id = None
    if "topic_id"   not in st.session_state: st.session_state.topic_id = None
    if "pom_run"    not in st.session_state: st.session_state.pom_run = False
    if "pom_start"  not in st.session_state: st.session_state.pom_start = None
    if "pom_elapsed"not in st.session_state: st.session_state.pom_elapsed = 0
    if "pom_done"   not in st.session_state: st.session_state.pom_done = False
    if "pom_today"  not in st.session_state: st.session_state.pom_today = today_mins()
    if "chat_hist"  not in st.session_state: st.session_state.chat_hist = []
    if "authed"     not in st.session_state: st.session_state.authed = not bool(PASSWORD)

# ── Password ──────────────────────────────────────────────────────────────────
def auth_screen():
    st.markdown("## Learning Notes")
    pw = st.text_input("Password", type="password")
    if st.button("Enter"):
        if pw == PASSWORD:
            st.session_state.authed = True
            st.rerun()
        else:
            st.error("Wrong password.")

# ── Pomodoro ──────────────────────────────────────────────────────────────────
def pomodoro(sidebar=True):
    ctx = st.sidebar if sidebar else st
    total = POMO_MINS * 60
    elapsed = st.session_state.pom_elapsed
    if st.session_state.pom_run and st.session_state.pom_start:
        elapsed += time.time() - st.session_state.pom_start
    remaining = max(0, total - elapsed)
    m, s = int(remaining // 60), int(remaining % 60)
    ctx.markdown(f"**Pomodoro** — `{m:02d}:{s:02d}`")
    ctx.progress(min(1.0, elapsed / total))
    c1, c2 = ctx.columns(2)
    with c1:
        if st.session_state.pom_run:
            if ctx.button("⏸ Pause", key="pom_pause", use_container_width=True):
                st.session_state.pom_elapsed = elapsed
                st.session_state.pom_run = False
                st.session_state.pom_start = None
        else:
            if ctx.button("▶ Start", key="pom_start_btn", use_container_width=True):
                st.session_state.pom_start = time.time()
                st.session_state.pom_run = True
                st.session_state.pom_done = False
    with c2:
        if ctx.button("↺ Reset", key="pom_reset", use_container_width=True):
            if st.session_state.pom_run and elapsed > 60:
                log_pomo(elapsed / 60)
                st.session_state.pom_today += int(elapsed / 60)
            st.session_state.pom_run = False
            st.session_state.pom_start = None
            st.session_state.pom_elapsed = 0
            st.session_state.pom_done = False
    if remaining == 0 and not st.session_state.pom_done and elapsed > 0:
        log_pomo(POMO_MINS)
        st.session_state.pom_today += POMO_MINS
        st.session_state.pom_done = True
        st.session_state.pom_run = False
        ctx.success("🎉 Done! Take a break.")
    ctx.caption(f"Today: {st.session_state.pom_today} min")
    if st.session_state.pom_run:
        time.sleep(1)
        st.rerun()

# ── OVERVIEW SCREEN ───────────────────────────────────────────────────────────
def overview():
    data = st.session_state.data

    # Sidebar
    with st.sidebar:
        st.markdown(f"### {data['course_title']}")
        if data.get("course_sub"):
            st.caption(data["course_sub"])
        done, total = overall_counts(data)
        pct = int(done / total * 100) if total else 0
        st.markdown(f"**{done} / {total}** topics done · **{pct}%**")
        st.progress(pct / 100)
        st.divider()
        with st.expander("✏️ Edit course info"):
            t = st.text_input("Title", value=data["course_title"])
            s = st.text_input("Subtitle", value=data.get("course_sub",""))
            if st.button("Save"):
                data["course_title"] = t; data["course_sub"] = s
                save_data(data); st.rerun()
        with st.expander("➕ Add module"):
            nm = st.text_input("Module title", key="new_mod")
            if st.button("Add module"):
                if nm:
                    data["modules"].append({"id": uid(), "title": nm, "sections": []})
                    save_data(data); st.rerun()
        st.divider()
        pomodoro()

    # Header
    st.markdown(f"## {data['course_title']}")
    if data.get("course_sub"):
        st.caption(data["course_sub"])

    if not data["modules"]:
        st.info("No modules yet. Add one in the sidebar.")
        return

    for mod in data["modules"]:
        done, total = mod_counts(mod)
        pct = int(done / total * 100) if total else 0
        label = f"**{mod['title']}** — {done}/{total} · {pct}%"

        with st.expander(label, expanded=False):
            st.progress(pct / 100)

            # Add section
            c1, c2 = st.columns([4, 1])
            with c1:
                ns = st.text_input("", key=f"ns_{mod['id']}", placeholder="Add section name…",
                                   label_visibility="collapsed")
            with c2:
                if st.button("Add section", key=f"as_{mod['id']}"):
                    if ns:
                        mod["sections"].append({"id": uid(), "title": ns, "topics": []})
                        save_data(data); st.rerun()

            for sec in mod["sections"]:
                sd = sum(1 for t in sec["topics"] if t.get("done"))
                st_total = len(sec["topics"])
                sp = int(sd / st_total * 100) if st_total else 0
                st.markdown(f"**{sec['title']}** · {sd}/{st_total}")
                st.progress(sp / 100)

                for topic in sec["topics"]:
                    tc1, tc2, tc3 = st.columns([0.5, 5, 1.2])
                    with tc1:
                        done_v = st.checkbox("", value=topic.get("done", False),
                                             key=f"td_{topic['id']}", label_visibility="collapsed")
                        if done_v != topic.get("done", False):
                            topic["done"] = done_v; save_data(data); st.rerun()
                    with tc2:
                        st.markdown(topic["name"])
                    with tc3:
                        if st.button("Open →", key=f"op_{topic['id']}"):
                            st.session_state.mod_id   = mod["id"]
                            st.session_state.sec_id   = sec["id"]
                            st.session_state.topic_id = topic["id"]
                            st.session_state.screen   = "topic"
                            st.session_state.chat_hist = []
                            st.rerun()

                # Add topic
                ta1, ta2 = st.columns([4, 1])
                with ta1:
                    nt = st.text_input("", key=f"nt_{sec['id']}", placeholder="+ Add topic…",
                                       label_visibility="collapsed")
                with ta2:
                    if st.button("Add", key=f"at_{sec['id']}"):
                        if nt:
                            sec["topics"].append({
                                "id": uid(), "name": nt, "done": False,
                                "notes_html": "", "resources": []
                            })
                            save_data(data); st.rerun()
                st.markdown("---")

# ── TOPIC SCREEN ──────────────────────────────────────────────────────────────
def topic_screen():
    data = st.session_state.data
    mod  = find_mod(data, st.session_state.mod_id)
    sec  = find_sec(mod,  st.session_state.sec_id)
    topic = find_topic(sec, st.session_state.topic_id)

    if not topic:
        st.error("Topic not found.")
        st.session_state.screen = "overview"; st.rerun(); return

    # Sidebar — full topic map
    with st.sidebar:
        if st.button("← Overview"):
            st.session_state.screen = "overview"; st.rerun()
        st.markdown(f"**{mod['title']}**")
        done_m, total_m = mod_counts(mod)
        pct_m = int(done_m / total_m * 100) if total_m else 0
        st.progress(pct_m / 100)
        st.caption(f"{done_m}/{total_m} topics · {pct_m}%")
        st.divider()

        for s in mod["sections"]:
            st.markdown(f"**{s['title']}**")
            for t in s["topics"]:
                is_active = t["id"] == topic["id"]
                prefix = "→ " if is_active else "  "
                label = f"{prefix}{'✅' if t.get('done') else '○'} {t['name']}"
                if st.button(label, key=f"nav_{t['id']}", use_container_width=True):
                    st.session_state.sec_id   = s["id"]
                    st.session_state.topic_id = t["id"]
                    st.session_state.chat_hist = []
                    st.rerun()

        st.divider()
        pomodoro()

    # Mark done
    col_title, col_done = st.columns([5, 1])
    with col_title:
        st.markdown(f"## {topic['name']}")
        st.caption(f"{sec['title']} · {mod['title']}")
    with col_done:
        done_now = st.checkbox("Mark done", value=topic.get("done", False), key="topic_done")
        if done_now != topic.get("done", False):
            topic["done"] = done_now; save_data(data)

    tab_notes, tab_resources = st.tabs(["📝 Notes", f"📚 Resources ({len(topic.get('resources',[]))})"])

    # ── NOTES TAB ──
    with tab_notes:
        col_notes, col_chat = st.columns([3, 1]) if HAS_LLM else (st.container(), None)

        with col_notes:
            # Formatting toolbar (simulated with buttons)
            st.markdown("**Format:**")
            fb1,fb2,fb3,fb4,fb5,fb6,fb7 = st.columns(7)
            with fb1: h1 = st.button("H1", key="tb_h1", help="Heading 1")
            with fb2: h2 = st.button("H2", key="tb_h2", help="Heading 2")
            with fb3: h3 = st.button("H3", key="tb_h3", help="Heading 3")
            with fb4: bl = st.button("• List", key="tb_ul", help="Bullet list")
            with fb5: ol = st.button("1. List", key="tb_ol", help="Numbered list")
            with fb6: cb = st.button("```", key="tb_code", help="Code block")
            with fb7:
                if HAS_LLM:
                    clean = st.button("✨ Clean", key="tb_clean", help="Clean up with AI")

            # Note: Streamlit doesn't support a true WYSIWYG editor natively.
            # In the real app, use streamlit-quill or st-tiptap component.
            # For now, a text_area with a note to the developer.
            st.info("💡 In the deployed app, this is a rich text editor (Quill.js). "
                    "The toolbar above will apply formatting. See CLAUDE.md for setup.")

            notes = st.text_area(
                "Notes", value=topic.get("notes_html", ""), height=400,
                placeholder="Paste your notes here. Use the toolbar above for formatting.",
                key=f"notes_{topic['id']}", label_visibility="collapsed"
            )
            if notes != topic.get("notes_html", ""):
                topic["notes_html"] = notes; save_data(data)

            if HAS_LLM and clean and notes.strip():
                with st.spinner("Cleaning up…"):
                    topic["notes_html"] = llm_clean(notes)
                save_data(data); st.rerun()

            # Add to module doc
            if st.button("📄 Add to module doc", key="add_doc"):
                st.session_state.setdefault("module_doc", [])
                st.session_state.module_doc.append({
                    "topic": topic["name"], "section": sec["title"], "text": notes
                })
                st.success(f"Added. Module doc now has {len(st.session_state.module_doc)} entries.")

            # Download module doc
            doc_entries = st.session_state.get("module_doc", [])
            if doc_entries:
                md = f"# {mod['title']}\n\n"
                for e in doc_entries:
                    md += f"## {e['topic']}\n\n_{e['section']}_\n\n{e['text']}\n\n---\n\n"
                st.download_button(
                    "⬇️ Download module doc (.md)", md,
                    file_name=f"{mod['title'].replace(' ','_')}_notes.md",
                    mime="text/markdown"
                )

        # Chat panel
        if HAS_LLM and col_chat:
            with col_chat:
                st.markdown("**Chat about this topic**")
                for msg in st.session_state.chat_hist:
                    role = "You" if msg["role"] == "user" else "Claude"
                    st.markdown(f"**{role}:** {msg['content']}")
                    if msg["role"] == "assistant":
                        if st.button("+ Push to notes", key=f"push_{hash(msg['content'])}"):
                            topic["notes_html"] = (topic.get("notes_html","") +
                                                   f"\n\n[From chat] {msg['content']}")
                            save_data(data); st.rerun()

                user_q = st.text_input("Ask about this topic…", key="chat_q")
                if st.button("Send", key="chat_send") and user_q:
                    with st.spinner("…"):
                        reply = llm_chat(topic["name"], notes[:2000],
                                         user_q, st.session_state.chat_hist)
                    st.session_state.chat_hist.append({"role":"user","content":user_q})
                    st.session_state.chat_hist.append({"role":"assistant","content":reply})
                    st.rerun()

    # ── RESOURCES TAB ──
    with tab_resources:
        resources = topic.setdefault("resources", [])

        # Filter
        fc1, fc2, fc3 = st.columns([2, 1, 1])
        with fc1: q = st.text_input("Search", placeholder="Search resources…", label_visibility="collapsed", key="res_q")
        with fc2: type_f = st.selectbox("Type", ["All","video","paper","article","code","docs"], label_visibility="collapsed", key="res_type")
        with fc3: unrev_f = st.checkbox("Unreviewed only", key="res_unrev")

        filtered = resources
        if q: filtered = [r for r in filtered if q.lower() in (r.get("title","") + r.get("authors","")).lower()]
        if type_f != "All": filtered = [r for r in filtered if r.get("type") == type_f]
        if unrev_f: filtered = [r for r in filtered if not r.get("reviewed")]

        if filtered:
            for i, res in enumerate(filtered):
                rc1, rc2, rc3, rc4, rc5 = st.columns([0.4, 3.5, 1.2, 1.5, 0.5])
                with rc1:
                    rev = st.checkbox("", value=res.get("reviewed", False),
                                      key=f"rev_{topic['id']}_{i}", label_visibility="collapsed")
                    if rev != res.get("reviewed", False):
                        res["reviewed"] = rev; save_data(data)
                with rc2:
                    st.markdown(f"**{res['title']}**")
                with rc3:
                    st.caption(res.get("type",""))
                with rc4:
                    st.caption(res.get("authors",""))
                with rc5:
                    if res.get("url"):
                        st.markdown(f"[↗]({res['url']})")
        else:
            st.caption("No resources yet." if not resources else "No matches.")

        # Export
        if resources:
            import csv, io
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=["title","type","authors","url","reviewed"])
            w.writeheader(); w.writerows(resources)
            st.download_button("⬇️ Export .csv", buf.getvalue(),
                               file_name="resources.csv", mime="text/csv")

        st.divider()
        st.markdown("**Add resource**")
        ra1, ra2 = st.columns([3,1])
        with ra1: res_title = st.text_input("Title", key="res_title")
        with ra2: res_type  = st.selectbox("Type", ["video","paper","article","code","docs"], key="res_type_new")
        rb1, rb2 = st.columns(2)
        with rb1: res_authors = st.text_input("Authors / Source", key="res_authors")
        with rb2: res_url     = st.text_input("URL", key="res_url")
        if st.button("Add resource", key="add_res"):
            if res_title:
                resources.append({"title": res_title, "type": res_type,
                                  "authors": res_authors, "url": res_url, "reviewed": False})
                save_data(data); st.rerun()


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    st.set_page_config(page_title="Learning Notes", layout="wide",
                       initial_sidebar_state="expanded")
    init()
    if not st.session_state.authed:
        auth_screen(); return
    if st.session_state.screen == "overview":
        overview()
    elif st.session_state.screen == "topic":
        topic_screen()

if __name__ == "__main__":
    main()
