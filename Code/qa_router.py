"""Deterministic routing: structured questions are answered from the extracted SQLite rows (no LLM),
and LLM answers get a citation check."""
import json, re, sqlite3
from pathlib import Path

ALIASES  = {"throttle": "If_ThrottleRequest", "acceleration": "If_VehicleAccel"}
R_DIFF   = r"what.{0,15}changed|\bchanges?\b|\bdifferences?\b|what.s new|\bnew in\b|\badded in\b|compared (?:to|with)"
R_FIND   = r"inconsisten|mismatch|\bissues?\b|\bproblems?\b|\berrors?\b|\bfindings?\b|\bconflicts?\b"
R_ATTR   = r"interface type|data ?type|\bunit\b|data element"
R_PERIOD = r"\bperiod\b|how often|cycle time|periodic|\binterval\b|\brunnable"
R_PROV   = r"\bprovid\w*|\bsuppl(?:y|ies)\b|\bsends?\b|\bpublish\w*|\bproduc\w*"
R_CONS   = r"\bconsum\w*|\breceiv\w*|\buses?\b|\breads?\b|\brequir\w*|\bneeds?\b"
R_PORTS  = r"\bports?\b"
INTENTS  = (("diff", R_DIFF), ("findings", R_FIND), ("attr", R_ATTR), ("period", R_PERIOD),
            ("provides", R_PROV), ("consumes", R_CONS), ("ports", R_PORTS))

def _norm(s): return re.sub(r"[^a-z0-9]", "", s.lower())
def _vkey(v): return tuple(int(x) for x in re.findall(r"\d+", v))

_SEC = {}
def _section(store, doc_id, section_id):
    k = (str(store), doc_id)
    if k not in _SEC:
        try: _SEC[k] = {s["section_id"]: " > ".join(s["path"]) for s in json.load(open(Path(store) / f"sections_{doc_id}.json"))["sections"]}
        except Exception: _SEC[k] = {}
    return _SEC[k].get(section_id, "component description")

def _cite(store, d, comp):
    return dict(doc_id=d["doc_id"], version=d["version"], section=_section(store, d["doc_id"], comp["section_id"]), page=comp["page"])

def _res(q, status, answer, cites, version, route):
    seen, cs = set(), []
    for c in cites:
        k = (c["doc_id"], c["section"], c["page"])
        if k not in seen: seen.add(k); cs.append(dict(n=len(cs) + 1, **c))
    return dict(question=q, status=status, answer=answer, citations=cs, confidence="high", top_logit=None,
                evidence=[], version=version, source="database", route=route)

def _load(con, doc_id):
    comps = [dict(r) for r in con.execute("SELECT * FROM components WHERE doc_id=? AND review_status!='rejected'", (doc_id,))]
    ports = [dict(r) for r in con.execute("SELECT * FROM ports WHERE doc_id=? AND review_status!='rejected'", (doc_id,))]
    return comps, ports

def _findings(con, doc_id):
    try: return [dict(r) for r in con.execute("SELECT * FROM findings WHERE doc_id=? AND review_status!='rejected'", (doc_id,))]
    except sqlite3.OperationalError: return []

def route(q, version, store):
    con = sqlite3.connect(str(Path(store) / "app.db")); con.row_factory = sqlite3.Row
    try: return _route(q, version, store, con)
    finally: con.close()

def _route(q, version, store, con):
    ql = q.lower()
    intent = next((n for n, rx in INTENTS if re.search(rx, ql)), None)
    if intent is None: return None
    vers = sorted([dict(r) for r in con.execute("SELECT doc_id, version FROM documents WHERE status='approved'")], key=lambda d: _vkey(d["version"]))
    if not vers: return None
    data = {d["doc_id"]: _load(con, d["doc_id"]) for d in vers}

    # ---- which components / interface does the question mention? ----
    comp_names = sorted({c["name"] for cs, _ in data.values() for c in cs}, key=len, reverse=True)
    all_ports  = [p for _, ps in data.values() for p in ps]
    itf_names  = sorted({p["interface"] for p in all_ports}, key=len, reverse=True)
    elem2itf   = {p["element"]: p["interface"] for p in all_ports}
    qn, found_c, found_i = _norm(q), [], []
    for n in comp_names:
        key = _norm(n)[3:] if _norm(n).startswith("swc") else _norm(n)
        if key and key in qn: found_c.append(n); qn = qn.replace(key, " ")
    for n in itf_names:
        key = _norm(n)[2:] if _norm(n).startswith("if") else _norm(n)
        if key and key in qn: found_i.append(n); qn = qn.replace(key, " ")
    if not found_i:
        for el, itf in elem2itf.items():
            if _norm(el) and _norm(el) in qn: found_i.append(itf); break
    if not found_i:
        for al, itf in ALIASES.items():
            if al in qn and itf in itf_names: found_i.append(itf); break

    # ---- which version(s)? ----
    qm = re.search(r"\bv(?:ersion)?\s*(\d+\.\d+)", ql)
    qv = qm.group(1) if qm else None
    pick = (qv or version) if intent == "diff" else (version or qv)
    sel = [d for d in vers if d["version"] == pick] if pick else vers
    if not sel: return _res(q, "not_found", f"No approved document has version {pick}.", [], version, intent)

    cites, lines = [], []
    if intent in ("provides", "consumes"):
        if not found_i: return None
        want, verb, itf = ("P", "provides", found_i[0]) if intent == "provides" else ("R", "consumes", found_i[0])
        for d in sel:
            comps, ports = data[d["doc_id"]]; crow = {c["name"]: c for c in comps}
            hit = [p for p in ports if p["interface"] == itf and p["dir"] == want]
            opp = [p for p in ports if p["interface"] == itf and p["dir"] != want]
            if hit:
                s = f'v{d["version"]}: ' + "; ".join(f'{p["component"]} (port {p["port"]})' for p in hit) + f' {verb} {itf}.'
                if not opp: s += f' No component {"consumes" if want == "P" else "provides"} it.'
            else:
                s = f'v{d["version"]}: no component {verb} {itf}.'
                if opp:
                    s += f' It is {"required" if want == "P" else "provided"} by ' + "; ".join(f'{p["component"]}.{p["port"]}' for p in opp) + "."
                    if want == "P": s += " This is a missing-provider inconsistency."
            lines.append(s)
            cites += [_cite(store, d, crow[p["component"]]) for p in hit + opp if p["component"] in crow]
        return _res(q, "answered", "\n".join(lines), cites, version, intent)

    if intent == "attr":
        if not found_i and not found_c: return None
        fields = []
        if re.search(r"interface type", ql): fields.append(("itype", "interface type"))
        if re.search(r"data ?type", ql):     fields.append(("dtype", "data type"))
        if re.search(r"\bunit", ql):         fields.append(("unit", "unit"))
        if re.search(r"data element", ql):   fields.append(("element", "data element"))
        if not fields: fields = [("itype", "interface type"), ("dtype", "data type"), ("unit", "unit")]
        for d in sel:
            comps, ports = data[d["doc_id"]]; crow = {c["name"]: c for c in comps}
            rows = [p for p in ports if (not found_i or p["interface"] in found_i) and (not found_c or p["component"] in found_c)]
            if not rows: lines.append(f'v{d["version"]}: no matching port found.'); continue
            for p in rows:
                lines.append(f'v{d["version"]}: {p["component"]}.{p["port"]} ({p["interface"]}): ' + ", ".join(f"{lab} = {p[k]}" for k, lab in fields))
                if p["component"] in crow: cites.append(_cite(store, d, crow[p["component"]]))
            for k, lab in fields:
                if len(rows) > 1 and len({p[k] for p in rows}) > 1:
                    lines.append(f'   Note: the {lab} differs between these ports (see the consistency findings).')
        return _res(q, "answered", "\n".join(lines), cites, version, intent)

    if intent == "period":
        for d in sel:
            comps, _ = data[d["doc_id"]]
            for c in comps:
                if not found_c or c["name"] in found_c:
                    lines.append(f'v{d["version"]}: {c["name"]} runs {c["runnable"]} every {c["period_ms"]} ms.')
                    cites.append(_cite(store, d, c))
        return _res(q, "answered", "\n".join(lines) or "No matching component.", cites, version, intent)

    if intent == "ports":
        if not found_c: return None
        for d in sel:
            comps, ports = data[d["doc_id"]]
            for c in comps:
                if c["name"] in found_c:
                    ps = [p for p in ports if p["component"] == c["name"]]
                    lines.append(f'v{d["version"]}: {c["name"]} has ' + ", ".join(
                        f'{p["port"]} ({"provided" if p["dir"] == "P" else "required"}, {p["interface"]}, {p["itype"]})' for p in ps) + ".")
                    cites.append(_cite(store, d, c))
        return _res(q, "answered", "\n".join(lines), cites, version, intent)

    if intent == "findings":
        for d in sel:
            rows = _findings(con, d["doc_id"])
            lines.append(f'v{d["version"]}: {len(rows)} finding(s)')
            for r in rows:
                lines.append(f'   - {r["type"]} @ {r["location"]}: {r["message"]}')
                cites.append(dict(doc_id=d["doc_id"], version=d["version"], section="Rule-based consistency check", page=r["page"]))
        return _res(q, "answered", "\n".join(lines), cites, version, intent)

    if intent == "diff":
        target = sel[0] if pick else vers[-1]
        idx = vers.index(target)
        if idx == 0:
            return _res(q, "answered", f'v{target["version"]} is the first approved version, so there is nothing to compare it with.', [], version, intent)
        old = vers[idx - 1]
        oc, op = data[old["doc_id"]]; nc, np_ = data[target["doc_id"]]
        ocd, ncd = {c["name"]: c for c in oc}, {c["name"]: c for c in nc}
        opd, npd = {(p["component"], p["port"]): p for p in op}, {(p["component"], p["port"]): p for p in np_}
        for n in sorted(ncd.keys() - ocd.keys()):
            lines.append(f'Component added: {n} (runnable {ncd[n]["runnable"]}, {ncd[n]["period_ms"]} ms)'); cites.append(_cite(store, target, ncd[n]))
        for n in sorted(ocd.keys() - ncd.keys()):
            lines.append(f'Component removed: {n}'); cites.append(_cite(store, old, ocd[n]))
        for n in sorted(ocd.keys() & ncd.keys()):
            for fld, lab, unit in (("period_ms", "Period", " ms"), ("runnable", "Runnable", ""), ("kind", "Kind", "")):
                if ocd[n][fld] != ncd[n][fld]:
                    lines.append(f'{lab} of {n} changed from {ocd[n][fld]}{unit} to {ncd[n][fld]}{unit}'); cites.append(_cite(store, target, ncd[n]))
        for k in sorted(npd.keys() - opd.keys()):
            if k[0] in ocd: lines.append(f'Port added: {k[0]}.{k[1]}')
        for k in sorted(opd.keys() - npd.keys()):
            if k[0] in ncd: lines.append(f'Port removed: {k[0]}.{k[1]}')
        for k in sorted(opd.keys() & npd.keys()):
            for fld in ("dir", "interface", "itype", "element", "dtype", "unit"):
                if opd[k][fld] != npd[k][fld]: lines.append(f'Port {k[0]}.{k[1]}: {fld} changed from {opd[k][fld]} to {npd[k][fld]}')
        of = {(f["type"], f["location"]) for f in _findings(con, old["doc_id"])}
        nf = {(f["type"], f["location"]) for f in _findings(con, target["doc_id"])}
        for t, l in sorted(of - nf): lines.append(f'Finding resolved: {t} at {l}')
        for t, l in sorted(nf - of): lines.append(f'New finding: {t} at {l}')
        head = f'Changes from v{old["version"]} to v{target["version"]}:\n'
        return _res(q, "answered", head + ("\n".join("- " + s for s in lines) if lines else "No differences found."), cites, version, intent)
    return None

# ---------------- citation check for LLM answers ----------------
IDENT = re.compile(r"\b(?:SWC|Rp|Pp|If|Rn)_[A-Za-z0-9_]+")

def verify(answer, cited_texts):
    if not cited_texts: return False, ["no citation"]
    blob = "\n".join(cited_texts)
    if len(re.findall(r"[A-Za-z]{3,}", answer)) < 2: return False, ["answer has no readable words"]
    missing = [t for t in sorted(set(IDENT.findall(answer))) if t not in blob]
    toks = {t.strip(".") for t in re.findall(r"[A-Za-z0-9_.]+", blob)}
    missing += [n for n in re.findall(r"\b\d+(?:\.\d+)?\b", answer) if n not in toks]
    if not IDENT.findall(answer) and not re.search(r"\d", answer):
        bl = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", blob)}
        cw = [w.lower() for w in re.findall(r"[A-Za-z]{4,}", answer)]
        if cw and sum(w in bl for w in cw) / len(cw) < 0.5: missing.append("answer wording not found in the cited text")
    return (not missing), missing

def check_llm_result(res, cited_texts):
    if res.get("status") != "answered": return res
    ok, missing = verify(res["answer"], cited_texts)
    res["citation_check"] = "passed" if ok else "failed"
    if not ok: res["status"] = "unverified"; res["unsupported"] = missing
    return res
