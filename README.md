# open-feedback-coder

Thematic coding of open-ended feedback — climate survey comments, exit
interview notes, free-text form answers — where every label can be traced back
to the words that produced it.

Two commands. The first reads the whole corpus and proposes a codebook. You
edit that codebook by hand: rename themes, merge the ones that say the same
thing, delete the ones you do not want. The second labels every comment
against the themes you approved, and attaches to each label the quote from the
comment that supports it.

The difference from pasting comments into a chat window is that the categories
are yours and the evidence is checked. No label reaches the output without a
quote, and no quote reaches the output without having been found, character
for character, inside the comment it came from.

## What this version does

1. Reads a CSV of open-ended answers.
2. Proposes an editable codebook from the full corpus.
3. Labels each comment with one primary theme, up to two secondary themes, a
   valence per theme, and a verified quote per theme.
4. Writes one CSV of labels and one CSV of everything the checks refused.
5. Counts the result, and draws a sample for you to read by hand.

That is the whole scope. There is no segmentation by team, no anonymity
threshold, no executive summary, no local model.

## What it guarantees, and what it does not

These are properties of the code, checked by the test suite:

- **You approve the codebook.** Labelling reads the YAML file on disk. Themes
  you deleted cannot be assigned; themes you renamed are what appear in the
  output. What the model originally proposed is recorded in the codebook, so
  `ofc label` can report what you changed rather than asking to be believed.
- **Every label carries a quote.** A labelled row always has a non-empty
  quote, plus the character offsets where it was found.
- **Every quote is verbatim.** For every labelled row in the output,
  `comment_text[quote_start:quote_end] == quote`. You can check this yourself
  with the output file alone and no access to the model.
- **Unverifiable labels do not ship.** If any quote for a comment cannot be
  located in that comment, the whole comment is excluded from the output and
  written to the failures file with the reason and the text the model
  returned.
- **No number comes from the model.** The tool prints three counts —
  comments labelled, comments unassigned, comments excluded — and tallies each
  in code as the run goes. It does not compute frequencies for you. The long
  format is there so that counting themes is a pivot table you build and can
  check, rather than a number this tool hands you.

What it does **not** tell you:

- **It is not validated against human coding.** No inter-coder agreement was
  measured, no accuracy was measured, and none is claimed. A verified quote
  means the words are really in the comment. It does not mean the theme is the
  right theme, or that the valence is right.
- Because of that: if you are going to make a decision with these results,
  read a sample of the labelled rows against their comments first. The output
  is built to make that cheap — the quote and its source text sit in the same
  row.

## Where your data goes

This tool sends the text you give it to OpenAI. For open-ended feedback from
employees that is the first thing anyone will ask about, so here is exactly
what leaves the machine and when.

**`ofc propose`** sends the whole corpus in a single request: the instructions
plus every comment, in full. Proposing a codebook means reading everything,
and there is no sampling.

**`ofc label`** sends one request per comment: the instructions, your approved
codebook, and that one comment's text. Over a run, every comment is sent
again.

**`ofc counts` and `ofc review` send nothing.** They read a CSV that already
exists and make no network calls at all.

**Nothing is redacted.** There is no detection of names, emails or anything
else, and this version does not try: rule-based redaction that misses things
is worse than none, because it invites the belief that the text was cleaned.
What is in the column you name is what gets sent. Empty cells and the
placeholder answers listed above are dropped before any request, so those
never leave your machine, and that is the only filtering there is.

**What the provider then does with it is between you and them.** Retention,
whether the content can be used for training, and whether a zero-retention or
enterprise arrangement applies all depend on your account and your agreement.
This README will not summarise those terms, because they change and because
getting them wrong here would be worse than saying nothing: read
<https://platform.openai.com/docs/> and whatever your organisation has signed.

**There is no offline mode.** If this text cannot leave your infrastructure,
this tool cannot help you in this version, and no flag changes that.

**The outputs carry the feedback too.** `labelled.csv` and
`labelling_failures.csv` contain the full text of every comment, and
`codebook.evidence.md` and `review.csv` contain verbatim quotes. Treat all of
them the way you treat the source file. `data/` is in `.gitignore`; your
output files are wherever you pointed `--output`.

The example dataset in this repository is public, CC0, and de-identified by
its authors, so running the quickstart against it sends nothing sensitive
anywhere.

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and an OpenAI API key.

```bash
git clone https://github.com/lucasbravo00/open-feedback-coder.git
cd open-feedback-coder
uv sync
```

```bash
export OPENAI_API_KEY="sk-..."
export OPENAI_MODEL="<the model id you want to use>"
```

There is no default model on purpose. A hard-coded model id goes stale, and
which model produced a codebook belongs in the record of the run — it is
written into the codebook file.

Propose a codebook from your own file. The column flags are explicit because
your columns will not be named like anyone else's:

```bash
uv run ofc propose --input responses.csv --text-column "What would you change?"
```

Before spending anything the command prints how many tokens it is about to
send and waits for you to confirm. Pass `--price-in` and `--price-out` (USD per
million tokens, from your provider's pricing page) to also see a cost estimate;
without them it reports tokens only rather than inventing a number.

That writes two files. `codebook.yaml` is short — an id, a label and a
description per theme — because it is the one you edit. `codebook.evidence.md`
holds the quotes behind each theme, to read while you decide what to keep.

Edit the codebook: rename, merge, delete. Then label:

```bash
uv run ofc label --input responses.csv \
                 --text-column "What would you change?" \
                 --codebook codebook.yaml
```

If a long run is interrupted, `--resume` continues it instead of paying for
the comments already done:

```bash
uv run ofc label --input responses.csv \
                 --text-column "What would you change?" \
                 --codebook codebook.yaml \
                 --resume
```

It skips every comment already in `labelled.csv` or `labelling_failures.csv`,
and redoes the last one in each — a run that was killed may have written some
of a comment's rows and not the rest, and one comment is cheaper than one
comment published with two of its three labels.

`ofc label` reports what you changed — "15 proposed, 6 kept as proposed, 3
relabelled, 6 deleted" — because a codebook a person approved should be able
to show it, not just assert it. Rows are written as they are checked, so a run
interrupted at comment 900 of 1000 leaves a valid file of the first 900 rather
than nothing.

You get `labelled.csv` and `labelling_failures.csv`.

Then count them, and read a sample. Neither command calls a model, so both are
free and work on a file produced months ago:

```bash
uv run ofc counts --labelled labelled.csv
uv run ofc review --labelled labelled.csv --sample 20
```

`counts` gives comments per theme, split by primary and secondary and by
valence. `review` prints twenty labels with each quote directly beneath the
comment it came from, and writes `review.csv` with an empty `agree` column.
The sample is drawn from a fixed seed, so working through it over several
sittings gives you the same rows.

`review` deliberately does not turn your answers into a score. A number
computed by one reader on a sample they chose would look like validation, and
it is not one.

### Graphical version

```bash
uv run --extra ui streamlit run app.py
```

Same steps, same checks, with the codebook in an editable table instead of a
text editor. It will also open a `codebook.yaml` you already have, so you can
propose on the command line and edit here; a codebook it would refuse on the
command line is refused here too, with the same message.

## Reading your own CSV

| Flag | |
|---|---|
| `--text-column` | Required. The column holding the open-ended answers. |
| `--id-column` | Optional. Defaults to the row number, counted from the first data row. |
| `--delimiter` | Defaults to `,`. Use `--delimiter $'\t'` for TSV. |
| `--encoding` | Defaults to `utf-8-sig`, which tolerates a byte-order mark. |

If the column you name is not there, the error lists the columns that are.

Two kinds of row are dropped before any model call, and both are reported:
empty cells, and cells whose whole content is one of `n/a`, `n.a.`, `na`,
`none`, `nil`, `null`, `-`, `--`, `.`, `?`, `blank`, `<blank>`. Row numbers
still refer to the original file, so a dropped row leaves a gap rather than
shifting everything after it.

## Output

`labelled.csv` is in long format: one row per comment-theme pair. A comment
with three themes occupies three rows; an unassigned comment occupies one.

| Column | |
|---|---|
| `comment_id` | From `--id-column`, or the row number. |
| `row_number` | Position in the input file, counting data rows from 1. |
| `comment_text` | The comment, NFC-normalised. |
| `assignment` | `primary`, `secondary`, or `unassigned`. |
| `theme_id`, `theme_label` | From your approved codebook. Empty when unassigned. |
| `valence` | `positive`, `negative` or `neutral`, for this theme in this comment. Empty when unassigned. |
| `quote` | The span of `comment_text` supporting this label. Empty when unassigned. |
| `quote_start`, `quote_end` | Character offsets into `comment_text`. |

A comment that fits no theme in your codebook gets a single row with
`assignment = unassigned` and no quote — it is the one row type without one.
The model is told not to stretch a theme to fit, so the count of unassigned
comments is information about your codebook, not noise to be cleared.

`labelling_failures.csv` holds everything the checks refused. Its `scope`
column says what was refused:

- **`comment`** — the whole comment was kept out of the results.
  `failure_reason` is one of `quote_not_found_in_comment`, `unknown_theme_id`,
  `invalid_valence`, `no_primary_theme`, `multiple_primary_themes`,
  `too_many_secondary_themes`, `malformed_response`, `model_error`.
- **`assignment`** — one label was removed and the comment stayed. The only
  reason is `duplicate_theme`, where the model assigned a comment the same
  theme twice.

A comment rejected at `comment` scope appears in this file and nowhere else. A
comment with an `assignment`-scope row appears in both files, which is the
point: you can see what was taken off it and why.

## How a quote is checked

Two transformations, kept apart deliberately.

**On read.** Every comment is normalised to Unicode NFC, once. That is the
canonical text: it is what the model sees, what is written to `comment_text`,
and what the offsets refer to.

**On matching.** A quote is compared against the comment in a folded form that
ignores differences models get wrong while copying, and nothing else:

- the quote is put through the same NFC normalisation the comment already
  went through, so a decomposed accent matches its composed form;
- runs of whitespace (including tabs, newlines and non-breaking spaces)
  collapse to a single space, and leading and trailing whitespace is dropped;
- invisible formatting characters — soft hyphen, zero-width space, zero-width
  joiner and non-joiner, word joiner, byte-order mark — are removed rather
  than folded to a space, so one sitting inside a word does not create a word
  break that neither you nor the model can see;
- curly quotes, primes and backticks fold to `'` and `"`;
- en dashes, em dashes, figure dashes and minus signs fold to `-`.

Case, wording and word order are not touched, so a paraphrase does not match.

The folded forms decide whether the quote is there; the offsets that come back
point into the canonical text. This means the string written to the `quote`
column is lifted out of the comment itself, keeping its original punctuation,
rather than being whatever the model typed. That is what makes
`comment_text[quote_start:quote_end] == quote` hold exactly.

Checking is all or nothing per comment. If one of a comment's three quotes
cannot be located, the whole comment is excluded rather than published with
its two surviving labels: a model that invented one quote has not earned trust
on the others, and in a spreadsheet a partly verified row looks exactly like a
fully verified one.

A call that never produced an answer is a different matter, and is retried:
connection errors, timeouts and rate limits are tried again with exponential
backoff, up to `--max-retries` (default 3, and `0` turns it off), and the
number of retries is reported at the end of the run. That is not a softening
of the rule above. The rule is about the model's answer, where a second
attempt at the same comment is a second chance to invent a quote; a dropped
connection produced no answer to judge.

One case is treated differently. A theme assigned twice to the same comment is
a formatting slip rather than an invention — the repeat names nothing the
comment does not already carry, so there is no judgement to make about which
of the two to keep. The repeat is dropped, the comment stays, and the drop is
recorded at `assignment` scope. Its quote is verified before that decision is
taken, so a fabricated quote is never waved through merely because the theme
it came attached to had already been used.

## Where the model is used, and where it is not

Used to read text and propose language: which themes emerge from the corpus,
which theme a comment belongs to, which span of the comment supports that, and
whether the tone is positive, negative or neutral.

Not used for anything else. The three counts printed at the end of a run are
tallied in code. Quote verification is string matching. Dropping empty and
placeholder answers is a fixed list. The token count shown before a run is
measured by encoding the text that will be sent; the cost figure is arithmetic
over prices you supply.

## Definitions the tool commits to

Primary theme, quoted from the labelling prompt:

> The primary theme is what the comment is mainly about: the concern or
> experience that prompted the person to write it. Secondary themes are
> mentioned but are not what the comment is driving at.

This is a judgement the model makes. Unlike quote verification, it cannot be
checked in code — it is stated here so that what was asked for is on the
record.

Valence is per comment-theme pair, not per comment: one comment can be
positive about pay and negative about onboarding, and both are recorded.

At most three themes per comment (one primary, up to two secondary), and at
most `--max-themes` themes in a proposed codebook (default 15). Both report it
when the model overshoots, but they do different things about it. A proposal
longer than the cap is truncated to the cap, and the number dropped is printed.
A comment handed more than two secondary themes is excluded whole into the
failures file under `too_many_secondary_themes`, because there is no
principled way to choose which of the surplus themes to discard.

## Size of a run

The codebook is proposed from the entire corpus in a single call. There is no
sampling, because choosing which comments stand for the rest is a
methodological decision this tool will not make for you.

Every run prints its measured input token count before sending anything.
`--max-input-tokens N` stops the run if that count goes over `N`. If a corpus
is too large for the model you chose, the run stops and reports the number;
what to do about it is yours to decide.

## Trying it on real open text

Apart from ten answers kept as test fixtures, described under Development
below, the repository ships no survey data. `scripts/download_dataset.py` converts
the open-ended answers of the 2025 UC OSPO Network open source survey into a
CSV this tool can read.

```bash
uv run python scripts/download_dataset.py
```

The data is on Dryad at <https://doi.org/10.5061/dryad.2280gb662>, licensed
CC0 and de-identified by its authors, with the free-text answers in a Word
file inside the archive. Dryad serves downloads behind an automated browser
check, so the script often cannot fetch the archive for you; when it cannot,
it prints the link to click and how to re-run it with `--archive`. The Zenodo
deposit associated with this survey holds the authors' R analysis code under
BSD-3, and its `data/` directory is deliberately empty, so it is not a source
for the answers.

### What is actually in it

318 answers across eight free-text questions. They are not all the same kind
of thing, and the script prints this breakdown so you can see that before you
spend anything:

| Question | Answers | Median length |
|---|---|---|
| Q4, Q6, Q7, Q15, Q19 — "Other" write-ins | 77 | 21–50 characters |
| Q8 — where code is shared | 26 | 15 characters |
| Q18 — primary field of study | 174 | 15 characters |
| **Q12 — other challenges, or support you would find helpful** | **41** | **175 characters** |

Only Q12 was asked as an open question, and only its answers read like the
survey comments this tool is built for. The other 277 are mostly one or two
words, and a codebook induced over all 318 is dominated by names of academic
disciplines. So write out that question on its own:

```bash
uv run python scripts/download_dataset.py --question Q12
```

Then run the tool over it, remembering that 41 comments is a small corpus:

```bash
uv run ofc propose --input data/ospo_open_responses_Q12.csv \
                   --text-column response \
                   --id-column response_id
```

**This is not workplace feedback.** It is a survey of academic open source
contributors. It is here for one reason: to run the pipeline over real
open-ended text that somebody else wrote, with the typos, fragments and odd
punctuation that implies, instead of over text invented to make the tool look
good.

Cite the data as: Scarlett, Curty, Gomez et al. (2026), *Survey responses from
the 2025 UC OSPO Network open source survey* [Data set], Dryad.

## Development

```bash
uv sync
uv run pytest
```

The suite runs offline: no API key, no network access, no model calls. The
model is replaced by a stand-in that returns fixed responses, which is what
makes the failure paths reachable at all.

The central test checks the quote guarantee over randomised slices of comments
containing curly quotes, em dashes, non-breaking spaces, soft hyphens,
zero-width spaces and joiners, byte-order marks and CRLF line endings. It is
then checked again on the rows the labeller builds, and again on the CSV an
end-to-end run writes to disk, because that file is what anyone auditing the
output will actually read.

Composition differences — a decomposed accent against its composed form — are
covered by their own tests rather than by that one. The randomised test
normalises both sides to NFC before comparing, as the pipeline does, so by
construction it cannot produce a pair that differs only in composition.

Ten of the real Q12 answers are committed under `tests/fixtures/`, with their
provenance in `tests/fixtures/SOURCE.md`. They carry what invented test
strings do not: non-breaking spaces mid-sentence, a curly apostrophe,
bracketed redactions made by the survey's own authors, respondents numbering
their own points, and lengths up to 1042 characters. The suite quotes hundreds
of spans of them back at the verifier, retyped the way a model flattens
typography, and checks that what gets stored is the source text rather than
the retyping.

When the full corpus has been downloaded, every one of its 318 answers is
checked the same way. Most of that corpus is one- and two-word answers to
write-in options, too short to slice into spans, so those are quoted whole —
which is what a model would do with them anyway.

The web interface is driven by `streamlit.testing.v1.AppTest` rather than left
to a screenshot. The one bug that made it useless — every upload failed — was
invisible on the page before the upload.

## Licence

MIT. See [LICENSE](LICENSE).
