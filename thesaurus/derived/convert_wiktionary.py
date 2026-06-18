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
  - Caps senses per word at 10 for the same reason.
  - Multi-word phrases (containing a space) are included — callers already
    pass a surface word, so multi-word entries simply won't match single words.
  - Words with no senses carrying any content (no def, no syn, no ant) are
    omitted from the output.
  - Licensed CC-BY-SA (Wiktionary). See ATTRIBUTION.md in this repo.
"""

import sys
import json
import gzip
from collections import defaultdict
from pathlib import Path

MAX_DEF_LEN = 300
MAX_SENSES = 10


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
            word = (item.get("word") or item.get("alt") or "").strip()
        else:
            continue
        if word:
            result.append(word)
    return result


def process_file(in_path: str, out_path: str) -> None:
    # word -> list of sense dicts in order of appearance
    word_senses: dict[str, list[dict]] = defaultdict(list)

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
            for ks in kaikki_senses:
                glosses = ks.get("glosses") or []
                raw_def = glosses[0].strip() if glosses else ""
                if len(raw_def) > MAX_DEF_LEN:
                    raw_def = raw_def[:MAX_DEF_LEN - 1] + "…"
                definition = raw_def or None

                synonyms = extract_link_targets(ks.get("synonyms") or [])
                antonyms = extract_link_targets(ks.get("antonyms") or [])

                # Skip senses with no useful content
                if not definition and not synonyms and not antonyms:
                    continue

                # De-duplicate against word itself
                synonyms = [s for s in synonyms if s.lower() != word.lower()]
                antonyms = [a for a in antonyms if a.lower() != word.lower()]

                sense: dict = {}
                if pos:
                    sense["pos"] = pos
                if definition:
                    sense["definition"] = definition
                if synonyms:
                    sense["synonyms"] = synonyms
                if antonyms:
                    sense["antonyms"] = antonyms

                word_senses[word].append(sense)

            if lines_read % 100_000 == 0:
                print(f"  {lines_read:,} lines read, {len(word_senses):,} words so far…",
                      file=sys.stderr)

    print(f"Done reading: {lines_read:,} lines, {len(word_senses):,} words", file=sys.stderr)

    with open(out_path, "w", encoding="utf-8") as out:
        for word in sorted(word_senses):
            senses = word_senses[word][:MAX_SENSES]
            obj = {"word": word, "senses": senses}
            out.write(json.dumps(obj, ensure_ascii=False) + "\n")
            words_written += 1

    print(f"Written: {words_written:,} words to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <kaikki.json[.gz]> <out.jsonl>", file=sys.stderr)
        sys.exit(1)
    process_file(sys.argv[1], sys.argv[2])
