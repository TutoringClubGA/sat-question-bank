"""Export the SAT bank to a directory that any static host can serve."""

import argparse
import json
from pathlib import Path
import re
import shutil
import tempfile

from bank import DEFAULT_DB, connect
from serve import WEB, directory_dates, directory_releases, question_data


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def export_site(db_path, output):
    output = output.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(f"Choose an empty export directory. {output} already contains files.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as staging:
        site = Path(staging) / "site"
        details = site / "data" / "question"
        details.mkdir(parents=True)
        with connect(db_path, read_only=True) as database:
            database.execute("BEGIN")
            releases = directory_releases(database)
            questions = releases["questions"]
            if not questions:
                raise ValueError("The database contains no listed questions.")
            write_json(site / "data" / "directory-releases.json", releases)
            write_json(site / "data" / "directory-dates.json", directory_dates(database))
            for row in questions:
                identifier = row["question_id"]
                if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
                    raise ValueError(f"Question ID cannot be used as a filename: {identifier!r}")
                write_json(details / f"{identifier}.json", question_data(database, identifier))
        for asset in ("app.js", "style.css"):
            shutil.copyfile(WEB / asset, site / asset)
        index = (WEB / "index.html").read_text(encoding="utf-8")
        static_head = (
            '<head>\n'
            '<meta name="sat-bank-static" content="true">\n'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'self\'; '
            "img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; object-src 'none'; "
            'frame-src \'none\'; base-uri \'none\'; form-action \'none\'">'
        )
        (site / "index.html").write_text(index.replace("<head>", static_head, 1), encoding="utf-8")
        (site / ".nojekyll").touch()
        site.replace(output)
    return len(questions)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=Path("_site"))
    args = parser.parse_args()
    count = export_site(args.db, args.output)
    print(f"Exported {count:,} questions to {args.output.resolve()}")


if __name__ == "__main__":
    main()
