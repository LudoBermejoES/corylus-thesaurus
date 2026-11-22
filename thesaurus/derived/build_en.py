#!/usr/bin/env python3
"""Build en_dict.jsonl.gz: Open English WordNet senses + Wiktionary + Moby.

Same output schema as convert_wiktionary.py (the one rust-thesaurus reads):
  {"word": "...", "senses": [{"pos", "definition", "synonyms", "antonyms"}]}

For every lowercase word that Open English WordNet knows, the senses are
WordNet's synsets (frequency order, WordNet definitions). Each sense's
synonym list is ranked from:
  - the other members of the synset, and of "similar" synsets (adjectives);
  - the synonyms of the Wiktionary sense that best matches it;
  - common hyponyms (walk -> stroll, amble; house -> cottage, mansion);
  - Moby Thesaurus II words, only when WordNet has them in the sense's part
    of speech, Moby lists them both ways, and their own WordNet/Wiktionary
    synonyms share >=3 words with the sense (Moby itself is one flat, noisy list per headword).
Wiktionary senses that match no WordNet sense are appended. Words WordNet
does not have (names, inflected forms, slang...) keep their Wiktionary
senses unchanged, so the word list the app checks spelling against does not
shrink.

Filters: no capitalised synonyms for lowercase words, nothing Wiktionary tags
only as offensive/vulgar/derogatory or only as obsolete/archaic, nothing
wordfreq has never seen (unless WordNet lists it in the synset itself), at
most 3 words per phrase, at most MAX_SYN synonyms per sense.

wordfreq is used at build time only, for ranking; its data is not shipped.

Usage (uv supplies wordfreq):
  python3 convert_wiktionary.py kaikki.org-dictionary-English.jsonl.gz wikt.jsonl
  uv run --no-project --with wordfreq python build_en.py \\
      kaikki.org-dictionary-English.jsonl.gz wikt.jsonl \\
      english-wordnet-2025.xml.gz mthesaur.txt en_dict.jsonl.gz

Sources and licences: see ATTRIBUTION.md.
"""

import gzip
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

from wordfreq import zipf_frequency

MAX_SYN = 30
MAX_ANT = 10
BAD_TAGS = {"offensive", "vulgar", "derogatory", "pejorative", "slur", "ethnic"}
OLD_TAGS = {"obsolete", "archaic"}
WN_POS = {"n": "n", "v": "v", "a": "adj", "s": "adj", "r": "adv"}
KAIKKI_POS = {"noun": "n", "verb": "v", "adj": "adj", "adv": "adv"}
STOP = set("the and for with from that this which into onto upon its their being been have has "
           "not any some something someone used especially often usually more less very one".split())
_TOK = re.compile(r"[a-z]+")


def toks(text):
    return {t for t in _TOK.findall((text or "").lower()) if len(t) > 2 and t not in STOP}


def opener(path):
    return gzip.open(path, "rt", encoding="utf-8") if path.endswith(".gz") else open(path, encoding="utf-8")


_zipf = {}


def zipf(w):
    if w not in _zipf:
        _zipf[w] = zipf_frequency(w, "en")
    return _zipf[w]


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def read_flags(kaikki_path):
    """Words whose every Kaikki sense is offensive (blocked) or obsolete (old)."""
    tagsets = defaultdict(list)
    with opener(kaikki_path) as f:
        for line in f:
            e = json.loads(line)
            w = e.get("word", "")
            for s in e.get("senses") or []:
                tagsets[w].append(set(s.get("tags") or []))
    blocked = {w for w, ts in tagsets.items() if all(t & BAD_TAGS for t in ts)}
    old = {w for w, ts in tagsets.items() if all(t & (OLD_TAGS | BAD_TAGS) for t in ts)}
    return blocked, old


def read_oewn(path):
    """Return (entries, synsets): lemma -> [(pos, synset_id, [antonym sense ids])], synset info."""
    entry_lemma, sense_lemma = {}, {}
    entries = defaultdict(list)
    synsets = {}
    with gzip.open(path) as f:
        for _, el in ET.iterparse(f):
            if el.tag == "LexicalEntry":
                lemma_el = el.find("Lemma")
                lemma = lemma_el.get("writtenForm")
                entry_lemma[el.get("id")] = lemma
                for s in el.findall("Sense"):
                    sense_lemma[s.get("id")] = lemma
                    ants = [r.get("target") for r in s.findall("SenseRelation") if r.get("relType") == "antonym"]
                    entries[lemma].append((WN_POS[lemma_el.get("partOfSpeech")], s.get("synset"), ants))
                el.clear()
            elif el.tag == "Synset":
                rels = defaultdict(list)
                for r in el.findall("SynsetRelation"):
                    rels[r.get("relType")].append(r.get("target"))
                d = el.find("Definition")
                synsets[el.get("id")] = {
                    "members": el.get("members").split(),
                    "def": d.text if d is not None else None,
                    "rels": rels,
                }
                el.clear()
    for ss in synsets.values():
        ss["members"] = [entry_lemma[m] for m in ss["members"] if m in entry_lemma]
    for senses in entries.values():
        for i, (pos, sid, ants) in enumerate(senses):
            senses[i] = (pos, sid, [sense_lemma[a] for a in ants if a in sense_lemma])
    return entries, synsets


def read_moby(path):
    moby = {}
    with open(path, encoding="latin-1") as f:
        for line in f:
            parts = [p.strip() for p in line.rstrip("\r\n").split(",") if p.strip()]
            if len(parts) > 1:
                moby[parts[0]] = parts[1:]
    return moby


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def acceptable(c, word, blocked, old, strict):
    if not c or c == word or c.lower() == word.lower():
        return False
    if c in blocked or c.startswith("-") or c.endswith("-") or len(c.split()) > 3 or any(ch.isdigit() for ch in c):
        return False
    if word.islower() and not c.islower():
        return False
    if strict and (c in old or zipf(c) == 0):
        return False
    return True


def build_word(word, wn_senses, synsets, wikt_senses, moby, moby_sets, neighbours, wn_pos, blocked, old):
    # Wiktionary POS order decides which part of speech comes first.
    pos_order = []
    for s in wikt_senses:
        if s.get("pos") in ("n", "v", "adj", "adv") and s["pos"] not in pos_order:
            pos_order.append(s["pos"])
    for p in ("n", "v", "adj", "adv"):
        if p not in pos_order:
            pos_order.append(p)

    senses = []
    for pos, sid, ants in wn_senses:
        ss = synsets[sid]
        score = defaultdict(float)
        for m in ss["members"]:
            score[m] += 3
        for rel, weight in (("similar", 2), ("also", 1)):
            for t in ss["rels"].get(rel, []):
                for m in synsets[t]["members"]:
                    score[m] += weight
        if pos in ("n", "v"):
            # Kinds of the thing (walk -> stroll, house -> cottage): the first
            # two members of each hyponym synset, single common words only.
            for t in ss["rels"].get("hyponym", []):
                for m in synsets[t]["members"][:2]:
                    if " " not in m and zipf(m) >= 2.5:
                        score[m] += 1
        if pos == "n":
            # What it is a kind of (house -> building, dwelling, home).
            for t in ss["rels"].get("hypernym", []):
                for m in synsets[t]["members"][:3]:
                    if " " not in m and zipf(m) >= 3:
                        score[m] += 0.5
        senses.append({"pos": pos, "definition": ss["def"], "score": score,
                       "ants": list(ants), "wn": True, "def_toks": toks(ss["def"])})

    # Attach each Wiktionary sense to the WordNet sense it matches best.
    for ws in wikt_senses:
        wsyn, want = ws.get("synonyms", []), ws.get("antonyms", [])
        best, best_score = None, 0
        for s in senses:
            if s["pos"] != ws.get("pos"):
                continue
            # The headword itself ("house" in "a gambling house") is no evidence.
            sc = 2 * len(set(wsyn) & s["score"].keys()) + len((toks(ws.get("definition")) - {word}) & s["def_toks"])
            if sc > best_score:
                best, best_score = s, sc
        if best is not None and best_score >= 3:
            for c in wsyn:
                best["score"][c] += 2
            best["ants"] += want
        elif wsyn or want:
            senses.append({"pos": ws.get("pos"), "definition": ws.get("definition"),
                           "score": defaultdict(float, {c: 2.0 for c in wsyn}),
                           "ants": list(want), "wn": False, "def_toks": set()})

    # Moby: a flat list; keep a word only where it shares >=3 neighbours with a
    # sense of a part of speech WordNet gives it.
    for c in moby.get(word, []):
        if not acceptable(c, word, blocked, old, True) or zipf(c) < 2.5 or len(c.split()) > 2:
            continue
        nb = neighbours(c)
        best, best_score = None, 2
        for s in senses:
            if s["pos"] not in wn_pos.get(c, ()):
                continue
            sc = len(nb & s["score"].keys())
            if sc > best_score:
                best, best_score = s, sc
        if best is not None and word in moby_sets.get(c, ()):  # Moby must list it both ways
            best["score"][c] += 1 + min(best_score, 4) / 4

    out = []
    for s in senses:
        strict = lambda c, s=s: s["score"][c] < 3  # synset members skip the rarity filter
        syns = [c for c in s["score"] if acceptable(c, word, blocked, old, strict(c))]
        syns.sort(key=lambda c: (-s["score"][c], len(c.split()) > 1, -zipf(c)))
        shown = set(syns[:MAX_SYN])
        ants = list(dict.fromkeys(a for a in s["ants"]
                                  if acceptable(a, word, blocked, old, True) and a not in shown))
        sense = {"pos": s["pos"]}
        if s["definition"]:
            sense["definition"] = s["definition"]
        if syns:
            sense["synonyms"] = syns[:MAX_SYN]
        if ants:
            sense["antonyms"] = ants[:MAX_ANT]
        out.append((s["pos"], not s["wn"], sense))

    # Group by POS (Wiktionary order); within a POS keep WordNet order, then
    # Wiktionary-only senses. Drop bare definitions past the third of a POS.
    out.sort(key=lambda t: (pos_order.index(t[0]) if t[0] in pos_order else 9, t[1]))
    result, seen_pos = [], defaultdict(int)
    for pos, _, sense in out:
        seen_pos[pos] += 1
        if "synonyms" in sense or "antonyms" in sense or seen_pos[pos] <= 3:
            result.append(sense)
    return result


def main(kaikki_path, wikt_path, oewn_path, moby_path, out_path):
    print("reading Kaikki flags…", file=sys.stderr)
    blocked, old = read_flags(kaikki_path)
    print("reading OEWN…", file=sys.stderr)
    wn_entries, synsets = read_oewn(oewn_path)
    print("reading Moby…", file=sys.stderr)
    moby = read_moby(moby_path)
    moby_sets = {k: set(v) for k, v in moby.items()}
    print("reading Wiktionary…", file=sys.stderr)
    wikt = {}
    with opener(wikt_path) as f:
        for line in f:
            o = json.loads(line)
            wikt[o["word"]] = o["senses"]

    wn_syn, wn_pos = defaultdict(set), defaultdict(set)
    for lemma, senses in wn_entries.items():
        for pos, sid, _ in senses:
            wn_syn[lemma].update(synsets[sid]["members"])
            wn_pos[lemma].add(pos)

    def neighbours(c):
        # Not Moby's own list: those run to thousands of words and match anything.
        nb = set(wn_syn.get(c, ()))
        for s in wikt.get(c, ()):
            nb.update(s.get("synonyms", ()))
        return nb

    words = sorted(set(wikt) | {w for w in wn_entries if w.islower()})
    rebuilt = 0
    with gzip.open(out_path, "wt", encoding="utf-8", compresslevel=9) as out:
        for w in words:
            if w.islower() and w in wn_entries:
                senses = build_word(w, wn_entries[w], synsets,
                                    [s for s in wikt.get(w, []) if s.get("synonyms") or s.get("antonyms")]
                                    + [s for s in wikt.get(w, []) if not (s.get("synonyms") or s.get("antonyms"))],
                                    moby, moby_sets, neighbours, wn_pos, blocked, old)
                rebuilt += 1
            else:
                senses = wikt[w]
            if senses:
                out.write(json.dumps({"word": w, "senses": senses}, ensure_ascii=False) + "\n")
    print(f"words={len(words):,} rebuilt_from_wordnet={rebuilt:,}", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) != 6:
        sys.exit(__doc__)
    main(*sys.argv[1:])
