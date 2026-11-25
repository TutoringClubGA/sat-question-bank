# SAT Question Bank

Use Python 3.10 or newer. The importer and web app use the Python standard library. To set up and instance, you can run these commands from the extracted `sat-bank` folder

```sh
gzip -dc data/seed.sqlite3.gz > data/sat.sqlite3  # Run once when starting with a fresh checkout.
python3 import_bank.py
python3 backfill.py --catalog-pre-july
python3 serve.py
```

and open [http://127.0.0.1:8000](http://127.0.0.1:8000). 

The importer and local server each default to `data/sat.sqlite3`. Pass `--db PATH` to use a different file. You can start the server immediately after unpacking the seed, and refresh the bank separately.

## Import and update

Run `python3 import_bank.py` again whenever you want to refresh the bank locally. A first fetch takes several minutes. Later runs reuse question bodies whose catalog metadata is unchanged. Use `--full` to fetch every body again, including edits the catalog metadata may omit. Use `--json` for a machine-readable report with the IDs added, updated, removed, and returned. Questions removed from the College Board catalog remain in SQLite.

The importer fetches Math and Reading & Writing and checks that the listings agree before and after the detail fetch. It commits only a complete run. First and last observed timestamps are in UTC. The main identifier is College Board's printed question ID. External IDs, internal IDs, and paper-test item numbers are retained separately. A small number of printed IDs share the same API detail record.

Both source formats are supported, including legacy Math items with image-based equations. For legacy items whose answer appears in the explanation, the importer extracts answer choices and numeric values when it can identify them. The answer key always includes the original explanation. The schema preserves the original metadata and detail payloads alongside the normalized fields. The request shapes were adapted from [Martin Svítek’s `refresh_questions.py`](https://github.com/Svitek0/moduledrill/blob/main/refresh_questions.py). See `LICENSE`.
