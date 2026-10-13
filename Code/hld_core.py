"""Shared logic for the API: hybrid retrieval, grounded Q&A, review and export. All models run on the CPU."""
import json, re, sqlite3, threading, datetime as _dt
from pathlib import Path
from typing import List
import requests
from pydantic import BaseModel

ROOT   = Path(__file__).resolve().parent.parent
STORE  = ROOT / "Code" / "store"
CFG    = json.load(open(ROOT / "Model_Prompts_Config" / "config.json"))
QA_SYS = (ROOT / "Model_Prompts_Config" / "qa_system.txt").read_text()
CHUNKS = json.load(open(STORE / "chunks.json"))
CHUNK_IDX = {c["chunk_id"]: i for i, c in enumerate(CHUNKS)}

def now(): return _dt.datetime.now().isoformat(timespec="seconds")
def db():
    con = sqlite3.connect(STORE / "app.db"); con.row_factory = sqlite3.Row; return con
def audit(actor, action, target, detail=""):
    with db() as con:
        con.execute("INSERT INTO audit_log(ts,actor,action,target,detail) VALUES(?,?,?,?,?)", (now(), actor, action, target, detail))

def tok(s):
    ids = re.findall(r"[a-z0-9_.]+", s.lower())
    return ids + [p for t in ids for p in t.split("_") if p and p != t]

_R, _LOCK = {}, threading.Lock()
def _res():
    with _LOCK:
        if not _R:
            from sentence_transformers import SentenceTransformer, CrossEncoder
            from rank_bm25 import BM25Okapi
            import chromadb
            _R["embedder"] = SentenceTransformer(CFG["EMBED_MODEL"], device="cpu")
            _R["reranker"] = CrossEncoder(CFG["RERANK_MODEL"], device="cpu")
            _R["coll"]     = chromadb.PersistentClient(path=str(STORE / "chroma")).get_collection(f'{CFG["PROJECT_ID"]}__hld')
            _R["bm25"]     = BM25Okapi([tok(c["text"]) for c in CHUNKS])
    return _R
def warm(): _res()

def _where(version=None, status="approved"):
    conds = [{"status": status}] + ([{"version": version}] if version else [])
    return conds[0] if len(conds) == 1 else {"$and": conds}

def search_chroma(q, k=10, version=None):
    R = _res()
    qv = R["embedder"].encode([CFG["QUERY_PREFIX"] + q], normalize_embeddings=True).tolist()
    r = R["coll"].query(query_embeddings=qv, n_results=k, where=_where(version))
    return r["ids"][0]

def search_bm25(q, k=10, version=None):
    sc = _res()["bm25"].get_scores(tok(q))
    ok = [i for i, c in enumerate(CHUNKS) if c["status"] == "approved" and (version is None or c["version"] == version)]
    return [CHUNKS[i]["chunk_id"] for i in sorted(ok, key=lambda i: -sc[i])[:k] if sc[i] > 0]

def hybrid_search(q, k=3, version=None, n_cand=10):
    fused = {}
    for lst in (search_chroma(q, n_cand, version), search_bm25(q, n_cand, version)):
        for rank, cid in enumerate(lst):
            fused[cid] = fused.get(cid, 0) + 1 / (60 + rank + 1)
    cands = sorted(fused, key=lambda c: -fused[c])[:n_cand]
    if not cands: return []
    scores = _res()["reranker"].predict([(q, CHUNKS[CHUNK_IDX[c]]["text"]) for c in cands])
    order = sorted(zip(cands, scores), key=lambda x: -x[1])[:k]
    return [dict(id=c, score=float(s), meta=CHUNKS[CHUNK_IDX[c]]) for c, s in order]

class Answer(BaseModel):
    found: bool
    answer: str
    citations: List[int]

def ask_llm(q, version=None, k=3, actor="user"):
    hits = hybrid_search(q, k=k, version=version)
    top = hits[0]["score"] if hits else -99.0
    if top < CFG["MIN_LOGIT"]:
        res = dict(question=q, status="out_of_scope", answer="This question is not covered by the approved HLD documents.",
                   citations=[], confidence="n/a", top_logit=top, evidence=[h["id"] for h in hits])
    else:
        ev = "\n\n".join(f'EVIDENCE [{i}]\n{h["meta"]["text"]}' for i, h in enumerate(hits, 1))
        r = requests.post(f'{CFG["OLLAMA_URL"]}/api/chat', timeout=900, json=dict(
            model=CFG["LLM_MODEL"], stream=False, format=Answer.model_json_schema(), options=CFG["LLM_OPTIONS"],
            messages=[dict(role="system", content=QA_SYS), dict(role="user", content=f"{ev}\n\nQUESTION: {q}")]))
        r.raise_for_status()
        a = Answer.model_validate_json(r.json()["message"]["content"])
        valid = sorted({n for n in a.citations if 1 <= n <= len(hits)})
        cites = [dict(n=n, doc_id=hits[n-1]["meta"]["doc_id"], version=hits[n-1]["meta"]["version"],
                      section=hits[n-1]["meta"]["breadcrumb"], page=hits[n-1]["meta"]["page_start"]) for n in valid]
        status = "not_found" if not a.found else ("answered" if valid else "unverified")
        conf = "high" if top > 2 else "medium" if top > -2 else "low"          # indicator only, not a probability
        res = dict(question=q, status=status, answer=a.answer, citations=cites, confidence=conf,
                   top_logit=top, evidence=[h["id"] for h in hits])
    res["version"] = version
    audit(actor, "ask", q[:60], f'{res["status"]} conf={res["confidence"]}')
    return res

# ---------- documents, review, export ----------
TABLES   = {"components": ["name", "kind", "runnable", "period_ms"],
            "ports": ["port", "dir", "interface", "itype", "element", "dtype", "unit"], "findings": []}
STATUSES = ("pending", "accepted", "rejected", "edited")

def list_documents():
    with db() as con:
        return [dict(r) for r in con.execute("SELECT doc_id, version, status, pages, uploaded_at FROM documents ORDER BY version")]

def list_rows(table, doc_id):
    if table not in TABLES: raise ValueError("unknown table")
    sql = f"SELECT * FROM {table} WHERE doc_id=?" if table == "findings" else f"SELECT rowid AS id, * FROM {table} WHERE doc_id=?"
    with db() as con:
        return [dict(r) for r in con.execute(sql + " ORDER BY 1", (doc_id,))]

def review(table, row_id, status, updates, actor):
    if table not in TABLES: raise ValueError("unknown table")
    if status not in STATUSES: raise ValueError("invalid status")
    updates = {k: v for k, v in (updates or {}).items() if k in TABLES[table]}
    if "dir" in updates and updates["dir"] not in ("P", "R"): raise ValueError("dir must be P or R")
    if "itype" in updates and updates["itype"] not in ("Sender-Receiver", "Client-Server"): raise ValueError("invalid interface type")
    if "period_ms" in updates: updates["period_ms"] = int(updates["period_ms"])
    with db() as con:
        row = con.execute(f"SELECT * FROM {table} WHERE rowid=?", (row_id,)).fetchone()
        if row is None: raise LookupError("row not found")
        row = dict(row)
        changes = {k: [row[k], v] for k, v in updates.items() if row[k] != v}
        if changes and status in ("pending", "edited"): status = "edited"
        sets, vals = ["review_status=?"], [status]
        for k, v in updates.items(): sets.append(f"{k}=?"); vals.append(v)
        con.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE rowid=?", vals + [row_id])
    audit(actor, "review", f"{table}#{row_id}", json.dumps(dict(status=status, changes=changes)))
    return dict(id=row_id, status=status, changes=changes)

def export(doc_id):
    return {t: [r for r in list_rows(t, doc_id) if r["review_status"] != "rejected"] for t in TABLES}


def list_flow(doc_id):
    with db() as con:
        try: return [dict(r) for r in con.execute("SELECT component, report, page FROM flow_reports WHERE doc_id=? ORDER BY rowid", (doc_id,))]
        except sqlite3.OperationalError: return []


# ---------- routing and citation check (added) ----------
import qa_router

def ask(q, version=None, k=3, actor="user"):
    r = qa_router.route(q, version, STORE)
    if r is not None:
        audit(actor, "ask_db", q[:60], f'{r["status"]} route={r["route"]}')
        return r
    res = ask_llm(q, version, k, actor)
    cited = [CHUNKS[CHUNK_IDX[res["evidence"][c["n"] - 1]]]["text"] for c in res["citations"]]
    res.setdefault("source", "llm")
    return qa_router.check_llm_result(res, cited)
