#!/usr/bin/env python3
"""Convert Princeton WordNet 3.0 WNdb data.* files -> flat synonyms JSONL.

Same output schema as the Spanish converter:
  {"word": "<lemma>", "synonyms": ["<syn>", ...], "pos": "n|v|a|r"}

In WNdb, each non-comment line of data.<pos> is a synset:
  offset filenum ss_type w_cnt(hex) [word lex_id(hex)]... p_cnt ... | gloss
The words in one synset are mutual synonyms. Multi-word lemmas use '_'.
"""
import sys, json, glob, os
from collections import defaultdict

POS_FILES = {"data.noun": "n", "data.verb": "v", "data.adj": "a", "data.adv": "r"}

def parse_synset_words(line):
    # strip gloss
    head = line.split("|", 1)[0].split()
    # head: offset filenum ss_type w_cnt word lex_id word lex_id ... p_cnt ...
    if len(head) < 4:
        return []
    try:
        w_cnt = int(head[3], 16)
    except ValueError:
        return []
    words = []
    i = 4
    for _ in range(w_cnt):
        if i + 1 >= len(head):
            break
        lemma = head[i].replace("_", " ")
        # strip trailing lexical marker like "word(a)" rare; keep simple
        words.append(lemma)
        i += 2  # skip lex_id
    return words

def main(dict_dir, out_path):
    syns = defaultdict(set)
    word_pos = {}
    for fname, pos in POS_FILES.items():
        path = os.path.join(dict_dir, fname)
        if not os.path.exists(path):
            print(f"missing {path}", file=sys.stderr)
            continue
        with open(path, encoding="latin-1") as f:
            for line in f:
                if line.startswith("  "):  # license header
                    continue
                words = parse_synset_words(line)
                # WordNet sometimes encodes case/markers; lowercase for lookup key
                for w in words:
                    word_pos.setdefault(w, pos)
                    for other in words:
                        if other != w:
                            syns[w].add(other)

    written = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for word in sorted(syns):
            group = sorted(s for s in syns[word] if s)
            if not group:
                continue
            obj = {"word": word, "synonyms": group, "pos": word_pos.get(word, "?")}
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
            written += 1
    print(f"words_with_synonyms={written}", file=sys.stderr)

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
