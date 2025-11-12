from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional

from db import init_db, SessionLocal
from models import License, create_all
from run_job import run_ut_job

app = FastAPI(title="State License Verifier (Flat Files)")

@app.on_event("startup")
def startup():
    create_all()
    init_db()

@app.get("/healthz")
def healthz():
    return {"ok": True}

class RunRequest(BaseModel):
    providers: List[str]

@app.post("/run")
def run(req: RunRequest):
    try:
        result = run_ut_job(req.providers)
        return {"ok": True, "summary": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/licenses")
def list_licenses(provider: Optional[str] = Query(None), state: Optional[str] = Query(None)):
    session = SessionLocal()
    try:
        q = session.query(License)
        if provider:
            q = q.filter(License.full_name.ilike(f"%{provider}%"))
        if state:
            q = q.filter(License.state == state.upper())
        items = [l.as_dict() for l in q.all()]
        return {"items": items}
    finally:
        session.close()
