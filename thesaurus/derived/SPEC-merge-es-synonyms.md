# Spec: Merge `es_synonyms.jsonl` into `es_dict.jsonl.gz`

## Background and motivation

`es_dict.jsonl.gz` is the Spanish thesaurus built from the Kaikki/Wiktionary dump.
It contains 830,467 words with rich sense structure (pos + definition per sense),
but **96.8% of words have zero synonyms or antonyms** — Wiktionary's Spanish coverage
of relations is thin.

`es_synonyms.jsonl` is a separate curated Spanish thesaurus (~30,771 words, flat
structure: word → synonyms + antonyms). It has deep relation coverage (7.8 synonyms
per word on average, 46% have antonyms) but **no definitions** and a coarse POS
scheme (only `n`, `v`, `r`).

The two sources are highly complementary. Merging them yields:
- **+223,262 new synonyms** across 25,657 existing words
- **+37,150 new antonyms** across those words
- **+4,414 genuinely new words** (not in Wiktionary) added as flat entries
- **+1,013 reflexive verb forms** (`abrir(se)` → merged into `abrir`)

---

## Research findings

### Source characterization

| Property | `es_synonyms.jsonl` | `es_dict.jsonl.gz` (Wiktionary) |
|---|---|---|
| Words | 30,771 | 830,467 |
| Definitions | None | ~1,025,847 senses with defs |
| Relations per word | 7.8 avg synonyms, 46% antonyms | 96.8% have zero |
| POS codes | `n`, `v`, `r` only | `v`, `n`, `adj`, `participle`, `name`, `adv`, … |
| Sense structure | Flat (1 entry per word) | Structured (multiple senses per word) |
| Regional markers | `(amer.)` embedded in syn text | None |

### Overlap analysis
- **25,211 shared words** (82% of es_synonyms)
- **5,560 words only in es_synonyms**: 7 pure noise, 1,139 reflexive forms, 4,414 real new words
- **98% of shared words** (24,636/25,211) have extra synonyms vs wikt

### POS compatibility
`es_synonyms` uses three codes that map to Wiktionary as follows:
- `n` → maps to wikt `n` in 63% of cases; 37% of words are actually `adj`, `v`, `adv`, etc. in wikt
- `v` → maps to wikt `v` cleanly (96% match)
- `r` → maps to wikt `adv` cleanly (100% match in sample)

The `n` mismatch is a known limitation of the source dictionary: it over-uses `n` as
a default POS. The merge strategy must not rely on POS for sense selection in `n` cases.

### Wiktionary sense enrichment opportunity by POS
Senses with existing relations:
- `v`: only 0.8% have any relation → highest gain potential
- `n`: 17.3% have relations → significant gain
- `adj`: 11.9% have relations → significant gain
- `adv`: 24.8% → moderate gain

### Noise and quality issues in `es_synonyms`
1. **7 pure noise entries**: `(a)`, `(al)`, `(con)`, `(de)`, `(en)`, `(por)`, `//` — skip entirely
2. **1,139 reflexive verb forms** (`abrir(se)`, `abatir(se)`, …): 1,013 have their bare form
   in wikt. Merge their data into the bare form; remaining 126 add as-is.
3. **2,228 synonyms with `(amer.)` marker** embedded (e.g., `babosear (amer.)`): strip the
   marker and keep the word, OR keep as-is (it's still a valid synonym). Decision: strip the
   marker text entirely — `babosear (amer.)` → `babosear`.
4. **Multi-word synonyms**: 1.8% of synonyms (4,397) are multi-word phrases like
   `a todo trance`. Keep them; the DB and lookup support multi-word entries.
5. **Entries with parenthetical words in key** (e.g., `semilla(s)`, `abandonar(se)`):
   strip the parenthetical to get the lookup key.

---

## Output format (unchanged)

The output `es_dict.jsonl.gz` must remain in the same schema:

```json
{ "word": "hablar", "senses": [
    { "pos": "v", "definition": "Articular palabras…", "synonyms": ["decir","conversar"], "antonyms": [] },
    { "pos": "v", "definition": null, "synonyms": ["platicar"], "antonyms": [] }
  ]
}
```

The rust-thesaurus SQLite ingestion (`db.rs`) reads this format directly.
Senses without pos or definition are valid (ingested with nulls).
Sense ordering within a word is preserved from the wikt original; new senses appended last.

---

## Algorithm

### Step 1 — Load sources

```python
# Load wikt (mutable dict: word -> entry)
wikt: dict[str, dict]  # word -> { word, senses: [{ pos, definition, synonyms, antonyms }] }

# Load es_synonyms (flat)
esyns: dict[str, dict]  # word -> { word, synonyms, antonyms, pos }
```

### Step 2 — Normalize es_synonyms keys

For each entry `w` in `esyns`:

1. **Skip pure noise**: if `w` matches `^\([a-z]+\)$` or is `//` → skip
2. **Strip reflexive/variant parentheticals**: `abrir(se)` → `abrir`, `semilla(s)` → `semilla`
   - Pattern: remove `\([^)]*\)` from the word key
   - Use the cleaned form as the lookup key, but record that this was a reflexive/variant
3. **Strip `(amer.)` from synonym text**: clean each synonym string with
   `re.sub(r'\s*\(amer\.\)', '', s).strip()` — do the same for antonyms

### Step 3 — POS normalization

Map `es_synonyms` POS to the canonical set used in wikt senses:
```
n  → n    (but fallback to first-sense if no n sense found, see Step 4)
v  → v
r  → adv
```

### Step 4 — Merge into existing wikt entries

For each normalized `(key, obj)` from `esyns` where `key in wikt`:

#### 4a. Find target sense(s)

Priority order for choosing which wikt sense(s) to attach the synonyms/antonyms to:

1. **POS match**: senses whose `pos` matches the mapped POS (`v`→`v`, `r`→`adv`, `n`→`n`)
2. **Fallback — first sense**: if no matching-POS sense exists (common for mis-tagged `n`)
   use the first sense regardless of its pos
3. **Append new sense**: if the word exists in wikt but has zero senses (shouldn't happen,
   but defensive: treat as new word and add a bare sense)

Attach to **all** matching-POS senses (there may be several `v` senses, each representing
a different meaning). Since `es_synonyms` doesn't discriminate between senses, the synonyms
go into the **first** matching-POS sense only (to avoid polluting unrelated senses with
the entire synonym pool).

#### 4b. Deduplicate and extend

```python
def extend_list(existing: list[str], new: list[str]) -> list[str]:
    seen = {s.lower() for s in existing}
    result = list(existing)
    for s in new:
        if s.lower() not in seen and s:
            seen.add(s.lower())
            result.append(s)
    return result
```

Apply to both `synonyms` and `antonyms` of the chosen sense.

### Step 5 — Add new entries (not in wikt)

For `key` in `esyns` where `key not in wikt` (after normalization):

1. Skip pure noise entries
2. Create a new wikt-format entry with a single sense:
   ```json
   { "word": "<key>", "senses": [{
       "pos": "<mapped_pos_or_null>",
       "definition": null,
       "synonyms": [...cleaned...],
       "antonyms": [...cleaned...]
   }] }
   ```
3. Add to the wikt dict

For reflexive forms where the bare form IS in wikt (`abrir` exists when `abrir(se)` is
processed): merge into the bare form's entry (Step 4), do not create a separate entry.

For reflexive forms where the bare form is NOT in wikt: add as a new entry using the
bare key (e.g., `abroquelar`) since the `(se)` form is just a usage variant.

### Step 6 — Write output

Write the updated wikt dict to `es_dict.jsonl.gz` (gzip, UTF-8):
- Preserve original word order (dict insertion order = wikt file order, new entries appended)
- One JSON object per line, `ensure_ascii=False`
- Print statistics summary at end

---

## Statistics to report

```
Input:
  wikt words:           830,467
  es_synonyms entries:  30,771  (after normalization)
  es_synonyms skipped (noise): N

Enrichment:
  wikt words enriched:  N  (had existing senses, got new syn/ant)
  new synonyms added:   N
  new antonyms added:   N

New entries:
  new words added:      N  (not in wikt, real words)
  reflexive merged:     N  (bare form existed in wikt)
  reflexive as new:     N  (bare form not in wikt)

Output:
  total words written:  N
  SHA-256: <hash>
```

---

## Filtering / quality rules

| Rule | Reason |
|---|---|
| Skip `^\([a-z]+\)$` and `//` | Idiomatic adverb fragments, not words |
| Strip `(amer.)` from synonym text | It's a usage note, not part of the word |
| Strip `(se)` and `(s)` from word keys | Merge into bare form |
| Skip self-references (`word == synonym`) | Circular |
| Max synonyms per sense: 50 | Prevent one flat entry from dominating all senses |
| Keep multi-word phrases as-is | Valid synonyms in the DB |
| No case normalization of synonym text | Preserve original capitalization |

---

## File locations

| File | Path |
|---|---|
| Input: es_synonyms | `thesaurus/derived/es_synonyms.jsonl` |
| Input: wikt (read) | `thesaurus/derived/es_dict.jsonl.gz` |
| Script | `thesaurus/derived/merge_es_synonyms.py` |
| Output (in-place) | `thesaurus/derived/es_dict.jsonl.gz` |

The script writes to a `.tmp` file and `os.replace()`s atomically, matching the
pattern established by `merge_es_sap.py`.

---

## Non-goals

- Do **not** change the EN (`en_dict.jsonl.gz`) pipeline — `es_synonyms` is Spanish only
- Do **not** add definitions from `es_synonyms` — it has none
- Do **not** re-download the Wiktionary dump — operate on the existing `es_dict.jsonl.gz`
- Do **not** change the `rust-thesaurus` DB schema or the `convert_es_wikt.py` script
- Do **not** deduplicate at the word level across senses — each sense retains its own
  synonym list (the DB lookup already handles cross-sense union)
- Do **not** infer missing definitions from synonym context — out of scope

---

## Validation

After running, verify:

```bash
# 1. Word count grew
python3 -c "import gzip, json; lines=list(gzip.open('thesaurus/derived/es_dict.jsonl.gz','rt')); print(len(lines), 'words')"

# 2. Spot-check a known enrichment
python3 -c "
import gzip, json
d = {o['word']:o for l in gzip.open('thesaurus/derived/es_dict.jsonl.gz','rt') for o in [json.loads(l)]}
e = d.get('hablar', d.get('correr'))
for s in e['senses'][:3]: print(s)
"

# 3. Spot-check a known new word
python3 -c "
import gzip, json
d = {o['word']:o for l in gzip.open('thesaurus/derived/es_dict.jsonl.gz','rt') for o in [json.loads(l)]}
print(d.get('baladronear', 'MISSING'))
"

# 4. Spot-check reflexive merge (abrir should have more synonyms)
python3 -c "
import gzip, json
d = {o['word']:o for l in gzip.open('thesaurus/derived/es_dict.jsonl.gz','rt') for o in [json.loads(l)]}
abrir = d['abrir']
print('abrir senses:', len(abrir['senses']))
for s in abrir['senses']: print(' ', s.get('pos'), len(s.get('synonyms',[])), 'syns')
"
```

---

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| POS mismatch (es_syn `n` → wikt `adj`) | Synonyms attached to semantically wrong sense | Fallback to first sense rather than skipping; acceptable quality tradeoff |
| `(amer.)` stripping introduces malformed words | Garbage synonyms in DB | Strip only the `\s*\(amer\.\)` suffix pattern, validate non-empty result |
| Max 50 synonyms cap exceeded | One sense overwhelmed | Cap enforced per-sense after merge |
| Reflexive stripping creates collision | Two es_syn entries map to same bare key | Merge both into the same wikt entry (union); handled by dedup |
| Output is larger than input | Longer download time for users | Acceptable; the added relations are the point |
