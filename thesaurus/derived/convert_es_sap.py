#!/usr/bin/env python3
"""Extract entries from the Sinónimos/Antónimos/Parónimos dictionary PDF.

Source: "Diccionario de Sinónimos, Antónimos y Parónimos – Uso de la lengua
española". A-Z entries span PDF page indices 7..379 (3-column layout).

Usage:
  # raw extraction (all A-Z pages) -> JSONL of expanded word variants
  python3 convert_es_sap.py > raw_es_sap.jsonl          # full run
  python3 convert_es_sap.py 7,8,10                      # specific pages (debug)
  # then merge into es_synonyms.jsonl:
  python3 merge_es_sap.py

Pipeline:
  1. Reconstruct reading-order text per column (3 cols), tagging:
       \x01..\x02  bold       -> headword / morphological variant markers (-da,-se,-s,-rio)
       \x03..\x04  bolditalic -> 'Ant.' / 'Par.' markers
     and healing end-of-line hyphenation.
  2. Split the per-page text into entries (each starts at a top-level bold headword).
  3. Parse each entry per the dictionary's documented grammar:
       HEADWORD, syn, syn. Ant. ant, ant.// syn(sense2). Ant. ...// Par. par.
     - '//' separates senses; synonyms/antonyms are flattened across senses.
     - bold mid-body markers '-da/-ta/-va/-ca/...', '-se', '-s', '-rio/-ria'
       introduce a morphological-variant sense (handled by variant expansion).
     - 'Ant.' -> antonyms (until next // , Ant., Par., or end)
     - 'Par.' -> paronyms
  4. Expand headword variants:
       'abnegado-da' -> abnegado (m) + abnegada (f)
       'abombar-se'  -> abombar + abombarse

Emits one JSON object per generated word:
  {"word","synonyms":[...],"antonyms":[...],"paronyms":[...],"_page":N}
"""
import pdfplumber, sys, re, json
from collections import defaultdict

PDF = "diccionario-de-sinonimos-antonimos-y-paronimos1.pdf"
COL_LEFTS = [58, 219, 381]

B0, B1, I0, I1 = "\x01", "\x02", "\x03", "\x04"

# ---------- stage 1: reconstruction ----------
def col_of(x0):
    best, bd = 0, 1e9
    for i, L in enumerate(COL_LEFTS):
        if x0 >= L - 6:
            d = x0 - L
            if d < bd:
                bd, best = d, i
    return best

def is_bold(fn): return "Bold" in fn and "Italic" not in fn
def is_bi(fn):   return "BoldItalic" in fn

def page_text(page):
    words = [w for w in page.extract_words(extra_attrs=["fontname"], use_text_flow=False)
             if w["top"] > 60]                      # drop running header
    cols = {0: [], 1: [], 2: []}
    for w in words:
        cols[col_of(w["x0"])].append(w)
    chunks = []
    for ci in range(3):
        ws = sorted(cols[ci], key=lambda w: (round(w["top"] / 3), w["x0"]))
        toks = []
        for w in ws:
            t = w["text"]
            if is_bold(w["fontname"]):  t = B0 + t + B1
            elif is_bi(w["fontname"]):  t = I0 + t + I1
            toks.append(t)
        chunks.append(" ".join(toks))
    text = " ".join(chunks)
    # heal hyphenation: "word- frag" -> "wordfrag" (only inside Book text)
    text = re.sub(r"(\w)-\s+(\w)", r"\1\2", text)
    # collapse sentinel pairs broken by the heal (rare) and tidy spaces
    text = re.sub(r"[ ]{2,}", " ", text)
    return text

# ---------- stage 2: split into entries ----------
# An entry begins with a bold token ending in a comma that sits at the start
# of an entry (i.e. a headword). Mid-body bold markers (-da/-se/...) also are
# bold; we distinguish headwords because they do NOT start with '-' and are not
# immediately preceded by '//'.
BOLD_RE = re.compile(B0 + r"([^" + B1 + r"]*)" + B1)

def split_entries(text):
    """Yield (headword, body_text) where body still contains sentinels."""
    # find all bold spans with positions
    spans = [(m.start(), m.end(), m.group(1)) for m in BOLD_RE.finditer(text)]
    entries = []
    cur_start = None
    cur_head = None
    for i, (s, e, content) in enumerate(spans):
        c = content.strip()
        is_variant_marker = c.startswith("-")
        # preceding non-space char before this bold span
        prev = text[:s].rstrip()
        after_dbar = prev.endswith("//")
        if is_variant_marker or after_dbar:
            continue                       # part of current entry body, not a new headword
        # it's a headword
        if cur_start is not None:
            entries.append((cur_head, text[cur_start:s]))
        cur_head = c.rstrip(",").strip()
        cur_start = e
    if cur_start is not None:
        entries.append((cur_head, text[cur_start:]))
    return entries

# ---------- stage 3: parse a body ----------
def strip_sent(s):
    return s.replace(B0, "").replace(B1, "").replace(I0, "").replace(I1, "")

def clean_term(t):
    t = strip_sent(t).strip()
    t = t.strip(" .,;:")
    t = re.sub(r"\s+", " ", t)
    # lowercase a leading capital that is only an artifact of following a
    # period / Ant. / Par. — keep all-caps acronyms and multiword as-is.
    if t and t[0].isupper() and not t.isupper():
        rest = t[1:]
        if not rest or rest.islower() or " " in t:
            # single capitalised word -> lowercase (sentence-initial artifact)
            if " " not in t:
                t = t[0].lower() + t[1:]
    return t

def split_terms(chunk):
    out = []
    for part in re.split(r"[,;]", strip_sent(chunk)):
        p = clean_term(part)
        if p:
            out.append(p)
    return out

# variant marker like "-da" / "-se" / "-s" / "-rio" possibly bold-wrapped
VAR_MARK = re.compile(r"^" + B0 + r"?\s*-(\w+)\b")

def parse_body(body):
    """Return (synonyms, antonyms, paronyms, variant_senses).

    variant_senses: list of (suffix, [synonyms]) for bold '-xxx' senses,
    used by variant expansion. We flatten regular senses into the main lists.
    """
    syns, ants, pars = [], [], []
    variant_senses = []

    # Split off paronyms first: 'Par.' marker (bold-italic). Everything after
    # the LAST Par. up to end is paronyms; there is usually one Par. at the end.
    # Use sentinel-aware search.
    par_re = re.compile(I0 + r"\s*Par\.\s*" + I1)
    ant_re = re.compile(I0 + r"\s*Ant\.\s*" + I1)

    # Work on senses split by '//'
    # First, extract paronyms wherever 'Par.' appears (they attach to a sense
    # but per the spec we flatten paronyms too).
    # Replace markers with tokens to ease splitting.
    work = body
    work = ant_re.sub(" \x05ANT\x05 ", work)
    work = par_re.sub(" \x05PAR\x05 ", work)

    senses = re.split(r"//", work)
    for sense in senses:
        # within a sense, segments separated by \x05ANT\x05 / \x05PAR\x05
        # pattern: SYN... [ANT ant...] [PAR par...] in any order, but normally
        # SYN then ANT then PAR.
        parts = re.split(r"\x05(ANT|PAR)\x05", sense)
        # parts[0] = synonyms; then pairs (tag, content)
        head = parts[0]
        # detect bold variant marker at start of this sense's synonym chunk
        mvar = VAR_MARK.match(head.strip())
        sense_syns = split_terms(head)
        if mvar:
            suffix = mvar.group(1)
            # the first 'term' is the marker itself ('-da' -> 'da'); drop it
            cleaned = [t for t in sense_syns if not re.fullmatch(r"-?\w{1,4}", t) or len(t) > 4]
            variant_senses.append((suffix, sense_syns[1:] if sense_syns else []))
            syns += sense_syns[1:]
        else:
            syns += sense_syns
        i = 1
        while i < len(parts) - 1:
            tag, content = parts[i], parts[i + 1]
            terms = split_terms(content)
            if tag == "ANT":
                ants += terms
            else:
                pars += terms
            i += 2
    return syns, ants, pars, variant_senses

# ---------- stage 4: variant expansion ----------
GENDER_SUFFIXES = {"da","ta","va","ca","ga","na","ra","sa","za","lla","ona","ina","esa","triz","a"}

def expand_headword(head):
    """Return list of (word, kind) for the headword.
    kind in {'base','fem','refl'}."""
    h = head.strip()
    out = []
    # reflexive: 'abombar-se'
    m = re.match(r"^(.*?)-se$", h)
    if m:
        base = m.group(1)
        out.append((base, "base"))
        out.append((base + "se", "refl"))
        return out
    # gender: 'abnegado-da', 'abyecto-ta', 'accesorio-ria'
    m = re.match(r"^(.+?)-(\w{1,4})$", h)
    if m:
        stem_word, suf = m.group(1), m.group(2)
        out.append((stem_word, "base"))   # masculine / base form
        # build feminine: replace the trailing chars of stem_word with suffix
        # e.g. abnegado + 'da' -> abnegada ; accesorio + 'ria' -> accesoria
        fem = build_feminine(stem_word, suf)
        if fem and fem != stem_word:
            out.append((fem, "fem"))
        return out
    out.append((h, "base"))
    return out

def build_feminine(masc, suf):
    """abnegado + 'da' -> abnegada; feliz + ... ; accesorio + 'ria' -> accesoria.
    Heuristic: the suffix gives the feminine ending; we replace the masculine
    ending of equal-ish length."""
    # common: masc ends in 'o', fem suffix starts after dropping 'o'
    # suffix 'da' for 'abnegado' -> drop 'o', stem 'abnegad', + 'a' ... but suf='da'
    # The dictionary's '-da' means: full feminine = stem + 'da' where stem = masc without last syllable's vowel.
    # Simplest robust rule: feminine = masc[:-len(suf)+ (overlap)] ... ambiguous.
    # Practical approach: replace final vowel-group.
    # Map: if masc endswith 'o' -> masc[:-1] + suf_last_a_form
    # The suffix encodes the differing TAIL. Align by last consonant.
    # Rule that works for the doc's pattern: feminine = masc with its ending
    # replaced so it ends in the suffix. The suffix shares its leading
    # consonant(s) with the masculine ending.
    if masc.endswith("o"):
        # abnegado(da)->abnegada ; accesorio(ria)->accesoria ; activo(va)->activa
        return masc[:-1] + suf[-1] if len(suf) == 2 and suf[0] == masc[-2] else _fem_replace(masc, suf)
    if masc.endswith("e") or masc[-1] in "lrnsz":
        # abyecto handled by 'o'; for consonant endings fem often = masc + 'a'
        return masc + "a"
    return _fem_replace(masc, suf)

def _fem_replace(masc, suf):
    # general: find overlap of suf's first letter in masc tail
    c = suf[0]
    idx = masc.rfind(c)
    if idx >= 0:
        return masc[:idx] + suf
    return masc[:-1] + suf if masc else masc

# ---------- driver ----------
def process_page(page, pageno):
    text = page_text(page)
    results = []
    for head, body in split_entries(text):
        if not head or len(head) > 40:
            continue
        syns, ants, pars, _ = parse_body(body)
        words = expand_headword(head)
        for (w, kind) in words:
            w = clean_term(w)
            if not w:
                continue
            results.append({
                "word": w,
                "synonyms": dedup([s for s in syns if s.lower() != w.lower()]),
                "antonyms": dedup([a for a in ants if a.lower() != w.lower()]),
                "paronyms": dedup([p for p in pars if p.lower() != w.lower()]),
                "_page": pageno,
            })
    return results

def dedup(xs):
    seen = {}
    for x in xs:
        k = x.lower()
        if k not in seen:
            seen[k] = x
    return list(seen.values())

def main():
    pdf = pdfplumber.open(PDF)
    if len(sys.argv) > 1:
        idxs = [int(x) for x in sys.argv[1].split(",")]
    else:
        idxs = range(7, 380)   # A-Z dictionary section only
    for i in idxs:
        for e in process_page(pdf.pages[i], i):
            print(json.dumps(e, ensure_ascii=False))

if __name__ == "__main__":
    main()
