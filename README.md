# SAT Question Bank

Use Python 3.10 or newer. The importer and web app use the Python standard library. Run these commands from the extracted `sat-bank` folder.

```sh
gzip -dc data/seed.sqlite3.gz > data/sat.sqlite3  # Run once when starting with a fresh checkout.
python3 import_bank.py
python3 backfill.py --catalog-pre-july
python3 serve.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The importer and local server each default to `data/sat.sqlite3`. Pass `--db PATH` to use a different file. You can start the server immediately after unpacking the seed, and refresh the bank separately.

## GitHub Pages

Push the contents of this directory to the `main` branch of a public GitHub repository named `sat-question-bank`. In the repository's **Settings → Pages**, set **Source** to **GitHub Actions**. Then open **Actions → Refresh and publish SAT Question Bank → Run workflow**. Leave **Fetch the latest questions from College Board before publishing** unchecked to publish the bundled snapshot to start with.

The workflow publishes at `https://<owner>.github.io/sat-question-bank/`. It also refreshes and republishes the bank daily at 10:17 UTC. After the second successful refresh, the `sat-bank-state` GitHub release keeps both the latest and previous complete SQLite snapshots. A normal push republishes the current snapshot. `data/last-refresh.json` records the latest successful daily import. The **Actions** tab shows the last run and the deployment URL.

To test the Pages build locally, export to an empty directory and serve the static files:

```sh
python3 export_site.py --db data/sat.sqlite3 --output _site
python3 -m http.server 8000 --bind 127.0.0.1 --directory _site
```

The export includes the HTML, JavaScript, CSS, and JSON used by the browser. SQLite is used during the build and refresh.

## Import and update

Run `python3 import_bank.py` again whenever you want to refresh the bank locally. A first fetch takes several minutes. Later runs reuse question bodies whose catalog metadata is unchanged. Use `--full` to fetch every body again, including edits the catalog metadata may omit. Use `--json` for a machine-readable report with the IDs added, updated, removed, and returned. Questions removed from the College Board catalog remain in SQLite.

The importer fetches Math and Reading & Writing and checks that the listings agree before and after the detail fetch. It commits only a complete run. First and last observed timestamps are in UTC. The main identifier is College Board's printed question ID. External IDs, internal IDs, and paper-test item numbers are retained separately. A small number of printed IDs share the same API detail record.

Both source formats are supported, including legacy Math items with image-based equations. For legacy items whose answer appears in the explanation, the importer extracts answer choices and numeric values when it can identify them. The answer key always includes the original explanation. The schema preserves the original metadata and detail payloads alongside the normalized fields. The request shapes were adapted from [Martin Svítek’s `refresh_questions.py`](https://github.com/Svitek0/moduledrill/blob/main/refresh_questions.py). See `LICENSE`.

## Historical backfill

- `python3 backfill.py` applies the included July 30 manifest and [public August 2025 archive](https://www.reddit.com/r/Sat/comments/1bwg9x1/sat_question_bank_pdfs/). The archive records its supporting PDF URL for each printed ID and confirms its matches as **Pre-July 2026**. The July batch’s 270 IDs came from the 72 question PDFs linked in the July directory, cross-checked against the original CSV manifests. The grouped Math manifest lacked one printed ID, which the matching PDF supplies.
- `python3 backfill.py --catalog-pre-july` also labels questions with a College Board catalog creation date before July 30, 2026 as **Pre-July 2026 (catalog date)**. This is provisional historical evidence. Existing questions with no dated source match remain **Unknown**. Future arrivals are labeled **Observed after baseline** with their first observed date.
- `python3 backfill.py` also applies the included Mr. Thomas manifest. It records printed IDs and their source PDF links from the 85 uploaded packets. Choose **Thomas’ Bank** in a skill’s question picker to see these matches. A newer export can be applied with `python3 backfill.py --thomas /path/to/thomas.csv`. The command also accepts question PDFs, folders of PDFs, and DOCX directories with accessible linked PDFs. PDF extraction requires Poppler's `pdftotext`. `--thomas-online` uses the original Google Doc URL and requires the document and PDFs to be publicly downloadable.
- An ID CSV may provide `question_id`, `external_id`, `uid`, or `ibn` plus an optional `source_url`. Grouped CSVs with a semicolon-delimited `question_ids` column are also supported. Each run reports unmatched and conflicting IDs. Confirmed evidence from Mr. Thomas takes precedence over the July batch and provisional catalog dates. The underlying evidence and live observation timestamps remain in SQLite.

## Browse and print

Choose a subject to browse and bookmark it. **Filter by Release** defaults to July 30 and August 2026. Hover over a section, domain, skill, or difficulty to see the latest question date. Choose **Easy**, **Medium**, or **Hard**, then **Select** to preview or **Add to worksheet** to collect questions across categories. Clearing the release filter lets you choose banks and months for each category individually.

Dates use College Board catalog creation dates, with the first observed date as the fallback. **Custom date range** switches the selection to all questions from the start date through the end date. You can then check banks or months to add to that range. Overlapping selections count each question once.

In preview, uncheck questions to omit them from printing. **Details** shows catalog timestamps, first and last seen, question IDs, and release evidence. Use **Include title** to add an editable print title. **Switch to answer key** preserves the selection in individual category previews. **Print** opens the US Letter print dialog.
