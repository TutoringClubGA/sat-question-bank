"""Match historical SAT question IDs to the imported catalog."""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import csv
import io
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from bank import DEFAULT_DB, ROOT, connect, record_change


THOMAS_DOC = "https://docs.google.com/document/d/11BEte3RXJH63QI9ZfjL9aaopWT1o9TyWK6aZBwLQUto/edit"
JULY_MANIFEST = ROOT / "history" / "july30.csv"
OLDER_MANIFEST = ROOT / "history" / "august2025-archive.csv"
THOMAS_MANIFEST = ROOT / "history" / "mr-thomas.csv"
OLDER_ARCHIVE = "https://drive.google.com/drive/folders/1dU7W-CEl_RzInxcFeIx_ZCZWSgR3RRNK"
WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PRINTED_ID = re.compile(r"Question\s+ID\s*:\s*([0-9a-f]{8})\b", re.I)


def get_bytes(url):
    with urlopen(url, timeout=30) as response:
        if urlsplit(response.geturl()).hostname == "accounts.google.com":
            raise ValueError("Google requires sign-in for this source. Use a local export or an ID CSV.")
        return response.read()


def pdf_ids(data):
    try:
        result = subprocess.run(["pdftotext", "-layout", "-", "-"], input=data,
                                capture_output=True, check=True)
    except FileNotFoundError as error:
        raise ValueError("pdftotext is needed for PDF sources. Install Poppler or provide an ID CSV.") from error
    except subprocess.CalledProcessError as error:
        raise ValueError("could not read historical PDF") from error
    text = result.stdout.decode("utf-8")
    ids = PRINTED_ID.findall(text)
    if not ids:
        ids = re.findall(r"^\s*\d+\.\d+\s+([0-9a-f]{8})\b", text, re.I | re.M)
    return list(dict.fromkeys(key.lower() for key in ids))


def drive_download(url):
    url_parts = urlsplit(url)
    if url_parts.hostname != "drive.google.com":
        raise ValueError(f"expected a Google Drive PDF link, found {url}")
    file_id = parse_qs(url_parts.query).get("id", [""])[0]
    if not file_id:
        match = re.search(r"/file/d/([A-Za-z0-9_-]+)", url_parts.path)
        file_id = match[1] if match else ""
    if not file_id:
        raise ValueError(f"could not identify linked Google Drive PDF {url}")
    return get_bytes("https://drive.google.com/uc?" + urlencode({"export": "download", "id": file_id}))


def directory_ids(data):
    with ZipFile(io.BytesIO(data)) as document:
        links = {
            node.get("Id"): node.get("Target")
            for node in ET.fromstring(document.read("word/_rels/document.xml.rels"))
            if node.get("TargetMode") == "External"
        }
        body = ET.fromstring(document.read("word/document.xml"))
    text = " ".join(node.text or "" for node in body.iter(WORD + "t"))
    entries = [{"question_id": key} for key in PRINTED_ID.findall(text)]
    done = set()
    for link in body.iter(WORD + "hyperlink"):
        url = links.get(link.get(REL + "id"))
        label = "".join(node.text or "" for node in link.iter(WORD + "t"))
        if not url or url in done or re.search(r"answer|key", label, re.I):
            continue
        match = re.search(r"\((\d+)\s+questions?\)", label, re.I)
        parts = urlsplit(url)
        is_drive_file = parts.hostname == "drive.google.com" and (
            "id" in parse_qs(parts.query) or "/file/d/" in parts.path
        )
        is_pdf = is_drive_file or parts.path.lower().endswith(".pdf")
        if not is_pdf or (match and int(match[1]) == 0):
            continue
        pdf = drive_download(url) if urlsplit(url).hostname == "drive.google.com" else get_bytes(url)
        ids = pdf_ids(pdf)
        if not ids or (match and len(ids) != int(match[1])):
            raise ValueError(f"question ID count disagrees with {label!r} in {url}")
        entries.extend({"question_id": key, "source_url": url} for key in ids)
        done.add(url)
    return entries


def read_source(source):
    url_parts = urlsplit(str(source))
    if url_parts.scheme == "https":
        match = re.fullmatch(r"/document/d/([\w-]+)(?:/.*)?", url_parts.path)
        if url_parts.hostname != "docs.google.com" or not match:
            raise ValueError("provide a Google Docs URL or a local CSV, PDF, or DOCX")
        entries = directory_ids(get_bytes(f"https://docs.google.com/document/d/{match[1]}/export?format=docx"))
    else:
        path = Path(source)
        kind = path.suffix.lower()
        if path.is_dir():
            pdf_paths = sorted(path.rglob("*.pdf"))
            entries = [
                {**entry, "source_url": str(pdf)}
                for pdf in pdf_paths
                for entry in read_source(str(pdf))
            ]
        elif kind == ".csv":
            with path.open(encoding="utf-8-sig", newline="") as file:
                entries = []
                for row in csv.DictReader(file):
                    if "question_ids" in row:
                        # Original CSVs contain one semicolon-delimited ID list
                        # per worksheet and an optional totals row.
                        entries.extend({"question_id": key} for key in
                                       re.findall(r"\b[0-9a-f]{8}\b", row["question_ids"] or "", re.I))
                    else:
                        entries.append(row)
        elif kind == ".pdf":
            entries = [{"question_id": key} for key in pdf_ids(path.read_bytes())]
        elif kind == ".docx":
            entries = directory_ids(path.read_bytes())
        else:
            raise ValueError("historical files must be CSV, PDF, or DOCX")
    if not entries:
        raise ValueError(f"no question IDs found in {source}")
    return entries


ID_HEADERS = {
    "questionid": "question_id", "id": "question_id", "externalid": "external_id",
    "uid": "uid", "ibn": "ibn", "itemid": "ibn",
}


def apply_sources(path, sources, *, thomas_source=None):
    report = {}
    with connect(path) as database:
        database.execute("BEGIN IMMEDIATE")
        indexes = {field: defaultdict(set) for field in set(ID_HEADERS.values())}
        for row in database.execute("SELECT id, question_id, external_id, uid, ibn FROM questions"):
            for field, index in indexes.items():
                if row[field]:
                    index[row[field].lower()].add((row["id"], row["question_id"]))
        if not indexes["question_id"]:
            raise ValueError("run the live import before applying historical evidence")
        before = database.total_changes
        for period, label, entries in sources:
            matched = set()
            unmatched = []
            ambiguous = []
            for entry in entries:
                identifiers = {}
                for header, value in entry.items():
                    field = ID_HEADERS.get(re.sub(r"[^a-z]", "", header.lower())) if header else None
                    if field and value and value.strip():
                        identifiers[field] = re.sub(r"^id:\s*", "", value.strip(), flags=re.I).lower()
                if not identifiers:
                    raise ValueError(f"{label} has a row with no recognized question IDs")
                known = [indexes[field].get(value, set()) for field, value in identifiers.items()]
                candidates = set.intersection(*known)
                if not any(known):
                    unmatched.append(identifiers)
                    continue
                if len(candidates) != 1:
                    ambiguous.append({"ids": identifiers, "candidates": sorted(
                        candidate[1] for candidate in set.union(*known))})
                    continue
                (primary_key, question_id), = candidates
                source_url = entry.get("source_url") or label
                method = "+".join(sorted(identifiers))
                if label == thomas_source:
                    method = "mr_thomas:" + method
                database.execute("""
                    INSERT INTO release_evidence
                        (question_pk, period, source, source_id, match_method)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(question_pk, period, source, source_id) DO UPDATE SET
                        match_method = excluded.match_method
                    WHERE excluded.match_method GLOB 'mr_thomas:*'
                    AND release_evidence.match_method <> excluded.match_method
                """, (primary_key, period, source_url, json.dumps(identifiers, sort_keys=True), method))
                matched.add(question_id)
            report[label] = {"period": period, "rows": len(entries), "matched": len(matched),
                              "unmatched": unmatched, "ambiguous": ambiguous}
        if database.total_changes > before:
            record_change(database)
    return report



def apply_catalog_dates(path):
    """Store provisional dates from the public listing metadata."""
    cutoff = int(datetime(2026, 7, 30, tzinfo=timezone.utc).timestamp() * 1000)
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        catalog_rows = db.execute("""
            SELECT id, json_extract(metadata_json, '$.createDate') AS catalog_created_at
            FROM questions WHERE typeof(catalog_created_at) = 'integer'
            AND catalog_created_at BETWEEN 1 AND ?
            AND NOT EXISTS (SELECT 1 FROM release_evidence e
                            WHERE e.question_pk = questions.id
                            AND e.match_method <> 'catalog_created_at')
        """, (cutoff - 1,)).fetchall()
        evidence = {(row["id"], "Pre-July 2026", "College Board Educator Question Bank listing createDate",
                     datetime.fromtimestamp(row["catalog_created_at"] / 1000, timezone.utc).isoformat())
                    for row in catalog_rows}
        existing = {tuple(row) for row in db.execute("""
            SELECT question_pk, period, source, source_id FROM release_evidence
            WHERE match_method = 'catalog_created_at'
        """)}
        if evidence != existing:
            db.executemany("""
                DELETE FROM release_evidence WHERE (question_pk, period, source, source_id) = (?, ?, ?, ?)
            """, existing - evidence)
            db.executemany("""
                INSERT INTO release_evidence (question_pk, period, source, source_id, match_method)
                VALUES (?, ?, ?, ?, 'catalog_created_at')
            """, evidence - existing)
            record_change(db)
    return len(catalog_rows)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--july", default=str(JULY_MANIFEST), help="July CSV, PDF, DOCX, or Google Docs URL")
    parser.add_argument("--thomas", help="Mr. Thomas's CSV, PDF, folder of PDFs, DOCX, or Google Docs URL")
    parser.add_argument("--thomas-online", action="store_true", help="use the supplied Mr. Thomas Google Doc")
    parser.add_argument("--catalog-pre-july", action="store_true",
                        help="label pre-July-30 catalog creation dates as provisional evidence")
    args = parser.parse_args()
    try:
        sources = [("Pre-July 2026", OLDER_ARCHIVE, read_source(OLDER_MANIFEST))]
        thomas = args.thomas
        if not thomas and args.thomas_online:
            thomas = THOMAS_DOC
        if not thomas and THOMAS_MANIFEST.is_file():
            thomas = str(THOMAS_MANIFEST)
        if thomas:
            sources.append(("Pre-July 2026", thomas, read_source(thomas)))
        sources.append(("July 30, 2026", args.july, read_source(args.july)))
        report = apply_sources(args.db, sources, thomas_source=thomas)
        if args.catalog_pre_july:
            report["Pre-July 2026 (catalog date)"] = {"matched": apply_catalog_dates(args.db)}
        print(json.dumps(report, indent=2))
    except (OSError, ValueError, sqlite3.Error, ET.ParseError, BadZipFile) as error:
        parser.exit(1, f"Backfill failed. {error}\n")


if __name__ == "__main__":
    main()
