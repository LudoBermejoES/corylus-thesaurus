#!/usr/bin/env python3
"""Merge SAP-dictionary extraction into thesaurus/derived/es_synonyms.jsonl.

- Aggregates raw extraction by word (variants already expanded upstream).
- For each word:
    existing -> union synonyms, union antonyms, union paronyms.
    new      -> create entry with POS inferred from morphology.
- Optional fields (antonyms/paronyms) written only when non-empty.
"""
import json, re, os
from collections import defaultdict, OrderedDict

RAW = "thesaurus/derived/raw_es_sap.jsonl"
PATH = "thesaurus/derived/es_synonyms.jsonl"

CYR = re.compile(r"[А-Яа-яЁё]")

def ok(t):
    t = (t or "").strip()
    if not t or CYR.search(t):
        return None
    if re.fullmatch(r"\d{1,4}", t):
        return None
    if len(t) > 40:
        return None
    return t

def union(base, extra):
    seen = OrderedDict((x.lower(), x) for x in base)
    for x in extra:
        c = ok(x)
        if c and c.lower() not in seen:
            seen[c.lower()] = c
    return list(seen.values())

def infer_pos(word):
    w = word.lower().strip()
    base = w[:-2] if w.endswith("se") and len(w) > 4 else w
    if base.endswith("mente"):
        return "r"
    if re.search(r"(ar|er|ir)$", base) and len(base) > 3:
        return "v"
    return "n"

def main():
    syn = defaultdict(list); ant = defaultdict(list); par = defaultdict(list)
    with open(RAW, encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            w = ok(e.get("word"))
            if not w:
                continue
            syn[w] += e.get("synonyms") or []
            ant[w] += e.get("antonyms") or []
            par[w] += e.get("paronyms") or []

    existing = OrderedDict()
    with open(PATH, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            existing[d["word"]] = d

    st = dict(pdf_words=len(syn), existing=len(existing), updated=0, added=0,
              syn_added=0, ant_added=0, par_added=0)

    for w in syn:
        s = [x for x in (ok(t) for t in syn[w]) if x and x.lower() != w.lower()]
        a = [x for x in (ok(t) for t in ant[w]) if x and x.lower() != w.lower()]
        p = [x for x in (ok(t) for t in par[w]) if x and x.lower() != w.lower()]
        if w in existing:
            d = existing[w]
            ns = union(d.get("synonyms", []), s)
            st["syn_added"] += len(ns) - len(d.get("synonyms", []))
            d["synonyms"] = ns
            if a:
                na = union(d.get("antonyms", []), a)
                st["ant_added"] += len(na) - len(d.get("antonyms", []))
                d["antonyms"] = na
            if p:
                npar = union(d.get("paronyms", []), p)
                st["par_added"] += len(npar) - len(d.get("paronyms", []))
                d["paronyms"] = npar
            st["updated"] += 1
        else:
            if not (s or a or p):
                continue
            d = {"word": w, "synonyms": s, "pos": infer_pos(w)}
            if a: d["antonyms"] = a
            if p: d["paronyms"] = p
            existing[w] = d
            st["added"] += 1

    rows_ant = rows_par = 0
    with open(PATH + ".tmp", "w", encoding="utf-8") as f:
        for w in sorted(existing):
            d = existing[w]
            obj = {"word": d["word"], "synonyms": d.get("synonyms", []),
                   "pos": d.get("pos", "?")}
            if d.get("antonyms"): obj["antonyms"] = d["antonyms"]; rows_ant += 1
            if d.get("paronyms"): obj["paronyms"] = d["paronyms"]; rows_par += 1
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    os.replace(PATH + ".tmp", PATH)
    st["total_rows"] = len(existing)
    st["rows_with_antonyms"] = rows_ant
    st["rows_with_paronyms"] = rows_par
    print(json.dumps(st, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
