import json, requests, pandas as pd, streamlit as st

st.set_page_config(page_title="AUTOSAR HLD Assistant", layout="wide")
st.title("AUTOSAR HLD Document Analysis Assistant")
st.caption("AI-assisted. Every output must be reviewed by a qualified engineer before it is used for design, compliance or release.")

with st.sidebar:
    api = st.text_input("API URL", "http://localhost:8000")
    key = st.text_input("API key", type="password")

def call(method, path, **kw):
    try:
        r = requests.request(method, api + path, headers={"X-API-Key": key}, timeout=900, **kw)
    except requests.ConnectionError:
        st.error("Cannot reach the API. Is it running?"); st.stop()
    if r.status_code != 200:
        try: msg = r.json().get("detail", r.text)
        except Exception: msg = r.text
        st.error(f"{r.status_code}: {msg}"); st.stop()
    return r

if not key:
    st.info("Enter your API key in the sidebar to start."); st.stop()
me   = call("GET", "/whoami").json()
docs = call("GET", "/documents").json()
st.sidebar.success(f'Signed in as {me["user"]} ({me["role"]})')
doc_id   = st.sidebar.selectbox("Document", [d["doc_id"] for d in docs])
can_edit = me["role"] in ("engineer", "admin")
st.session_state.setdefault("rev", 0)

STATUSES = ["pending", "accepted", "rejected", "edited"]
CONFIG = {
  "components": dict(show=["id", "name", "kind", "runnable", "period_ms", "page", "grounded", "review_status"], edit=["name", "kind", "runnable", "period_ms"]),
  "ports": dict(show=["id", "component", "port", "dir", "interface", "itype", "element", "dtype", "unit", "page", "grounded", "review_status"],
                edit=["port", "dir", "interface", "itype", "element", "dtype", "unit"]),
  "findings": dict(show=["id", "type", "severity", "location", "page", "message", "review_status"], edit=[]),
}

def review_table(table):
    cfg = CONFIG[table]
    df = pd.DataFrame(call("GET", f"/entities/{table}", params=dict(doc_id=doc_id)).json())
    if df.empty:
        st.info("No rows yet. Run the extraction and check cells in the notebook for this document."); return
    df = df[cfg["show"]].set_index("id")
    editable = (["review_status"] + cfg["edit"]) if can_edit else []
    colcfg = {"review_status": st.column_config.SelectboxColumn("review_status", options=STATUSES, required=True),
              "dir": st.column_config.SelectboxColumn("dir", options=["P", "R"]),
              "itype": st.column_config.SelectboxColumn("itype", options=["Sender-Receiver", "Client-Server"])}
    edited = st.data_editor(df, column_config={k: v for k, v in colcfg.items() if k in df.columns},
                            disabled=[c for c in df.columns if c not in editable], use_container_width=True,
                            key=f'ed_{table}_{doc_id}_{st.session_state["rev"]}')
    if not can_edit: st.caption("Your role is read-only. Engineers and admins can review.")
    elif st.button("Save review decisions", key=f'save_{table}_{doc_id}'):
        n = 0
        for rid in df.index:
            o, e = df.loc[rid], edited.loc[rid]
            upd = {c: (e[c].item() if hasattr(e[c], "item") else e[c]) for c in cfg["edit"] if e[c] != o[c]}
            if upd or e["review_status"] != o["review_status"]:
                call("POST", "/review", json=dict(table=table, id=int(rid), status=e["review_status"], updates=upd)); n += 1
        st.session_state["rev"] += 1
        st.success(f"Saved {n} change(s)."); st.rerun()

t_ask, t_ent, t_find, t_exp, t_aud = st.tabs(["Ask", "Components and ports", "Findings", "Export", "Audit log"])

with t_ask:
    versions = ["All approved versions"] + sorted({d["version"] for d in docs if d["status"] == "approved"})
    ver = st.selectbox("Document version", versions)
    q = st.text_input("Question", placeholder="Which components consume the vehicle speed?")
    if st.button("Ask") and q.strip():
        with st.spinner("Retrieving evidence and generating a grounded answer on the local CPU model (this can take a minute)..."):
            res = call("POST", "/ask", json=dict(question=q, version=None if ver.startswith("All") else ver)).json()
        st.session_state.setdefault("history", []).insert(0, res)
    for res in st.session_state.get("history", [])[:5]:
        st.markdown(f'**Q:** {res["question"]}')
        line = f'Status: **{res["status"]}** | confidence indicator: **{res["confidence"]}** | top rerank score: {res["top_logit"]:.1f}'
        (st.success if res["status"] == "answered" else st.warning)(line)
        st.write(res["answer"])
        if res["status"] == "unverified": st.error("The model gave an answer without a valid citation. Do not rely on it.")
        if res["citations"]: st.dataframe(pd.DataFrame(res["citations"]), hide_index=True, use_container_width=True)
        st.caption("Verify against the cited page before use. Missing items (for example a port with no provider) are found by the Findings checks, not by Q&A.")
        st.divider()

with t_ent:
    which = st.radio("Show", ["components", "ports"], horizontal=True)
    review_table(which)

with t_find:
    st.caption("Rule-based consistency checks over the extracted ports (no LLM involved).")
    review_table("findings")

with t_exp:
    st.caption("Rejected rows are excluded from exports. Review status is included in every row.")
    exp = call("GET", f"/export/{doc_id}").json()
    st.download_button("Download JSON (all tables)", json.dumps(exp, indent=2), file_name=f"{doc_id}_export.json")
    for t in ("components", "ports", "findings"):
        csv_text = call("GET", f"/export/{doc_id}", params=dict(fmt="csv", table=t)).text
        st.download_button(f"Download {t}.csv", csv_text, file_name=f"{doc_id}_{t}.csv", key=f"dl_{t}")

with t_aud:
    if me["role"] == "admin":
        st.dataframe(pd.DataFrame(call("GET", "/audit").json()), hide_index=True, use_container_width=True)
    else:
        st.info("The audit log is visible to admins only.")
