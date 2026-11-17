"""Fetch a complete Digital SAT snapshot and commit it to SQLite.

API request shapes adapted from Martin Svítek's moduledrill/refresh_questions.py.
See LICENSE for the upstream MIT license.
"""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
import json
import random
import re
import sqlite3
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bank import DEFAULT_DB, connect, record_change


BANK_URL = "https://qbank-api.collegeboard.org/msreportingquestionbank-prod/questionbank/digital/"
SECTIONS = {
    "Math": {"test": 2, "domain": "H,P,Q,S"},
    "Reading & Writing": {"test": 1, "domain": "INI,CAS,EOI,SEC"},
}
QUESTION_FIELDS = (
    "question_id", "external_id", "uid", "ibn", "section", "domain_code", "domain",
    "skill_code", "skill", "difficulty", "question_type", "answer_source", "stimulus", "stem",
    "answer_options", "correct_answers", "explanation", "figures",
    "metadata_json", "detail_json",
)


def json_text(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def request_json(endpoint, payload, *, attempts=4):
    request = Request(
        BANK_URL + endpoint, json_text(payload).encode(),
        {"Content-Type": "application/json", "Accept": "application/json"},
    )
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=25) as response:
                return json.load(response)
        except (TimeoutError, URLError, OSError, ValueError) as error:
            if isinstance(error, HTTPError) and error.code not in (408, 429, 500, 502, 503, 504):
                raise
            if attempt + 1 == attempts:
                raise
            retry_after = error.headers.get("Retry-After", "") if isinstance(error, HTTPError) else ""
            delay = min(60, int(retry_after)) if retry_after.isdigit() else 2**attempt
            time.sleep(delay + random.random())


class FigureParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.figures = []

    def handle_starttag(self, tag, attrs):
        if tag in ("img", "svg"):
            fields = dict(attrs)
            self.figures.append({
                "kind": tag,
                "src": fields.get("src") or fields.get("data-src"),
                "alt": fields.get("alt") or fields.get("aria-label"),
            })



class RationaleText(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.parts = []
        self.feed(markup)

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.parts.append(dict(attrs).get("alt") or "")

    def text(self):
        return " ".join("".join(self.parts).split())


def rationale_answers(explanation):
    text = RationaleText(explanation).text()
    match = re.match(r"The correct answers? (?:is|are) (?:either )?(.+?)(?:\.(?=\s|$)|$)",
                     text, re.I)
    if not match:
        return []
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
             "seven": 7, "eight": 8, "nine": 9}
    denominators = {"half": 2, "halves": 2, "third": 3, "fourth": 4, "fifth": 5,
                    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}
    text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", match[1].lower())
    answers = []
    for value in re.split(r"\s*(?:,\s*(?:or\s+|and\s+)?|\s+or\s+|\s+and\s+)\s*", text):
        value = value.replace("−", "-").replace("negative ", "-")
        value = re.sub(r"^the fraction ", "", value).replace(" over ", "/")
        spoken = value.split()
        if len(spoken) == 2:
            numerator, denominator = spoken
            divisor = denominators.get(denominator) or denominators.get(denominator.rstrip("s"))
            if numerator.lstrip("-") in words and divisor:
                sign = "-" if numerator.startswith("-") else ""
                value = f"{sign}{words[numerator.lstrip('-')]}/{divisor}"
        if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:/\d+)?", value):
            return []
        answers.append(value)
    return list(dict.fromkeys(answers))

def required_text(payload, name):
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing or invalid {name}")
    return value.strip()


def optional_text(payload, name):
    value = payload.get(name)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"invalid {name}")
    return value or ""


def lookup_id(meta):
    return optional_text(meta, "external_id") or required_text(meta, "ibn")


def list_questions():
    entries = {}
    for section, params in SECTIONS.items():
        response = request_json("get-questions", {"asmtEventId": 99, **params})
        if not isinstance(response, list) or not response:
            raise ValueError(f"{section} returned an empty or invalid listing")
        domains = set()
        for meta in response:
            if not isinstance(meta, dict) or meta.get("program") != "SAT":
                raise ValueError(f"{section} returned unexpected metadata")
            question_id = required_text(meta, "questionId")
            lookup_id(meta)
            domains.add(required_text(meta, "primary_class_cd"))
            entry = (section, meta)
            if question_id in entries and entries[question_id] != entry:
                raise ValueError(f"conflicting rows for College Board question {question_id}")
            entries[question_id] = entry
        if domains != set(params["domain"].split(",")):
            raise ValueError(f"{section} listing omitted or added unexpected domains: {domains}")
    return entries


def fetch_detail(key):
    detail = request_json("get-question", {"external_id": key})
    if not isinstance(detail, dict) or key not in (detail.get("externalid"), detail.get("item_id")):
        raise ValueError("detail response is missing or belongs to a different question")
    return detail


def legacy_fields(detail):
    answer = detail.get("answer")
    if not isinstance(answer, dict):
        raise ValueError("legacy answer payload is missing")
    style = required_text(answer, "style").lower()
    explanation = optional_text(answer, "rationale")
    choices = answer.get("choices") or {}
    if not isinstance(choices, dict):
        raise ValueError("invalid legacy choices")
    options = [{"id": key.upper(), "content": required_text(choice, "body")}
               for key, choice in sorted(choices.items())]
    if style == "multiple choice":
        correct = optional_text(answer, "correct_choice").upper()
        if correct:
            return "mcq", options, [correct], explanation, "structured"
        text = RationaleText(explanation).text()
        match = re.search(r"^Choice ([A-D]) is correct\b", text, re.I)
        return "mcq", options, [match[1].upper()] if match else [], explanation, "rationale"
    if style == "spr":
        return "spr", options, rationale_answers(explanation), explanation, "rationale"
    raise ValueError(f"unknown legacy answer style {style}")


def format_question(entry, detail):
    section, meta = entry
    external_id = meta.get("external_id")
    if external_id:
        question_type = required_text(detail, "type")
        stem = required_text(detail, "stem")
        stimulus = optional_text(detail, "stimulus")
        explanation = optional_text(detail, "rationale")
        options = detail.get("answerOptions") or []
        answers = detail.get("correct_answer") or detail.get("keys") or []
        answer_source = "structured"
    else:
        question_type, options, answers, explanation, answer_source = legacy_fields(detail)
        stem = optional_text(detail, "prompt")
        stimulus = optional_text(detail, "body")
        if not (stem or stimulus):
            raise ValueError("legacy question has no prompt or body")
    if not isinstance(options, list) or not isinstance(answers, list):
        raise ValueError("answer payload is invalid")
    if not answers and (answer_source == "structured" or not explanation):
        raise ValueError("answer payload is incomplete")
    if question_type == "mcq" and not options:
        raise ValueError("multiple-choice options are missing")
    option_fields = []
    for option in options:
        if not isinstance(option, dict):
            raise ValueError("invalid answer option")
        option_fields.append(required_text(option, "content"))

    figures = FigureParser()
    for markup in (stimulus, stem, *option_fields, explanation):
        figures.feed(markup)
    return dict(zip(QUESTION_FIELDS, (
        meta["questionId"], external_id, meta.get("uId"), meta.get("ibn"), section,
        required_text(meta, "primary_class_cd"), required_text(meta, "primary_class_cd_desc"),
        required_text(meta, "skill_cd"), required_text(meta, "skill_desc"),
        required_text(meta, "difficulty"), question_type, answer_source, stimulus, stem,
        json_text(options), json_text(answers), explanation, json_text(figures.figures),
        json_text(meta), json_text(detail),
    ), strict=True))


def cached_details(path, entries):
    if not path.exists():
        return {}
    with connect(path, read_only=True) as db:
        previous = {
            row["question_id"]: row
            for row in db.execute("SELECT question_id, metadata_json, detail_json FROM questions WHERE is_listed = 1")
        }
    details = {}
    for key, (_, metadata) in entries.items():
        old = previous.get(key)
        if old and old["metadata_json"] == json_text(metadata):
            details[lookup_id(metadata)] = json.loads(old["detail_json"])
    # Refetch shared bodies when any associated row is new or carries a catalog change.
    for key, (_, metadata) in entries.items():
        old = previous.get(key)
        if old is None or old["metadata_json"] != json_text(metadata):
            details.pop(lookup_id(metadata), None)
    return details


def fetch_snapshot(workers, path=None):
    entries = list_questions()
    counts = Counter(section for section, _ in entries.values())
    print(f"College Board lists {len(entries):,} questions ({dict(counts)}).", file=sys.stderr, flush=True)
    lookup_ids = sorted({lookup_id(meta) for _, meta in entries.values()})
    details = cached_details(path, entries) if path else {}
    missing = [key for key in lookup_ids if key not in details]
    print(f"Reusing {len(details):,} unchanged details. Fetching {len(missing):,}.", file=sys.stderr, flush=True)
    errors = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = {executor.submit(fetch_detail, key): key for key in missing}
        for number, future in enumerate(as_completed(pending), 1):
            question_id = pending[future]
            try:
                details[question_id] = future.result()
            except (OSError, ValueError) as error:
                errors[question_id] = str(error)
            if number % 250 == 0 or number == len(missing):
                print(f"Fetched {len(details):,}/{len(lookup_ids):,} details ({len(errors)} errors).",
                      file=sys.stderr, flush=True)
    if errors:
        for question_id, error in list(errors.items())[:8]:
            print(f"  {question_id}: {error}", file=sys.stderr)
        raise ValueError(f"{len(errors):,} question detail requests failed")
    # Re-read the listings to catch catalog changes while detail requests were in flight.
    if list_questions() != entries:
        raise ValueError("College Board listings changed during the fetch. Run the import again.")
    questions = {}
    for key, entry in entries.items():
        try:
            questions[key] = format_question(entry, details[lookup_id(entry[1])])
        except ValueError as error:
            raise ValueError(f"question {key}: {error}") from error
    return questions


def save_snapshot(path, questions, observed_at):
    keys = ", ".join(QUESTION_FIELDS)
    values = ", ".join(":" + name for name in QUESTION_FIELDS)
    updates = ", ".join(f"{name} = excluded.{name}" for name in QUESTION_FIELDS[1:])
    report = {name: [] for name in ("added", "updated", "removed", "returned")}
    if not questions:
        raise ValueError("refusing to save an empty snapshot")
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        last = db.execute("SELECT observed_at FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
        if last and observed_at <= last["observed_at"]:
            raise ValueError("a newer snapshot is already saved")
        existing = {
            row["question_id"]: row
            for row in db.execute(f"SELECT {keys}, is_listed FROM questions")
        }
        run = db.execute("INSERT INTO sync_runs(observed_at, total) VALUES (?, ?)",
                         (observed_at, len(questions))).lastrowid
        for question_id, question in questions.items():
            previous = existing.get(question_id)
            if previous is None:
                report["added"].append(question_id)
            else:
                if not previous["is_listed"]:
                    report["returned"].append(question_id)
                if any(question[name] != previous[name] for name in QUESTION_FIELDS[1:]):
                    report["updated"].append(question_id)
        db.executemany(f"""
            INSERT INTO questions (
                {keys}, first_seen_at, last_seen_at, first_seen_run, last_seen_run, is_listed
            ) VALUES ({values}, :seen, :seen, :run, :run, 1)
            ON CONFLICT(question_id) DO UPDATE SET
                {updates}, last_seen_at = excluded.last_seen_at,
                last_seen_run = excluded.last_seen_run, is_listed = 1
        """, ({**q, "seen": observed_at, "run": run} for q in questions.values()))
        report["removed"] = [
            question_id for question_id in existing.keys() - questions.keys()
            if existing[question_id]["is_listed"]
        ]
        db.execute("UPDATE questions SET is_listed = 0 WHERE is_listed = 1 AND last_seen_run <> ?", (run,))
        if any(report.values()):
            record_change(db, observed_at)
    return {key: sorted(values) for key, values in report.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--workers", type=int, default=8, choices=range(1, 17), metavar="1..16")
    parser.add_argument("--json", action="store_true", help="print every changed ID as JSON")
    parser.add_argument("--full", action="store_true", help="refetch every body even if catalog metadata agrees")
    args = parser.parse_args()
    observed_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    try:
        questions = fetch_snapshot(args.workers, None if args.full else args.db)
        changes = save_snapshot(args.db, questions, observed_at)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"Import failed. The last successful snapshot is preserved. {error}\n")
    totals = {name: len(ids) for name, ids in changes.items()}
    if args.json:
        print(json.dumps({"observed_at": observed_at, "listed": len(questions),
                          "totals": totals, "question_ids": changes}, indent=2))
    else:
        print(f"{len(questions):,} listed. " + ", ".join(f"{n:,} {label}" for label, n in totals.items()))
        print(args.db.resolve())


if __name__ == "__main__":
    main()
