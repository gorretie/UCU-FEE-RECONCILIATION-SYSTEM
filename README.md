# UCU Tuition Reconciliation and Learning Access Monitoring

A Python OOP prototype that reconciles assessed tuition and confirmed tuition payments with Moodle account snapshots. It identifies students who meet the configured university tuition milestones and simulates access activation when their tuition is sufficient.

> **Scope and policy boundary:** This project intentionally handles tuition only. Other fees, accommodation, and late penalties are outside this prototype and do not affect tuition balances or eligibility. University deadlines and tuition thresholds are not modified. Records are fictional; the app does not connect to Alpha or Moodle or make real account changes.

## Features

- Tuition-only reconciliation and cumulative checks against the supplied 2026 cutoff dates.
- Confirmed tuition payments count; pending and rejected payments do not. Payments dated after the selected evaluation date are excluded.
- Late tuition payments can satisfy milestones that remain in force after their cutoffs.
- A disabled Moodle account is activated in simulation only after eligibility is verified; each activation is written to the SQLite audit log.
- Student and reconciliation tables show tuition due, paid, balance, and credit in UGX.
- Missing tuition assessments, unmapped accounts, unmatched IDs, and dates before the first cutoff are flagged for review.
- Streamlit dashboard, record management, atomic CSV imports, exports, analytics, unit tests, GitHub Actions workflow, and Jupyter analysis notebook.

## Tuition milestones

| Deadline | Milestone | Tuition-only requirement |
|---|---|---|
| 14 Aug 2026 | Prompt payment | At least 45% of assessed tuition |
| 11 Sep 2026 | Registration | At least 45% of assessed tuition |
| 9 Oct 2026 | Midterm | At least 75% of assessed tuition |
| 23 Oct 2026 | Full tuition | 100% of assessed tuition |
| 24 Oct 2026 onward | Late period | 100% of assessed tuition |

Dates are cutoffs rather than expiring windows: reached requirements remain due and later thresholds accumulate. Before August 14, 2026, no supplied cutoff has been reached and eligibility requires manual review.

## Requirements and run

Python 3.11 or newer is recommended. Dependencies for the dashboard, interactive charts, and notebook kernel are listed in `requirements.txt`; unit tests use Python's standard `unittest` library.

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

By default, the application creates or uses `database.db` in the project root and seeds fictional demonstration records only when there are no students. Set `UCU_DATABASE_PATH` to use a separate database:

```powershell
$env:UCU_DATABASE_PATH = "C:\data\ucufeereconciliation.sqlite3"
python -m streamlit run app.py
```

The demo data includes **Frank Ouma (`UCU-2026-006`)** with fully paid tuition and a disabled Moodle account, demonstrating tuition reconciliation and simulated activation. It also includes eight clearly labeled `UCU-TEST-*` cases with 0%, 20%, 45%, 50%, 75%, 90%, and 100% tuition coverage, split payments, and a pending payment. Existing databases are not overwritten by demo seeding; use **Manage records → Add synthetic payment test cases** to add these cases to an existing database. The button is safe to repeat and does not change existing records. Legacy non-tuition database rows are retained but ignored by tuition calculations.

To try a payment scenario, open **Manage records → Tuition payments**, choose a `UCU-TEST-*` student, record a new payment with a unique reference, and keep the status `confirmed` for it to count. Change the evaluation date in the sidebar to compare the 45%, 75%, and full-tuition milestones. At the final cutoff, an eligible disabled test account is activated automatically after reconciliation.

## Unit tests

```powershell
python -m unittest discover -s tests -v
```

GitHub Actions runs this suite on pushes and pull requests.

## Jupyter analysis

Open `fee_reconciliation_analysis.ipynb` in Jupyter and run its cells from the project root. It creates a temporary SQLite database, analyses fictional tuition-only cases, and demonstrates that the final tuition cutoff remains in force after October 31. It does not modify the app's `database.db`.

## CSV import contract

On the **Import data** page, upload all four files together. Headers are case-sensitive; additional columns are ignored. Each import replaces the tuition source snapshots only after validation, in a single SQLite transaction. If a record is invalid, the previous snapshot remains intact. If an older assessment or payment CSV includes a `category` column, only rows marked `tuition` are imported; other supported legacy categories are excluded. Without a `category` column, all amounts in those files are treated as tuition.

| File | Required columns | Values |
|---|---|---|
| `students.csv` | `student_id, full_name` | Non-empty identifiers and names |
| `assessments.csv` | `student_id, amount_due` | Nonnegative amount |
| `payments.csv` | `reference, student_id, amount, status, paid_at` | Positive amount; status `confirmed`, `pending`, or `rejected` |
| `moodle_accounts.csv` | `student_id, username, enabled` | enabled: `true/false`, `yes/no`, or `1/0` |

Example:

```csv
student_id,full_name
UCU-2026-001,Example Student
```

```csv
student_id,amount_due
UCU-2026-001,600000
```

```csv
reference,student_id,amount,status,paid_at
SYNTH-001,UCU-2026-001,300000,confirmed,2026-10-20T10:00:00+00:00
```

```csv
student_id,username,enabled
UCU-2026-001,ucu-2026-001,false
```

## Architecture

```text
app.py (Streamlit UI)
  ├── src/domain.py        Immutable, validated tuition/student/account objects
  ├── src/policies.py      Abstract eligibility rules and dated tuition milestones
  ├── src/storage.py       SQLite repository, transactional imports, audit log
  ├── src/service.py       Tuition reconciliation, data quality, activation simulation
  ├── src/analytics.py     Pandas/NumPy summaries and turnaround metrics
  └── src/sample_data.py   Fictional tuition-only scenarios

tests/                     unittest suite
.github/workflows/tests.yml GitHub Actions tests
fee_reconciliation_analysis.ipynb  Reproducible analysis
```

The design uses immutable domain objects to enforce record invariants; an abstract `EligibilityRule` with polymorphic tuition milestone implementations; and separate repository, reconciliation service, analytics, and interface components.

## Limitations

This is a local prototype, not an approved university system. SQLite records and the system audit actor are for demonstration. Authentication, role-based authorization, secure production data handling, concurrent external synchronization, and supported Alpha/Moodle APIs are outside scope. Do not load real student records into an unapproved deployment.
