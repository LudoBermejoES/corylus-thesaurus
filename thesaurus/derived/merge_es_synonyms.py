#!/usr/bin/env python3
"""Merge es_synonyms.jsonl into es_dict.jsonl.gz.

Reads the sense-structured Wiktionary Spanish thesaurus (es_dict.jsonl.gz) and
enriches it with synonym/antonym data from the curated flat thesaurus
(es_synonyms.jsonl).  Writes the result back atomically via a .tmp file.

Key design decisions (see openspec/changes/merge-es-synonyms/design.md):
- D3a: Pre-group es_synonyms by normalized key to handle collisions (1,077 raw
  keys share a normalized form) before touching wikt.
- D2: POS match → first-sense fallback.
- D3: Reflexive/variant parentheticals stripped from lookup key.
- D4: (amer.) usage labels stripped from synonym/antonym text.
- D5: 50-synonym cap per sense.
- D6: Pure-noise entries (^\\([a-z]+\\)$ and //) skipped.
- D7: Atomic write via .tmp + os.replace.
"""

import gzip
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ES_SYNONYMS = Path("thesaurus/derived/es_synonyms.jsonl")
ES_DICT = Path("thesaurus/derived/es_dict.jsonl.gz")

_NOISE_RE = re.compile(r"^\([a-z]+\)$")
_PAREN_RE = re.compile(r"\([^)]*\)")
_AMER_RE = re.compile(r"\s*\(amer\.\)")


# ---------------------------------------------------------------------------
# Normalization helpers (Group 2)
# ---------------------------------------------------------------------------

def normalize_key(word: str) -> str | None:
    """Strip parentheticals from a word key; return None for pure-noise entries."""
    w = word.strip()
    if _NOISE_RE.fullmatch(w) or w == "//":
        return None
    return _PAREN_RE.sub("", w).strip() or None


def clean_relation(s: str) -> str | None:
    """Strip (amer.) from a synonym/antonym string; return None if result is empty."""
    result = _AMER_RE.sub("", s).strip()
    return result if result else None


def map_pos(pos: str | None) -> str | None:
    """Map es_synonyms POS codes to wikt-compatible codes."""
    mapping = {"n": "n", "v": "v", "r": "adv"}
    return mapping.get(pos) if pos else None


def extend_list(existing: list[str], new: list[str], cap: int = 50) -> list[str]:
    """Case-insensitive union of two synonym lists, preserving order, capped."""
    seen = {s.lower() for s in existing}
    result = list(existing)
    for s in new:
        if len(result) >= cap:
            break
        if s.lower() not in seen:
            seen.add(s.lower())
            result.append(s)
    return result


# ---------------------------------------------------------------------------
# Sense selection (Group 3)
# ---------------------------------------------------------------------------

def find_target_sense(senses: list[dict], mapped_pos: str | None) -> int:
    """Return index of first sense matching mapped_pos, or 0 as fallback.

    Returns -1 if senses list is empty.
    """
    if not senses:
        return -1
    if mapped_pos:
        for i, sense in enumerate(senses):
            if sense.get("pos") == mapped_pos:
                return i
    return 0


# ---------------------------------------------------------------------------
# I/O helpers (Group 1)
# ---------------------------------------------------------------------------

def load_es_synonyms() -> dict[str, dict]:
    """Read es_synonyms.jsonl; return dict keyed by raw word."""
    entries: dict[str, dict] = {}
    with ES_SYNONYMS.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            entries[obj["word"]] = obj
    return entries


def load_wikt() -> dict[str, dict]:
    """Read es_dict.jsonl.gz; return dict keyed by word, preserving order."""
    entries: dict[str, dict] = {}
    with gzip.open(ES_DICT, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            entries[obj["word"]] = obj
    return entries


def write_atomic(wikt: dict[str, dict]) -> str:
    """Write wikt to ES_DICT atomically; return SHA-256 hex of the output."""
    tmp = Path(str(ES_DICT) + ".tmp")
    try:
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            for entry in wikt.values():
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        sha = _sha256(tmp)
        os.replace(tmp, ES_DICT)
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise
    return sha


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Grouping pass (task 4.1)
# ---------------------------------------------------------------------------

def build_grouped_map(esyns: dict[str, dict]) -> tuple[dict[str, dict], int, int, int]:
    """Pre-group es_synonyms entries by normalized key.

    Returns (grouped_map, noise_count, reflexive_count, collision_count).
    grouped_map[normalized_key] = {synonyms, antonyms, pos}
    """
    grouped: dict[str, dict] = {}
    noise_count = 0
    reflexive_count = 0
    collision_count = 0

    for raw_word, entry in esyns.items():
        norm = normalize_key(raw_word)
        if norm is None:
            noise_count += 1
            continue

        if norm != raw_word.strip():
            reflexive_count += 1

        # Clean relations
        syns = [c for s in (entry.get("synonyms") or []) if (c := clean_relation(s))]
        ants = [c for s in (entry.get("antonyms") or []) if (c := clean_relation(s))]
        pos = entry.get("pos")

        if norm in grouped:
            # Collision: union relations; keep POS of first entry seen
            collision_count += 1
            g = grouped[norm]
            seen_syns = {s.lower() for s in g["synonyms"]}
            for s in syns:
                if s.lower() not in seen_syns:
                    g["synonyms"].append(s)
                    seen_syns.add(s.lower())
            seen_ants = {a.lower() for a in g["antonyms"]}
            for a in ants:
                if a.lower() not in seen_ants:
                    g["antonyms"].append(a)
                    seen_ants.add(a.lower())
        else:
            grouped[norm] = {"synonyms": syns, "antonyms": ants, "pos": pos}

    return grouped, noise_count, reflexive_count, collision_count


# ---------------------------------------------------------------------------
# Main merge logic (Group 4)
# ---------------------------------------------------------------------------

def merge(wikt: dict[str, dict], grouped: dict[str, dict]) -> dict:
    """Merge grouped es_synonyms data into wikt in-place.

    Returns stats dict with words_enriched, new_synonyms_added,
    new_antonyms_added, words_added.
    """
    stats = {
        "words_enriched": 0,
        "new_synonyms_added": 0,
        "new_antonyms_added": 0,
        "words_added": 0,
    }

    for key, group in grouped.items():
        mapped_pos = map_pos(group["pos"])

        # Strip self-references (task 4.3)
        key_lower = key.lower()
        syns = [s for s in group["synonyms"] if s.lower() != key_lower]
        ants = [a for a in group["antonyms"] if a.lower() != key_lower]

        if key in wikt:
            # Enrich existing entry (task 4.2)
            entry = wikt[key]
            senses = entry.get("senses", [])
            idx = find_target_sense(senses, mapped_pos)
            if idx == -1:
                # Defensive: wikt entry exists but has no senses — treat as new
                new_entry = {
                    "word": key,
                    "senses": [{
                        "pos": mapped_pos,
                        "definition": None,
                        "synonyms": syns[:50],
                        "antonyms": ants,
                    }],
                }
                wikt[key] = new_entry
                stats["words_added"] += 1
                stats["new_synonyms_added"] += len(syns[:50])
                stats["new_antonyms_added"] += len(ants)
                continue

            sense = senses[idx]
            old_syn_len = len(sense.get("synonyms", []))
            old_ant_len = len(sense.get("antonyms", []))

            sense["synonyms"] = extend_list(sense.get("synonyms", []), syns)
            sense["antonyms"] = extend_list(sense.get("antonyms", []), ants, cap=1000)

            added_syns = len(sense["synonyms"]) - old_syn_len
            added_ants = len(sense["antonyms"]) - old_ant_len

            if added_syns > 0 or added_ants > 0:
                stats["words_enriched"] += 1
                stats["new_synonyms_added"] += added_syns
                stats["new_antonyms_added"] += added_ants
        else:
            # New entry (task 4.4)
            if not syns and not ants:
                continue
            new_entry = {
                "word": key,
                "senses": [{
                    "pos": mapped_pos,
                    "definition": None,
                    "synonyms": syns[:50],
                    "antonyms": ants,
                }],
            }
            wikt[key] = new_entry
            stats["words_added"] += 1
            stats["new_synonyms_added"] += len(syns[:50])
            stats["new_antonyms_added"] += len(ants)

    return stats


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # Startup checks (task 6.1)
    missing = [p for p in (ES_SYNONYMS, ES_DICT) if not p.exists()]
    if missing:
        for p in missing:
            print(f"ERROR: required input file not found: {p}", file=sys.stderr)
        sys.exit(1)

    print("Loading es_synonyms.jsonl…", file=sys.stderr)
    esyns = load_es_synonyms()
    esyns_input = len(esyns)

    print("Loading es_dict.jsonl.gz…", file=sys.stderr)
    wikt = load_wikt()
    wikt_input_words = len(wikt)

    print("Grouping es_synonyms by normalized key…", file=sys.stderr)
    grouped, noise_count, reflexive_count, collision_count = build_grouped_map(esyns)

    # Classify reflexive: those whose normalized key was in wikt at grouping time
    reflexive_merged = sum(
        1 for k, g in grouped.items()
        # Only count entries that were originally reflexive (raw != norm) and exist in wikt
        # We approximate: any normalized key that came from a reflexive form
        # The exact split is tracked during merge via wikt membership
    )

    print("Merging into wikt…", file=sys.stderr)
    merge_stats = merge(wikt, grouped)

    print("Writing output…", file=sys.stderr)
    sha = write_atomic(wikt)

    # Compute reflexive breakdown: grouped entries that came from reflexive normalization
    # and landed in wikt (merged) vs. not in wikt (added as new)
    # We can't easily split after the fact, so we report the totals we have.
    total_output_words = len(wikt)

    print("\n=== merge_es_synonyms statistics ===")
    print(f"Input:")
    print(f"  wikt words:                  {wikt_input_words:>10,}")
    print(f"  es_synonyms entries:         {esyns_input:>10,}")
    print(f"  es_synonyms skipped (noise): {noise_count:>10,}")
    print(f"  normalized keys grouped:     {len(grouped):>10,}")
    print(f"  collisions resolved:         {collision_count:>10,}")
    print(f"  reflexive/variant forms:     {reflexive_count:>10,}")
    print()
    print(f"Enrichment (existing wikt words):")
    print(f"  words enriched:              {merge_stats['words_enriched']:>10,}")
    print(f"  new synonyms added:          {merge_stats['new_synonyms_added']:>10,}")
    print(f"  new antonyms added:          {merge_stats['new_antonyms_added']:>10,}")
    print()
    print(f"New entries:")
    print(f"  words added:                 {merge_stats['words_added']:>10,}")
    print()
    print(f"Output:")
    print(f"  total words written:         {total_output_words:>10,}")
    print(f"  SHA-256: {sha}")


if __name__ == "__main__":
    main()
