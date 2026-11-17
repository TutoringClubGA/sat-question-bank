"""Run the read-only SAT question browser on localhost."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qs, urlsplit

from bank import DEFAULT_DB, ROOT, connect


WEB = ROOT / "web"
FILTERS = ("section", "domain", "skill", "skill_code", "difficulty", "release_period")
CATALOG_DATE = "date(json_extract(metadata_json, '$.createDate') / 1000.0, 'unixepoch')"
DATED_BANK = f"""(
    SELECT *, COALESCE({CATALOG_DATE}, substr(first_seen_at, 1, 10)) AS catalog_date
    FROM question_bank
)"""
OLDER_LABELS = ("Pre-July 2026", "Pre-July 2026 (catalog date)")
NAMED_BANKS = ("Mr. Thomas release", "July 30, 2026")
COLUMNS = "question_id, section, domain, skill, difficulty, release_period, first_seen_at, last_seen_at, is_listed"
DETAIL_COLUMNS = (
    COLUMNS + ", question_type, stimulus, stem, answer_options, correct_answers, explanation, answer_source"
    + ", external_id, uid, ibn, domain_code, skill_code, first_seen_run, last_seen_run, metadata_json, catalog_date"
)
ORDER = "ORDER BY section, domain, skill, difficulty, question_id"
PAGE_SIZE = 40
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


def picker_data(database, query):
    rows = database.execute(f"""
        SELECT question_id, release_period, catalog_date FROM {DATED_BANK}
        WHERE is_listed = 1 AND section = ? AND skill_code = ? AND difficulty = ?
        {ORDER}
    """, tuple(query.get(field, [""])[0] for field in ("section", "skill_code", "difficulty")))
    return {"questions": [dict(row) for row in rows]}


def directory_releases(database):
    rows = database.execute(f"""
        SELECT question_id, section, skill, skill_code, difficulty, release_period, catalog_date,
            CASE WHEN first_seen_run = (SELECT min(id) FROM sync_runs)
                 THEN catalog_date ELSE date(first_seen_at) END AS added_date
        FROM {DATED_BANK} WHERE is_listed = 1
        {ORDER}
    """)
    return {"questions": [dict(row) for row in rows]}


def database_status(database):
    last_sync = database.execute("SELECT max(observed_at) FROM sync_runs").fetchone()[0]
    last_change = None
    if database.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'bank_change'").fetchone():
        row = database.execute("SELECT changed_at FROM bank_change WHERE id = 1").fetchone()
        if row:
            last_change = row["changed_at"]
    last_update = max(filter(None, (last_sync, last_change)), default=None)
    if last_change:
        return {"last_update": last_update, "last_change": last_change, "change_from_catalog": False}
    catalog_change = database.execute(f"""
        SELECT max(max(COALESCE({CATALOG_DATE}, ''),
                       COALESCE(date(json_extract(metadata_json, '$.updateDate') / 1000.0, 'unixepoch'), '')))
        FROM questions
    """).fetchone()[0] or None
    return {"last_update": last_update, "last_change": catalog_change, "change_from_catalog": bool(catalog_change)}


def directory_dates(database):
    rows = database.execute(f"""
        SELECT skill_code, difficulty, max(catalog_date) AS latest
        FROM {DATED_BANK} WHERE is_listed = 1
        GROUP BY skill_code, difficulty
    """)
    return {"dates": [dict(row) for row in rows], "database_status": database_status(database)}


def question_data(database, key):
    row = database.execute(f"SELECT {DETAIL_COLUMNS} FROM {DATED_BANK} WHERE question_id = ?", (key,)).fetchone()
    if row is None:
        return None
    question = dict(row)
    question["catalog_metadata"] = json.loads(question.pop("metadata_json"))
    for field in ("answer_options", "correct_answers"):
        question[field] = json.loads(question[field])
    question["release_sources"] = [dict(evidence) for evidence in database.execute("""
        SELECT period, source, source_id, match_method FROM release_evidence
        WHERE question_pk = (SELECT id FROM questions WHERE question_id = ?)
        ORDER BY period, source
    """, (key,))]
    return question


def question_list(database, query):
    conditions = []
    values = []
    for field in FILTERS:
        if value := query.get(field, [""])[0]:
            conditions.append(f"{field} = ?")
            values.append(value)
    source = query.get("source", [""])[0]
    if source == "older":
        conditions.append("(release_period IN (?, ?) OR (release_period NOT IN (?, ?) AND catalog_date < ?))")
        values.extend((*OLDER_LABELS, *NAMED_BANKS, "2026-07-30"))
    elif source in ("thomas", "john"):
        conditions.append("release_period = ?")
        values.append(NAMED_BANKS[0 if source == "thomas" else 1])
    elif source:
        if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", source) or source < "2026-08":
            raise ValueError("Invalid source month.")
        conditions.append("release_period NOT IN (?, ?, ?, ?) AND substr(catalog_date, 1, 7) = ?")
        values.extend((*OLDER_LABELS, *NAMED_BANKS, source))
    if value := query.get("question_id", [""])[0].strip():
        conditions.append("question_id = ? COLLATE NOCASE")
        values.append(value)
    for field, operator in (("from", ">="), ("to", "<=")):
        if value := query.get(field, [""])[0]:
            conditions.append(f"substr(first_seen_at, 1, 10) {operator} ?")
            values.append(value)
    if query.get("include_removed") != ["1"]:
        conditions.append("is_listed = 1")
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    table = DATED_BANK if source else "question_bank"
    if query.get("ids_only") == ["1"]:
        return {"ids": [r[0] for r in database.execute(
            f"SELECT question_id FROM {table} {where} {ORDER}", values)]}
    try:
        offset = max(0, int(query.get("offset", ["0"])[0]))
    except ValueError as error:
        raise ValueError("offset must be an integer") from error
    total = database.execute(f"SELECT count(*) FROM {table} {where}", values).fetchone()[0]
    questions = database.execute(
        f"SELECT {COLUMNS} FROM {table} {where} {ORDER} LIMIT ? OFFSET ?",
        [*values, PAGE_SIZE, offset],
    )
    return {"total": total, "page_size": PAGE_SIZE, "questions": [dict(row) for row in questions]}


class Handler(BaseHTTPRequestHandler):
    def send(self, status, content_type, data):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; "
                         "object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'; "
                         "frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, data, status=200):
        self.send(status, "application/json; charset=utf-8", json.dumps(data).encode())

    def do_GET(self):
        request = urlsplit(self.path)
        if request.path in STATIC:
            filename, mime = STATIC[request.path]
            self.send(200, mime, (WEB / filename).read_bytes())
            return
        endpoints = ("/api/filters", "/api/questions", "/api/sources", "/api/picker",
                     "/api/directory-dates", "/api/directory-releases")
        if request.path not in endpoints and not request.path.startswith("/api/question/"):
            self.send_json({"error": "Page not found."}, 404)
            return
        try:
            with connect(self.server.db_path, read_only=True) as database:
                if request.path == "/api/filters":
                    rows = database.execute(
                        "SELECT DISTINCT section, domain, skill, difficulty, release_period FROM question_bank")
                    self.send_json({"filters": [dict(row) for row in rows]})
                elif request.path == "/api/questions":
                    self.send_json(question_list(database, parse_qs(request.query)))
                elif request.path == "/api/picker":
                    self.send_json(picker_data(database, parse_qs(request.query)))
                elif request.path == "/api/directory-releases":
                    self.send_json(directory_releases(database))
                elif request.path == "/api/directory-dates":
                    self.send_json(directory_dates(database))
                elif request.path == "/api/sources":
                    months = database.execute(f"""
                        SELECT DISTINCT substr(catalog_date, 1, 7) AS month FROM {DATED_BANK}
                        WHERE is_listed = 1 AND release_period NOT IN (?, ?, ?, ?)
                        AND catalog_date >= '2026-08-01' ORDER BY month
                    """, (*OLDER_LABELS, *NAMED_BANKS))
                    self.send_json({"months": [row["month"] for row in months]})
                else:
                    key = request.path.removeprefix("/api/question/")
                    question = question_data(database, key)
                    if question is None:
                        self.send_json({"error": "Question not found."}, 404)
                        return
                    self.send_json(question)
        except ValueError as error:
            self.send_json({"error": str(error)}, 400)
        except sqlite3.Error:
            self.send_json({"error": "The database is unavailable. Run python3 import_bank.py first."}, 503)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        server.db_path = args.db
        print(f"SAT Question Bank at http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
