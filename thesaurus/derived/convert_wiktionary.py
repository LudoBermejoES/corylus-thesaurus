#!/usr/bin/env python3
"""Convert Kaikki/wiktextract JSONL dump to Corylus sense-structured JSONL.

Input: one JSON object per line from Kaikki (kaikki.org/language-*.json),
already pre-parsed from Wiktionary markup.

Output schema (one line per word, merged across all senses):
  {
    "word": "happy",
    "senses": [
      {
        "pos": "adj",
        "definition": "Having a feeling of contentment; pleased.",
        "synonyms": ["glad", "joyful"],
        "antonyms": ["sad", "unhappy"]
      }
    ]
  }

Usage:
  # Download from kaikki.org (example for English):
  #   wget https://kaikki.org/dictionary/English/kaikki.org-dictionary-English.json.gz
  #   gunzip kaikki.org-dictionary-English.json.gz
  python3 convert_wiktionary.py kaikki.org-dictionary-English.json en_dict.jsonl
  python3 convert_wiktionary.py kaikki.org-dictionary-Spanish.json es_dict.jsonl

  # Then gzip:
  gzip en_dict.jsonl   # produces en_dict.jsonl.gz
  gzip es_dict.jsonl

  # Compute SHA-256:
  sha256sum en_dict.jsonl.gz
  sha256sum es_dict.jsonl.gz

Notes:
  - Strips etymology, pronunciation, usage examples, translations, inflections.
  - Caps definition length at 300 chars to keep artifact size manageable.
  - No cap on senses per word: an earlier cap of 10 cut "light", "run" and
    "house" off before any sense that carried synonyms.
  - The definition is the LAST gloss of a sense: Kaikki lists a subsense's
    parent glosses first, so glosses[0] repeated the parent ("To move
    swiftly." ten times for "run").
  - Entry-level synonyms/antonyms (Kaikki puts some at the entry, not on a
    sense) are attached to the sense whose gloss matches their "sense" hint,
    else to the sense whose synonyms they overlap most, else the first sense.
    Lists Kaikki copies from a Thesaurus page onto every entry of the word
    are attached once per part of speech, not once per entry.
  - MISFILED_ANTONYM_PAGES lists Thesaurus pages whose antonyms are known to
    be misparsed; from those, only noun antonyms that name the word back are kept.
  - Senses with the same POS and definition are merged.
  - Synonyms tagged obsolete/archaic/offensive/vulgar/derogatory are dropped;
    senses with those tags are listed after the others.
  - Multi-word phrases (containing a space) are included — callers already
    pass a surface word, so multi-word entries simply won't match single words.
  - Words with no senses carrying any content (no def, no syn, no ant) are
    omitted from the output.
  - Licensed CC-BY-SA (Wiktionary). See ATTRIBUTION.md in this repo.
"""

import sys
import json
import re
import gzip
from collections import defaultdict
from pathlib import Path

MAX_DEF_LEN = 300
# Thesaurus pages whose antonym lists wiktextract gets wrong. Thesaurus:light
# has a misspelled "Hypeoyms" heading after its noun "Antonyms", so its
# hyponyms (alpha ray, gamma ray, microwave...) come out as antonyms of
# "light". Drop this entry once the page is fixed upstream.
MISFILED_ANTONYM_PAGES = {"Thesaurus:light"}
# Synonyms carrying one of these tags are dropped; senses carrying one sort last.
BAD_TAGS = {"obsolete", "archaic", "offensive", "vulgar", "derogatory", "pejorative", "slur", "ethnic"}


def extract_link_targets(items):
    """Extract word strings from a list of Kaikki link objects or plain strings.

    Kaikki 'synonyms'/'antonyms' entries are objects: {"word": "...", "sense": "..."}
    or sometimes plain strings in older dumps.
    """
    result = []
    for item in items:
        if isinstance(item, str):
            word = item.strip()
        elif isinstance(item, dict):
            if BAD_TAGS.intersection(item.get("tags") or []):
                continue
            word = (item.get("word") or item.get("alt") or "").strip()
        else:
            continue
        if word:
            result.append(word)
    return result


_TOKEN = re.compile(r"[a-z]+")


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if len(t) > 2}


def _dedupe(words: list[str]) -> list[str]:
    seen, out = set(), []
    for w in words:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def attach_entry_level(senses: list[dict], rels: list, key: str, word: str) -> None:
    """Route entry-level synonyms/antonyms onto the best-matching sense."""
    if not senses:
        return
    for item in rels:
        if not isinstance(item, dict):
            continue
        target = (item.get("word") or "").strip()
        if not target or target.lower() == word.lower():
            continue
        hint = _tokens(item.get("sense") or "")
        best, best_score = senses[0], 0
        for s in senses:
            if hint:
                score = len(hint & _tokens(s.get("definition") or ""))
            else:
                score = len(_tokens(target) & {t for w in s.get("synonyms", []) for t in _tokens(w)})
            if score > best_score:
                best, best_score = s, score
        best.setdefault(key, [])
        if target not in best[key]:
            best[key].append(target)


def process_file(in_path: str, out_path: str) -> None:
    # word -> list of sense dicts in order of appearance
    word_senses: dict[str, list[dict]] = defaultdict(list)
    entry_items: dict[tuple, dict] = defaultdict(dict)

    opener = gzip.open if in_path.endswith(".gz") else open

    lines_read = 0
    words_written = 0

    with opener(in_path, "rt", encoding="utf-8") as f:
        for raw_line in f:
            lines_read += 1
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                entry = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            word = (entry.get("word") or "").strip()
            if not word:
                continue

            pos_raw = (entry.get("pos") or "").strip()
            # Kaikki uses full names: "noun", "verb", "adj", "adv", etc.
            # Normalize to short codes.
            pos_map = {
                "noun": "n", "verb": "v", "adj": "adj", "adv": "adv",
                "adjective": "adj", "adverb": "adv", "intj": "intj",
                "phrase": "phrase", "prep": "prep", "conj": "conj",
                "det": "det", "pron": "pron", "num": "num",
            }
            pos = pos_map.get(pos_raw.lower(), pos_raw) if pos_raw else None

            # Each Kaikki entry has a top-level 'senses' list.
            kaikki_senses = entry.get("senses") or []
            entry_senses: list[dict] = []
            for ks in kaikki_senses:
                glosses = ks.get("glosses") or []
                raw_def = glosses[-1].strip() if glosses else ""
                if len(raw_def) > MAX_DEF_LEN:
                    raw_def = raw_def[:MAX_DEF_LEN - 1] + "…"
                definition = raw_def or None

                synonyms = extract_link_targets(ks.get("synonyms") or [])
                antonyms = extract_link_targets(ks.get("antonyms") or [])

                # Skip senses with no useful content
                if not definition and not synonyms and not antonyms:
                    continue

                # De-duplicate against word itself
                synonyms = _dedupe([s for s in synonyms if s.lower() != word.lower()])
                antonyms = _dedupe([a for a in antonyms if a.lower() != word.lower()])

                sense: dict = {}
                if BAD_TAGS.intersection(ks.get("tags") or []):
                    sense["_bad"] = True
                if pos:
                    sense["pos"] = pos
                if definition:
                    sense["definition"] = definition
                if synonyms:
                    sense["synonyms"] = synonyms
                if antonyms:
                    sense["antonyms"] = antonyms

                entry_senses.append(sense)

            # Kaikki copies entry-level lists onto every entry of a word
            # (light "bright" and light "not heavy" both get Thesaurus:light's),
            # so they wait until all entries are read and are attached once
            # per part of speech, to the best sense among all entries.
            for key in ("synonyms", "antonyms"):
                pend = entry_items[(word, pos)].setdefault(key, [])
                pend += [i for i in entry.get(key) or [] if isinstance(i, dict) and i not in pend]

            # Merge senses with the same POS and definition.
            existing = {(s.get("pos"), s.get("definition")): s for s in word_senses[word]}
            for sense in entry_senses:
                prev = existing.get((sense.get("pos"), sense.get("definition")))
                if prev is None:
                    word_senses[word].append(sense)
                    existing[(sense.get("pos"), sense.get("definition"))] = sense
                    continue
                for k in ("synonyms", "antonyms"):
                    if sense.get(k):
                        prev[k] = _dedupe(prev.get(k, []) + sense[k])

            if lines_read % 100_000 == 0:
                print(f"  {lines_read:,} lines read, {len(word_senses):,} words so far…",
                      file=sys.stderr)

    print(f"Done reading: {lines_read:,} lines, {len(word_senses):,} words", file=sys.stderr)

    pending_ants = defaultdict(set)
    for (w, _), by_key in entry_items.items():
        pending_ants[w].update(i.get("word", "") for i in by_key.get("antonyms", ()))

    def antonyms_of(w):
        return pending_ants.get(w, set()) | {a for s in word_senses.get(w, ()) for a in s.get("antonyms", ())}

    for (word, pos), by_key in entry_items.items():
        senses = [s for s in word_senses.get(word, []) if s.get("pos") == pos]
        for key, items in by_key.items():
            if key == "antonyms" and pos == "n":
                # Keep only the noun antonyms such a page's own entries name back
                # (darkness <-> light); the rest are its misfiled hyponyms.
                items = [i for i in items
                         if i.get("source") not in MISFILED_ANTONYM_PAGES
                         or word in antonyms_of(i.get("word", ""))]
            attach_entry_level(senses, items, key, word)

    with open(out_path, "w", encoding="utf-8") as out:
        for word in sorted(word_senses):
            # Stable sort: archaic/obsolete/offensive senses go last.
            senses = sorted(word_senses[word], key=lambda s: s.pop("_bad", False))
            obj = {"word": word, "senses": senses}
            out.write(json.dumps(obj, ensure_ascii=False) + "\n")
            words_written += 1

    print(f"Written: {words_written:,} words to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <kaikki.json[.gz]> <out.jsonl>", file=sys.stderr)
        sys.exit(1)
    process_file(sys.argv[1], sys.argv[2])
