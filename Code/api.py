import csv, io, json
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional
import requests
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
import hld_core as core

USERS = json.load(open(core.STORE / "users.json"))                  # {api_key: {user, role}}
RANK  = {"viewer": 1, "engineer": 2, "admin": 3}

@asynccontextmanager
async def lifespan(app):
    core.warm()                                                    # load embedding and rerank models once, on the CPU
    yield

app = FastAPI(title="AUTOSAR HLD Analysis Assistant API", lifespan=lifespan)

def auth(min_role):
    def dep(x_api_key: str = Header(default="")):
        u = USERS.get(x_api_key)
        if not u: raise HTTPException(401, "Invalid or missing API key")
        if RANK[u["role"]] < RANK[min_role]: raise HTTPException(403, f"Requires role '{min_role}' or higher")
        return u
    return dep

class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    version: Optional[str] = None

class ReviewIn(BaseModel):
    table: str
    id: int
    status: str
    updates: Dict[str, Any] = {}

@app.get("/health")
def health(): return dict(status="ok", llm=core.CFG["LLM_MODEL"], chunks=len(core.CHUNKS))

@app.get("/whoami")
def whoami(u=Depends(auth("viewer"))): return u

@app.get("/documents")
def documents(u=Depends(auth("viewer"))): return core.list_documents()

@app.post("/ask")
def ask(body: AskIn, u=Depends(auth("viewer"))):
    try:
        return core.ask(body.question.strip(), body.version, actor=u["user"])
    except requests.ConnectionError:
        raise HTTPException(503, "The local LLM server (Ollama) is not reachable")

@app.get("/entities/{table}")
def entities(table: str, doc_id: str, u=Depends(auth("viewer"))):
    if table not in core.TABLES: raise HTTPException(404, "unknown table")
    return core.list_rows(table, doc_id)

@app.post("/review")
def review(body: ReviewIn, u=Depends(auth("engineer"))):
    try:
        return core.review(body.table, body.id, body.status, body.updates, u["user"])
    except ValueError as e: raise HTTPException(400, str(e))
    except LookupError as e: raise HTTPException(404, str(e))

@app.get("/export/{doc_id}")
def export(doc_id: str, fmt: str = "json", table: str = "ports", u=Depends(auth("viewer"))):
    data = core.export(doc_id)
    core.audit(u["user"], "export", doc_id, f"{fmt}:{table if fmt == 'csv' else 'all'}")
    if fmt == "json": return data
    rows = data.get(table, [])
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    return PlainTextResponse(buf.getvalue(), media_type="text/csv")

@app.get("/audit")
def audit_log(limit: int = 200, u=Depends(auth("admin"))):
    with core.db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))]
