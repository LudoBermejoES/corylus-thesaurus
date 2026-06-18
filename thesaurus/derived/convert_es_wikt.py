"""
Convert the Spanish Wiktionary Kaikki dump (es-extract.jsonl.gz) to the
sense-structured JSONL format used by rust-thesaurus.

Source: https://kaikki.org/dictionary/downloads/es/es-extract.jsonl.gz
  lang: "Español" entries only
  senses[].glosses: definitions in Spanish
  synonyms[]: top-level list, { word, sense_index, sense }
  antonyms[]: top-level list, same shape

Output schema (one JSON object per line, gzip):
  { "word": str, "senses": [ { "pos": str|null, "definition": str|null,
                                "synonyms": [str], "antonyms": [str] } ] }

Caps: definition ≤ 300 chars, senses per word ≤ 10.
Merge: multiple entries for the same word+pos are merged (they represent
different senses in the Wiktionary article).
"""

import gzip, json, sys, urllib.request, collections, re
from pathlib import Path

URL = "https://kaikki.org/dictionary/downloads/es/es-extract.jsonl.gz"
OUT = Path(__file__).parent / "es_dict.jsonl.gz"

MAX_DEF = 300
MAX_SENSES = 10

def fetch_stream():
    print(f"Fetching {URL} …", flush=True)
    req = urllib.request.Request(URL, headers={"User-Agent": "corylus-thesaurus-builder/1.0"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return resp.read()

def pos_normalize(pos: str | None) -> str | None:
    if not pos:
        return None
    # Map Kaikki pos codes to the canonical set used in rust-thesaurus
    MAP = {
        "noun": "n", "adj": "adj", "adv": "adv", "verb": "v",
        "name": "name", "phrase": "phrase", "pron": "pron",
        "det": "det", "conj": "conj", "prep": "prep",
        "num": "num", "interj": "interj", "article": "article",
        "particle": "particle", "abbrev": None, "proverb": None,
        "symbol": None, "character": None, "punct": None,
    }
    return MAP.get(pos, pos)

def build_sense(gloss: str | None, pos: str | None) -> dict:
    return {
        "pos": pos_normalize(pos),
        "definition": gloss[:MAX_DEF] if gloss else None,
        "synonyms": [],
        "antonyms": [],
    }

def run():
    data = fetch_stream()
    print(f"Downloaded {len(data):,} bytes, parsing…", flush=True)

    # word → list of sense dicts (keyed by sense_index for syn/ant join)
    # We accumulate per (word, pos, sense_index) → sense dict
    # Key: word (lowercased for dedup); value: OrderedDict sense_index→sense
    WordEntry = collections.namedtuple("WordEntry", ["pos", "senses_by_idx"])
    entries: dict[str, list[WordEntry]] = {}

    processed = 0
    with gzip.GzipFile(fileobj=__import__("io").BytesIO(data)) as f:
        for raw in f:
            try:
                obj = json.loads(raw)
            except Exception:
                continue

            if obj.get("lang") != "Español":
                continue

            word = obj.get("word", "").strip()
            if not word:
                continue

            pos = obj.get("pos") or None
            pos_norm = pos_normalize(pos)

            # Build sense_index → definition dict from senses array
            idx_to_def: dict[str, str | None] = {}
            for s in obj.get("senses", []):
                idx = str(s.get("sense_index", ""))
                glosses = s.get("glosses", [])
                gloss = glosses[0].strip() if glosses else None
                if gloss and len(gloss) > MAX_DEF:
                    gloss = gloss[:MAX_DEF]
                idx_to_def[idx] = gloss

            # Build sense_index → sense dict
            senses_by_idx: dict[str, dict] = {}
            for idx, gloss in idx_to_def.items():
                senses_by_idx[idx] = {
                    "pos": pos_norm,
                    "definition": gloss,
                    "synonyms": [],
                    "antonyms": [],
                }
            # Also create a fallback "" sense for syn/ant with no index
            senses_by_idx.setdefault("", {
                "pos": pos_norm,
                "definition": None,
                "synonyms": [],
                "antonyms": [],
            })

            # Attach top-level synonyms / antonyms to matching sense
            for rel_key, sense_key in (("synonyms", "synonyms"), ("antonyms", "antonyms")):
                for rel in obj.get(rel_key, []):
                    rel_word = rel.get("word", "").strip()
                    if not rel_word:
                        continue
                    idx = str(rel.get("sense_index", ""))
                    target = senses_by_idx.get(idx) or senses_by_idx.get("") or None
                    if target is not None:
                        lst = target[sense_key]
                        if rel_word not in lst:
                            lst.append(rel_word)

            # Register under word
            wkey = word.lower()
            if wkey not in entries:
                entries[wkey] = []
            entries[wkey].append(WordEntry(pos=pos_norm, senses_by_idx=senses_by_idx))

            processed += 1
            if processed % 50_000 == 0:
                print(f"  {processed:,} entries read, {len(entries):,} words…", flush=True)

    print(f"Total: {processed:,} entries, {len(entries):,} unique words", flush=True)
    print(f"Writing {OUT} …", flush=True)

    written = 0
    with gzip.open(OUT, "wt", encoding="utf-8") as out:
        for word_lower, word_entries in entries.items():
            # Use the original-case word from the first entry
            # (re-derive from entries — store original word)
            pass

    # Redo: keep original word casing
    # word_lower → (original_word, list of senses)
    word_map: dict[str, tuple[str, list[dict]]] = {}

    with gzip.GzipFile(fileobj=__import__("io").BytesIO(data)) as f:
        for raw in f:
            try:
                obj = json.loads(raw)
            except Exception:
                continue
            if obj.get("lang") != "Español":
                continue
            word = obj.get("word", "").strip()
            if not word:
                continue

            pos = obj.get("pos") or None
            pos_norm = pos_normalize(pos)

            idx_to_def: dict[str, str | None] = {}
            for s in obj.get("senses", []):
                idx = str(s.get("sense_index", ""))
                glosses = s.get("glosses", [])
                gloss = glosses[0].strip() if glosses else None
                if gloss and len(gloss) > MAX_DEF:
                    gloss = gloss[:MAX_DEF]
                idx_to_def[idx] = gloss

            senses_by_idx: dict[str, dict] = {}
            for idx, gloss in idx_to_def.items():
                senses_by_idx[idx] = {
                    "pos": pos_norm,
                    "definition": gloss,
                    "synonyms": [],
                    "antonyms": [],
                }
            senses_by_idx.setdefault("", {
                "pos": pos_norm,
                "definition": None,
                "synonyms": [],
                "antonyms": [],
            })

            for rel_key in ("synonyms", "antonyms"):
                for rel in obj.get(rel_key, []):
                    rel_word = rel.get("word", "").strip()
                    if not rel_word:
                        continue
                    idx = str(rel.get("sense_index", ""))
                    target = senses_by_idx.get(idx) or senses_by_idx.get("") or None
                    if target is not None:
                        lst = target[rel_key]
                        if rel_word not in lst:
                            lst.append(rel_word)

            wkey = word.lower()
            if wkey not in word_map:
                word_map[wkey] = (word, [])

            # Append all non-empty senses (skip the fallback "" if it has no content)
            orig_word, sense_list = word_map[wkey]
            for idx, sense in senses_by_idx.items():
                if idx == "" and not sense["definition"] and not sense["synonyms"] and not sense["antonyms"]:
                    continue
                sense_list.append(sense)

    print(f"Writing {len(word_map):,} words to {OUT} …", flush=True)
    with gzip.open(OUT, "wt", encoding="utf-8") as out:
        for wkey, (orig_word, senses) in word_map.items():
            # Deduplicate senses by (pos, definition)
            seen = set()
            deduped = []
            for s in senses:
                key = (s["pos"], s["definition"])
                if key not in seen:
                    seen.add(key)
                    deduped.append(s)
            deduped = deduped[:MAX_SENSES]
            if not deduped:
                continue
            out.write(json.dumps({"word": orig_word, "senses": deduped}, ensure_ascii=False) + "\n")
            written += 1

    print(f"Done — {written:,} words written to {OUT}", flush=True)

    # Print a SHA-256
    import hashlib
    h = hashlib.sha256(OUT.read_bytes()).hexdigest()
    print(f"SHA-256: {h}", flush=True)

if __name__ == "__main__":
    run()
