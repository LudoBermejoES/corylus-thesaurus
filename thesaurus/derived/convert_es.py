#!/usr/bin/env python3
"""Convert UAB Spanish WordNet wn_variantes.xml -> flat synonyms JSONL.

Output: one JSON object per word:
  {"word": "<lemma>", "synonyms": ["<syn>", ...], "pos": "n|v|a|r|?"}

Synonyms come from two mechanisms:
  1. Rows sharing an id_synset -> their non-empty `trad` values are mutual synonyms.
  2. The inline `sin` attribute -> comma-separated synonyms of that row's `trad`.

POS is decoded from the WordNet sense key embedded in `skey`:
  ...%N:... where N is 1=noun 2=verb 3=adj 4=adv 5=adj-satellite.
"""
import sys, json, re
from collections import defaultdict
from xml.sax import make_parser, handler

POS_MAP = {"1": "n", "2": "v", "3": "a", "4": "r", "5": "a"}

def pos_from_skey(skey: str) -> str:
    m = re.search(r"%([1-5]):", skey or "")
    return POS_MAP.get(m.group(1), "?") if m else "?"

def clean(lemma: str) -> str:
    return (lemma or "").strip()

class Handler(handler.ContentHandler):
    def __init__(self):
        self.synset_members = defaultdict(list)   # id_synset -> [(lemma, pos)]
        self.sin_pairs = []                        # (lemma, [sins], pos)
        self.rows = 0

    def startElement(self, name, attrs):
        if name != "row":
            return
        self.rows += 1
        syn = attrs.get("id_synset", "")
        trad = clean(attrs.get("trad", ""))
        sin = attrs.get("sin", "") or ""
        pos = pos_from_skey(attrs.get("skey", ""))
        if trad:
            self.synset_members[syn].append((trad, pos))
            sins = [clean(s) for s in sin.split(",") if clean(s)]
            if sins:
                self.sin_pairs.append((trad, sins, pos))

def main(xml_path, out_path):
    parser = make_parser()
    h = Handler()
    parser.setContentHandler(h)
    parser.parse(xml_path)

    # word -> set of synonyms ; word -> pos (first seen)
    syns = defaultdict(set)
    word_pos = {}

    # 1. synset grouping
    for members in h.synset_members.values():
        lemmas = [(l, p) for (l, p) in members]
        words = [l for (l, _) in lemmas]
        for (lemma, pos) in lemmas:
            word_pos.setdefault(lemma, pos)
            for other in words:
                if other != lemma:
                    syns[lemma].add(other)

    # 2. inline `sin`
    for (lemma, sins, pos) in h.sin_pairs:
        word_pos.setdefault(lemma, pos)
        for s in sins:
            if s != lemma:
                syns[lemma].add(s)
                word_pos.setdefault(s, pos)
                syns[s].add(lemma)  # symmetric

    written = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for word in sorted(syns):
            group = sorted(s for s in syns[word] if s)
            if not group:
                continue
            obj = {"word": word, "synonyms": group, "pos": word_pos.get(word, "?")}
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
            written += 1

    print(f"rows={h.rows} synsets={len(h.synset_members)} "
          f"words_with_synonyms={written}", file=sys.stderr)

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
