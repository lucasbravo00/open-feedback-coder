#!/usr/bin/env python3
"""Fetch the UC OSPO Network 2025 survey and convert its open answers to CSV.

The survey data is not part of this repository. This script downloads it and
reshapes the open-ended answers into the two-column CSV that `ofc` reads.

Where the data actually lives, which is not obvious from the paper:

  Data  : Dryad, https://doi.org/10.5061/dryad.2280gb662, licensed CC0.
          The open-ended answers are inside dryad_data_final.tar.gz, in a Word
          file (qual.docx). Dryad puts its downloads behind an automated
          browser check, so this script cannot always fetch the archive for
          you; when it cannot, it tells you the one click to make and how to
          hand it the file you downloaded.
  Code  : Zenodo, https://doi.org/10.5281/zenodo.17783102, licensed BSD-3.
          That deposit holds the authors' R analysis only. Its data/ directory
          is empty on purpose, so it is not a source for the survey answers.

Only one of the survey's eight free-text questions, Q12, was asked as an open
question; the rest are write-in options answered in a word or two. The script
prints the breakdown, and --question writes just one question's answers.

Usage:
    python scripts/download_dataset.py
    python scripts/download_dataset.py --archive ~/Downloads/dryad_data_final.tar.gz
    python scripts/download_dataset.py --question Q12
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import statistics
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile
from xml.etree import ElementTree

DATASET_DOI = "10.5061/dryad.2280gb662"
DATASET_PAGE = f"https://datadryad.org/dataset/doi:{DATASET_DOI}"
DRYAD_API = "https://datadryad.org/api/v2"
ARCHIVE_NAME = "dryad_data_final.tar.gz"
QUAL_DOCUMENT_SUFFIX = ".docx"

WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Responses in the Word file are numbered for readability. The authors say
# those numbers are arbitrary and match nothing in the other files, so they
# are stripped rather than kept as ids.
LEADING_NUMBER = re.compile(r"^\s*\d+[.)]\s+")
# Each block of answers is introduced by the question that produced it, in the
# form "Q12: Are there any other challenges you've encountered...".
SECTION_HEADING = re.compile(r"^(Q\d+):\s*(.+)$")

MANUAL_INSTRUCTIONS = f"""\
Dryad served an automated browser check instead of the file, so this script
cannot download it for you. Download it once by hand:

  1. Open {DATASET_PAGE}
  2. Download {ARCHIVE_NAME}
  3. Re-run this script pointing at the file you downloaded:

     python scripts/download_dataset.py --archive /path/to/{ARCHIVE_NAME}
"""


def fetch(url: str, timeout: int = 120) -> bytes:
    request = urllib.request.Request(url, headers={"Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def find_archive_url() -> str | None:
    """Ask the Dryad API which file holds the data in the current version."""
    try:
        dataset = json.loads(fetch(f"{DRYAD_API}/datasets/doi%3A{DATASET_DOI.replace('/', '%2F')}"))
        version_href = dataset["_links"]["stash:version"]["href"]
        listing = json.loads(fetch(f"https://datadryad.org{version_href}/files"))
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as error:
        print(f"Could not read the Dryad file listing: {error}", file=sys.stderr)
        return None

    for entry in listing.get("_embedded", {}).get("stash:files", []):
        if entry.get("path", "").endswith(".tar.gz"):
            href = entry.get("_links", {}).get("stash:download", {}).get("href")
            if href:
                return f"https://datadryad.org{href}"
    return None


def download_archive(destination: str) -> bool:
    """Try to download the data archive. Returns False if Dryad blocked us."""
    url = find_archive_url()
    if url is None:
        return False

    print(f"Downloading {url}", file=sys.stderr)
    try:
        payload = fetch(url)
    except urllib.error.HTTPError as error:
        print(f"Dryad refused the download ({error.code}).", file=sys.stderr)
        return False
    except urllib.error.URLError as error:
        print(f"Could not reach Dryad: {error}", file=sys.stderr)
        return False

    # A browser check answers with HTML; the real file is a gzip stream.
    if not payload.startswith(b"\x1f\x8b"):
        return False

    with open(destination, "wb") as handle:
        handle.write(payload)
    return True


def extract_qualitative_document(archive_path: str) -> tuple[str, bytes]:
    """Pull the Word file holding the open-ended answers out of the tarball."""
    with tarfile.open(archive_path, "r:gz") as archive:
        candidates = [
            member
            for member in archive.getmembers()
            if member.isfile()
            and member.name.lower().endswith(QUAL_DOCUMENT_SUFFIX)
            and "instrument" not in member.name.lower()
        ]
        if not candidates:
            raise SystemExit(
                f"No open-response document found inside {archive_path}. "
                "The deposit may have been restructured; open the archive and check."
            )
        member = min(candidates, key=lambda entry: len(entry.name))
        handle = archive.extractfile(member)
        if handle is None:
            raise SystemExit(f"Could not read {member.name} from {archive_path}.")
        return member.name, handle.read()


def paragraphs_of(document: bytes) -> list[str]:
    """Return the visible paragraphs of a .docx, in order."""
    with zipfile.ZipFile(io.BytesIO(document)) as bundle:
        xml = bundle.read("word/document.xml")

    root = ElementTree.fromstring(xml)
    paragraphs = []
    for paragraph in root.iter(f"{WORD_NAMESPACE}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{WORD_NAMESPACE}t"))
        text = text.strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def to_rows(paragraphs: list[str]) -> list[dict]:
    """Split paragraphs into question sections and responses.

    Deliberately literal: a paragraph is either a question heading or one
    response. Nothing is merged, reworded, filtered or dropped beyond the
    display numbering, so the CSV holds every answer the deposit holds.
    """
    rows: list[dict] = []
    question_id = ""
    question_text = ""

    for paragraph in paragraphs:
        heading = SECTION_HEADING.match(paragraph)
        if heading:
            question_id, question_text = heading.group(1), heading.group(2).strip()
            continue
        rows.append(
            {
                "response_id": str(len(rows) + 1),
                "question_id": question_id,
                "question_text": question_text,
                "response": LEADING_NUMBER.sub("", paragraph),
            }
        )

    return rows


def summarise(rows: list[dict]) -> str:
    """Describe what came out, so nobody has to guess at the shape of it."""
    order: list[str] = []
    counts: dict[str, list[int]] = {}
    for row in rows:
        key = row["question_id"] or "(no question)"
        if key not in counts:
            counts[key] = []
            order.append(key)
        counts[key].append(len(row["response"]))

    lines = [f"{'question':<12}{'answers':>9}{'median length':>15}"]
    for key in order:
        median = round(statistics.median(counts[key]))
        lines.append(f"{key:<12}{len(counts[key]):>9}{median:>15}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--archive",
        metavar="FILE",
        help=f"Use an already downloaded {ARCHIVE_NAME} instead of fetching it.",
    )
    parser.add_argument(
        "--out-dir", default="data", metavar="DIR", help="Where to write the CSV (default: data)."
    )
    parser.add_argument(
        "--question",
        metavar="ID",
        help="Keep only one question's answers, for example Q12.",
    )
    parser.add_argument(
        "--dump-paragraphs",
        action="store_true",
        help="Print the raw paragraphs of the Word file and stop, for inspection.",
    )
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    archive_path = args.archive or os.path.join(args.out_dir, ARCHIVE_NAME)

    if not args.archive and not os.path.exists(archive_path):
        if not download_archive(archive_path):
            print(MANUAL_INSTRUCTIONS, file=sys.stderr)
            return 1

    name, document = extract_qualitative_document(archive_path)
    print(f"Reading {name}", file=sys.stderr)

    paragraphs = paragraphs_of(document)

    if args.dump_paragraphs:
        for paragraph in paragraphs:
            print(paragraph)
        return 0

    rows = to_rows(paragraphs)
    if not rows:
        raise SystemExit(
            "No responses were found in the document. Run again with "
            "--dump-paragraphs to see what it actually contains."
        )

    if args.question:
        wanted = args.question.strip().upper()
        available = sorted(
            {row["question_id"] for row in rows if row["question_id"]},
            key=lambda value: int(value.lstrip("Q") or 0),
        )
        kept = [row for row in rows if row["question_id"].upper() == wanted]
        if not kept:
            raise SystemExit(
                f"No answers found for {args.question!r}. "
                f"This file has: {', '.join(available)}."
            )
        print(
            f"Keeping {len(kept)} of {len(rows)} answers ({wanted} only).",
            file=sys.stderr,
        )
        rows = kept
        for index, row in enumerate(rows, start=1):
            row["response_id"] = str(index)

    suffix = f"_{args.question.strip().upper()}" if args.question else ""
    out_path = os.path.join(args.out_dir, f"ospo_open_responses{suffix}.csv")
    columns = ["response_id", "question_id", "question_text", "response"]
    with open(out_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    questions = {row["question_id"] for row in rows if row["question_id"]}
    print(
        f"Wrote {out_path}: {len(rows)} responses across {len(questions)} questions.",
        file=sys.stderr,
    )
    if not questions:
        print(
            "Warning: no question headings were recognised, so every paragraph "
            "was treated as a response. Check the file with --dump-paragraphs.",
            file=sys.stderr,
        )
    else:
        print(file=sys.stderr)
        print(summarise(rows), file=sys.stderr)
        if not args.question:
            print(
                "\nMost of these questions are write-in options answered in a word "
                "or two. Q12 is the one asked as an open question, and its answers "
                "read like survey comments. Re-run with --question Q12 to write "
                "only those.",
                file=sys.stderr,
            )
    print(
        "\nThis data is CC0 (Scarlett, Curty, Gomez et al., 2026, "
        f"https://doi.org/{DATASET_DOI}). It is survey data about open source "
        "contribution, not workplace feedback.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
