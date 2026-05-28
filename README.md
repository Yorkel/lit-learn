# lit-learn

Two single-user Streamlit apps that share one Neon Postgres database.

| Folder | App | Purpose |
|---|---|---|
| [`learning-app/`](learning-app/) | **Learning Notes** | Modules → sections → topics with notes, resources, pomodoro |
| [`lit-review-tool-staging/`](lit-review-tool-staging/) | **Literature Review** | Multi-project paper-writing helper (one project = one paper) |

Both apps run as separate deployments on Streamlit Community Cloud, point at the same Neon database (different tables — `ln_*` vs `lr_*`), and gate behind an app-specific password.

---

## Architecture

```
                 ┌───────────────────────────────┐
                 │  Neon Postgres (eu-west-2)    │
                 │  one db, 13 tables in public  │
                 │  ln_courses, ln_modules, ...  │
                 │  lr_projects, lr_sources, ... │
                 └───────────┬───────────────────┘
                             │
            ┌────────────────┴────────────────┐
            ▼                                 ▼
  ┌─────────────────────┐         ┌─────────────────────┐
  │ Learning Notes app  │         │ Lit-Review tool app │
  │ Streamlit Cloud     │         │ Streamlit Cloud     │
  │ password gate       │         │ password gate       │
  └─────────────────────┘         └─────────────────────┘
```

Each app reads `NEON_DATABASE_URL` from env (locally) or Streamlit Cloud secrets (deployed). Connections go through Neon's transaction pooler (port 6543, host `*-pooler.…`) which gives Streamlit's many short-lived sessions a friendly front door.

---

## First-time deploy to Streamlit Community Cloud

You'll deploy **two separate apps** from the same GitHub repo, each pointing at one of the two subfolder main files.

### 1. Push this repo to GitHub
```bash
cd /workspaces/lit-learn
git add .
git commit -m "wire both apps to neon"
git push
```

### 2. Confirm your Neon connection string is in [.env](.env) (gitignored)
```
NEON_DATABASE_URL=postgresql://user:pwd@ep-xxx-pooler.eu-west-2.aws.neon.tech/neondb?sslmode=require
```

### 3. Create the Learning Notes app on Streamlit Cloud
1. Go to [share.streamlit.io](https://share.streamlit.io) → **New app**
2. **Repository:** point at this repo
3. **Branch:** `main`
4. **Main file path:** `learning-app/app.py`
5. **App URL:** pick a subdomain like `yorkel-learning-notes`
6. **Advanced settings → Secrets** — paste:
   ```toml
   NEON_DATABASE_URL = "postgresql://user:pwd@ep-xxx-pooler.eu-west-2.aws.neon.tech/neondb?sslmode=require"
   NOTES_PASSWORD = "pick-a-password"
   ANTHROPIC_API_KEY = "sk-ant-..."   # optional, enables the ✨ buttons
   ```
7. **Deploy** — first build takes ~2 min. Bookmark the URL.

### 4. Create the Lit-Review app on Streamlit Cloud
Same flow, different main file:
- **Main file path:** `lit-review-tool-staging/lit_review_app.py`
- **App URL:** e.g. `yorkel-lit-review`
- **Secrets:**
  ```toml
  NEON_DATABASE_URL = "...same as above..."
  LIT_REVIEW_PASSWORD = "pick-a-password"
  ANTHROPIC_API_KEY = "sk-ant-..."
  ```

Both apps will share the same Neon database but never see each other's tables (different prefixes, different code).

---

## Running locally

```bash
# Make sure .env at the repo root contains NEON_DATABASE_URL=...
# Each app's db.py auto-loads .env from its own dir, the parent dir, and cwd.

# Learning Notes
cd learning-app
pip install -r requirements.txt
streamlit run app.py        # → http://localhost:8501

# Lit-Review tool (in another shell)
cd lit-review-tool-staging
pip install -r requirements.txt
streamlit run lit_review_app.py    # → http://localhost:8501
```

Password gates honour `NOTES_PASSWORD` / `LIT_REVIEW_PASSWORD` env vars or `.streamlit/secrets.toml` (copy from `secrets.toml.template`). If unset, no password is asked.

---

## Database

| Aspect | Detail |
|---|---|
| Provider | Neon (free tier, 3 projects, 0.5 GB) |
| Region | AWS eu-west-2 (London) |
| Schema | [`supabase/schema.sql`](supabase/schema.sql) — yes, despite the folder name, this is the Neon-compatible schema (we pivoted from Supabase mid-build; left for reference) |
| Roles | Single Neon role (`neondb_owner`) — no RLS, no PostgREST |
| Tables | `ln_*` × 6 (Learning Notes), `lr_*` × 7 (Lit Review) |

To re-create from scratch on a fresh Neon project, run [supabase/schema.sql](supabase/schema.sql) against the new database — strip the `grant ... to anon/authenticated/service_role` lines (they only existed for the abandoned Supabase setup) and you're done.

---

## Known limitations

- **Streamlit reruns the whole script on every interaction** — feels laggy on click. Mitigation is `@st.fragment` (Streamlit ≥1.32) wrapping individual interactive panels. Lit-review tool already uses it for the pomodoro; learning app doesn't yet. A focused optimization pass (~2-3 hrs) would noticeably improve perceived latency on both.
- **Learning Notes does not have a true WYSIWYG editor** — `st.text_area` is plain text. `mockup_v7.html` shows the intended toolbar but Streamlit can't deliver it natively. `streamlit-quill` is the recommended fix when you're ready.
- **No multi-user awareness** — both apps assume one user at a time. Opening either in two browser tabs and editing concurrently will cause one tab's saves to clobber the other's.

---

## Repo layout

```
.
├── .env                            # gitignored — NEON_DATABASE_URL lives here for local dev
├── README.md                       # this file
├── supabase/
│   └── schema.sql                  # canonical Postgres schema (works on Neon as-is)
├── learning-app/
│   ├── app.py                      # Streamlit app
│   ├── db.py                       # Neon data layer
│   ├── seed_from_docx.py           # one-off seeder (legacy, file-based)
│   ├── requirements.txt
│   ├── secrets.toml.template
│   ├── learning_notes_data.json    # historical seed (data now lives in Neon)
│   ├── mockup_v7.html              # design reference
│   └── CLAUDE.md
└── lit-review-tool-staging/
    ├── lit_review_app.py           # Streamlit app
    ├── db.py                       # Neon data layer
    ├── seed_from_bib.py            # one-off seeder (legacy, file-based)
    ├── requirements.txt
    ├── secrets.toml.template
    ├── README.md
    └── CLAUDE.md
```
