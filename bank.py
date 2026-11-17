"""SQLite access shared by the SAT importer and the local web app."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "sat.sqlite3"
SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY,
    observed_at TEXT NOT NULL,
    total INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS bank_change (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    changed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY,
    question_id TEXT NOT NULL UNIQUE,
    external_id TEXT,
    uid TEXT,
    ibn TEXT,
    section TEXT NOT NULL,
    domain_code TEXT NOT NULL,
    domain TEXT NOT NULL,
    skill_code TEXT NOT NULL,
    skill TEXT NOT NULL,
    difficulty TEXT NOT NULL,
    question_type TEXT NOT NULL,
    answer_source TEXT NOT NULL,
    stimulus TEXT NOT NULL,
    stem TEXT NOT NULL,
    answer_options TEXT NOT NULL,
    correct_answers TEXT NOT NULL,
    explanation TEXT NOT NULL,
    figures TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    first_seen_run INTEGER NOT NULL REFERENCES sync_runs(id),
    last_seen_run INTEGER NOT NULL REFERENCES sync_runs(id),
    is_listed INTEGER NOT NULL CHECK (is_listed IN (0, 1))
);
CREATE INDEX IF NOT EXISTS questions_external ON questions(external_id);
CREATE INDEX IF NOT EXISTS questions_browse
    ON questions(section, domain, skill, difficulty, is_listed);
CREATE TABLE IF NOT EXISTS release_evidence (
    question_pk INTEGER NOT NULL REFERENCES questions(id),
    period TEXT NOT NULL CHECK (period IN ('Pre-July 2026', 'July 30, 2026')),
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    match_method TEXT NOT NULL,
    PRIMARY KEY (question_pk, period, source, source_id)
);
"""
QUESTION_BANK_VIEW = """
CREATE VIEW question_bank AS
SELECT q.*,
  CASE
    WHEN EXISTS (SELECT 1 FROM release_evidence e
                 WHERE e.question_pk = q.id AND e.match_method GLOB 'mr_thomas:*')
      THEN 'Mr. Thomas release'
    WHEN EXISTS (SELECT 1 FROM release_evidence e
                 WHERE e.question_pk = q.id AND e.period = 'Pre-July 2026'
                   AND e.match_method <> 'catalog_created_at')
      THEN 'Pre-July 2026'
    WHEN EXISTS (SELECT 1 FROM release_evidence e
                 WHERE e.question_pk = q.id AND e.period = 'July 30, 2026')
      THEN 'July 30, 2026'
    WHEN EXISTS (SELECT 1 FROM release_evidence e
                 WHERE e.question_pk = q.id AND e.match_method = 'catalog_created_at')
      THEN 'Pre-July 2026 (catalog date)'
    WHEN q.first_seen_run = (SELECT min(id) FROM sync_runs)
      THEN 'Unknown'
    ELSE 'Observed after baseline'
  END AS release_period
FROM questions q;
"""


def record_change(database, changed_at=None):
    database.execute("""
        INSERT INTO bank_change (id, changed_at) VALUES (1, ?)
        ON CONFLICT(id) DO UPDATE SET changed_at = max(changed_at, excluded.changed_at)
    """, (changed_at or datetime.now(timezone.utc).isoformat(),))


@contextmanager
def connect(path, *, read_only=False):
    path = Path(path).resolve()
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(
        path.as_uri() + ("?mode=ro" if read_only else ""),
        uri=True,
        timeout=30,
    )
    database.row_factory = sqlite3.Row
    try:
        database.execute("PRAGMA foreign_keys = ON")
        if not read_only:
            database.execute("PRAGMA journal_mode = WAL")
            database.executescript(SCHEMA)
            if database.execute("PRAGMA user_version").fetchone()[0] == 0:
                database.executescript(
                    "BEGIN IMMEDIATE; DROP VIEW IF EXISTS question_bank; "
                    + QUESTION_BANK_VIEW
                    + "PRAGMA user_version = 1; COMMIT;"
                )
        with database:
            yield database
    finally:
        database.close()
