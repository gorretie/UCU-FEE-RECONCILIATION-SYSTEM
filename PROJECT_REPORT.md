# Student Tuition Reconciliation and Learning Access Monitoring System

**Course:** CSC8101: OOP with Python  
**Institution:** Uganda Christian University  
**Author:** Gorretie Namubiru  
**Repository:** [Add the GitHub repository URL before submission]

## Abstract

This prototype reconciles assessed tuition and confirmed tuition payments with Moodle account snapshots. It identifies students who meet tuition milestones but whose learning access is still disabled, flags missing or inconsistent data, and simulates access restoration with an audit event. It uses fictional records and a local SQLite database. This project deliberately handles tuition only: other fees, accommodation, and late penalties do not affect tuition balances or eligibility. It does not replace Alpha or Moodle or make real account changes.

## 1. Problem and motivation

The project proposal describes separate financial and learning-management records and the administrative delay that can occur when a student's payment is recorded but access is not restored automatically [1]. A reliable reconciliation process should match records using student identifiers, distinguish confirmed payments from unverified transactions, surface incomplete or inconsistent records, and provide an auditable activation recommendation. The project combines an explicit business-rule model, structured records, data processing, and an interactive dashboard.

## 2. Scope and requirements

The deliverable is a functional Python prototype, local SQLite database, Streamlit dashboard, tuition-only synthetic dataset, unit tests, GitHub Actions workflow, and Jupyter analysis notebook. It uses the supplied cutoff dates and tuition percentages without changing the university's rules. The exam guidelines require modular OOP implementation, functional software, system design, documentation, tests, and an interactive interface [2].

### 2.1 Tuition milestones applied by the prototype

| Deadline | Milestone | Tuition-only requirement |
|---|---|---|
| 14 August 2026 | Prompt payment | At least 45% of assessed tuition |
| 11 September 2026 | Registration | At least 45% of assessed tuition |
| 9 October 2026 | Midterm | At least 75% of assessed tuition |
| 23 October 2026 | Full tuition | 100% of assessed tuition |
| 24 October 2026 onward | Late period | 100% of assessed tuition |

These are cutoffs, not expiring windows. A reached requirement remains due and later thresholds accumulate. Late tuition payments can satisfy the requirements reached by the evaluation date, including the final 100% threshold after October 31. The source screenshot includes requirements outside this project's selected scope; the prototype excludes other fees, accommodation, and late penalties rather than changing those university rules. Dates before August 14, 2026, before the first cutoff, require manual review.

## 3. System design

### 3.1 Architecture

The Streamlit entry point (`app.py`) calls a reconciliation service. The service obtains typed student, tuition assessment, tuition payment, and account records from the SQLite repository. Pandas merges and aggregates snapshots; NumPy supports descriptive payment-coverage analysis. The rule set evaluates every tuition cutoff reached by the selected date, so missed thresholds remain due and later milestones accumulate. Eligible disabled accounts are simulated as activated by the service through the repository, which updates the account and appends the audit event in one transaction.

```text
Streamlit UI ──> FeeReconciliationService ──> EligibilityRuleSet
                         │                           └── TuitionMilestoneRule
                         ├──> SQLiteRepository ──> SQLite tables and audit log
                         └──> ReconciliationAnalytics ──> Pandas / NumPy summaries
```

### 3.2 Object-oriented design

- **Encapsulation:** immutable domain dataclasses validate student, assessment, payment, and Moodle account fields at construction. Repository connection and SQL operations are private implementation details.
- **Abstraction:** `EligibilityRule` defines the common cutoff applicability and evaluation contract.
- **Inheritance and polymorphism:** `TuitionMilestoneRule` implements the abstract contract; `EligibilityRuleSet` evaluates rules through that interface.
- **Composition and separation of concerns:** the reconciliation service composes a repository and rule set; analytics, persistence, interface, and business rules are separate modules.
- **Exception handling:** invalid imported or manually entered data raises explicit validation errors. CSV imports validate the full bundle and commit atomically.

## 4. Implementation

### 4.1 Reconciliation flow

1. Load student, tuition assessment, tuition payment, and Moodle account snapshots.
2. Match records using the student identifier and retain unmatched source records for data-quality reporting.
3. Aggregate only confirmed tuition payments dated on or before the selected evaluation date.
4. Evaluate all tuition cutoffs reached by the date and compare the payment total against the assessed tuition thresholds.
5. If the student is eligible and has an existing mapped disabled account, simulate activation and write an audit event.
6. Preserve active accounts regardless of current eligibility; the prototype never automatically revokes access.
7. Mark missing tuition assessments, missing account mappings, and dates before the first supplied cutoff for review.

### 4.2 Data storage and interface

SQLite stores students, tuition assessments, tuition payments, Moodle account snapshots, and audit events. Legacy non-tuition database rows are retained but filtered from the tuition workflow. The dashboard supports filtering, outcome summaries, data-quality findings, record entry, transactional four-file CSV imports, and reconciliation/data-quality exports. Tuition values in the interface are displayed in UGX.

### 4.3 Analysis and demonstration case

The Jupyter notebook loads the same fictional data and uses the same policy and reconciliation classes as the application. It reports outcome counts, unmatched and incomplete records, and descriptive tuition-payment coverage. It verifies that the final tuition cutoff remains in force after October 31. The Frank Ouma case (`UCU-2026-006`) demonstrates a cleared tuition balance alongside initially disabled access and the resulting audited activation. The sample demonstrates system behavior; it is not representative evidence of actual UCU payment or access performance.

## 5. Verification

The `tests/` directory uses Python's `unittest` framework to exercise cumulative tuition cutoffs, late payment, missing data, confirmed versus pending payments, exclusion of legacy non-tuition rows, activation and non-revocation behavior, unmatched records, and atomic database imports. GitHub Actions runs the unit test suite on each push and pull request. The notebook and UI use the same application modules as the tested business logic.

**Execution record:** The tuition-only version passed all 20 unit tests. Streamlit `AppTest` rendered the dashboard and all five secondary pages without exceptions. All seven notebook code cells executed; the Frank Ouma check showed a zero tuition balance and eligibility after the final cutoff. `pip check` reported no broken requirements.

## 6. Limitations and future work

This is a prototype with fictional data, local persistence, and a simulated system actor. It does not provide authentication, role-based authorization, real-time source synchronization, or production integrations. Before operational deployment, UCU would need to approve access, provide supported Alpha and Moodle interfaces and authoritative data definitions, validate policy interpretation with the responsible office, and review privacy, security, audit-retention, and access-control requirements.

## References

[1] G. Namubiru, “Design and Development of Student Fee Reconciliation and Learning Access Monitoring System at Uganda Christian University,” project proposal supplied with the project brief, 2026.

[2] Uganda Christian University, Department of Computing & Technology, “CSC8101: OOP with Python Project Guidelines,” Exams for Advent 2026 Semester, course project guidelines supplied with the project brief, 2026.
