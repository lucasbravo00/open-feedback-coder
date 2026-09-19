# Where these fixtures come from

`ospo_q12_sample.csv` holds ten real answers to question Q12 of the 2025 UC
OSPO Network open source survey:

> Q12: Are there any other challenges you've encountered in open source, or
> types of support that you would find helpful?

They are reproduced verbatim, including typos, spacing and the bracketed
placeholders such as `[department]` that the survey's authors substituted for
identifying details when they de-identified the data. Nothing here was written
for this project, and nothing was edited to make anything work.

They were chosen because between them they carry the things the quote verifier
has to survive in real text: non-breaking spaces in the middle of sentences,
a curly apostrophe, bracketed redactions, answers numbered `1)` `2)` by the
respondent, and lengths from 76 to 1042 characters.

## Provenance

Scarlett, Curty, Gomez et al. (2026). *Survey responses from the 2025 UC OSPO
Network open source survey* [Data set]. Dryad.
<https://doi.org/10.5061/dryad.2280gb662>

The data is licensed CC0 1.0 (public domain dedication), so this reuse carries
no licence obligation; the citation is here because crediting the people who
collected the data is the right thing to do regardless.

Regenerate the full converted corpus, of which this is a subset, with:

```bash
uv run python scripts/download_dataset.py --archive /path/to/dryad_data_final.tar.gz
```
