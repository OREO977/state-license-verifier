# State License Verifier — Utah first (flat files, no folders)

## What this is
- A tiny FastAPI app with a few flat files (no folders).
- `/healthz` → health check
- `/run` → pass provider names; saves license rows (UT only for now; mocked data)
- `/licenses` → read back stored rows

## Files (what each does)
- `main.py` — the API endpoints (FastAPI)
- `db.py` — database connection (uses SQLite by default; Postgres if you set DATABASE_URL)
- `models.py` — the Licenses table structure
- `ut_adapter.py` — Utah logic (currently returns a fake record so you can test end-to-end)
- `run_job.py` — orchestrates a run: calls Utah, saves rows
- `requirements.txt` — Python packages to install

## Deploy on Render (free, Native)
1. Create a **Web Service** from this repo.
2. Set:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn main:app --host 0.0.0.0 --port 8080`
   - **Env var:** `PORT=8080`
   - **Plan:** Free
3. After deploy:
   - Open `/healthz`
   - Open `/docs` → run **POST /run** with:
     ```json
     {"providers": ["Gregory Osmond"]}
     ```
   - Check `/licenses` to see the stored record.
