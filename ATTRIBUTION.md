# Data Attribution

## Licence of the artifacts

`en_dict.jsonl.gz` and `es_dict.jsonl.gz` are published under
[Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)](https://creativecommons.org/licenses/by-sa/4.0/),
because they contain Wiktionary data (share-alike). The Corylus application
code is licensed separately.

## Sources

### Wiktionary (English and Spanish)

Processed via the [Kaikki/wiktextract](https://kaikki.org/) pre-parsed dumps.

- Source: <https://kaikki.org>
- Upstream: <https://www.wiktionary.org>
- Licence: CC BY-SA 3.0 / CC BY-SA 4.0

### Open English WordNet (English, 2025 edition)

Sense inventory, definitions, synonyms and antonyms of `en_dict.jsonl.gz`
(built by `thesaurus/derived/build_en.py`).

- Source: <https://github.com/globalwordnet/english-wordnet>
- Licence: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
- Citation: John P. McCrae, Alexandre Rademaker, Francis Bond, Ewa Rudnicka
  and Christiane Fellbaum (2019). *English WordNet 2019 – An Open-Source
  WordNet for English*. Proceedings of the 10th Global WordNet Conference.
- Open English WordNet is derived from Princeton WordNet 3.0:
  "WordNet 3.0 Copyright 2006 by Princeton University. All rights reserved."
  Princeton's licence permits use, copying, modification and distribution
  for any purpose without fee, provided the copyright notice and the
  licence statement appear on all copies; THE DATABASE IS PROVIDED "AS IS"
  AND PRINCETON UNIVERSITY MAKES NO REPRESENTATIONS OR WARRANTIES, EXPRESS
  OR IMPLIED. The name of Princeton University may not be used in
  advertising or publicity pertaining to distribution of the database.
  Full text: <https://wordnet.princeton.edu/license-and-commercial-use>

### Moby Thesaurus II (English)

Extra synonyms in `en_dict.jsonl.gz`, kept only where they match a sense.

- Author: Grady Ward
- Source: Project Gutenberg etext #3202 (`mthesaur.txt`)
- Licence: public domain

### Build-time only (not distributed)

`build_en.py` ranks synonyms with word frequencies from
[wordfreq](https://github.com/rspeer/wordfreq) (code Apache 2.0, data
CC BY-SA 4.0). No wordfreq data is copied into the artifacts.
