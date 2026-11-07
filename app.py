"""Root Streamlit entry point for the UCU fee reconciliation prototype."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from src.analytics import ReconciliationAnalytics
from src.domain import FeeAssessment, MoodleAccount, Payment, PaymentStatus, Student
from src.policies import ucu_2026_rules
from src.sample_data import add_manual_test_scenarios, load_demo_data
from src.service import FeeReconciliationService
from src.storage import DataValidationError, SQLiteRepository


ROOT = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.environ.get("UCU_DATABASE_PATH", ROOT / "database.db"))


@st.cache_resource
def get_repository(database_path: str) -> SQLiteRepository:
    repository = SQLiteRepository(database_path)
    load_demo_data(repository)
    return repository


st.set_page_config(
    page_title="UCU Fee Reconciliation",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .block-container {padding-top: 1.6rem; padding-bottom: 2rem;}
    [data-testid="stMetric"] {
        background: #f3f6fb; border: 1px solid #e3eaf5;
        padding: 14px 16px; border-radius: 12px;
    }
    .hero {
        padding: 1.3rem 1.5rem; border-radius: 16px; color: white;
        background: linear-gradient(110deg, #102b57 0%, #1d5b9d 72%, #2c83b6 100%);
        margin-bottom: 1.1rem;
    }
    .hero h1 {margin: 0 0 .35rem 0; color: white;}
    .hero p {margin: 0; opacity: .9;}
    </style>
    """,
    unsafe_allow_html=True,
)

repository = get_repository(str(DATABASE_PATH))
service = FeeReconciliationService(repository, ucu_2026_rules())
analytics = ReconciliationAnalytics(repository)

st.sidebar.title("UCU | Fee & Learning Access")
st.sidebar.caption("Synthetic-data reconciliation prototype")
page = st.sidebar.radio(
    "Workspace",
    ("Dashboard", "Reconciliation", "Manage records", "Import data", "Audit log", "About"),
)
as_of = st.sidebar.date_input(
    "Evaluate university rules as of",
    value=date.today(),
    min_value=date(2026, 1, 1),
    max_value=date(2027, 12, 31),
)

if not any(rule.applies_on(as_of) for rule in service.rules.rules):
    st.sidebar.warning(
        "This date is before the first supplied fee cutoff. Automatic activation is paused "
        "and cases are marked for manual review."
    )

cases = service.reconcile(as_of)


def show_case_table(frame: pd.DataFrame) -> None:
    columns = [
        "student_id",
        "full_name",
        "tuition_due_ugx",
        "tuition_paid_ugx",
        "tuition_balance_ugx",
        "tuition_credit_ugx",
        "tuition_paid_percent",
        "rule_name",
        "eligible",
        "access_enabled",
        "case_status",
        "explanation",
    ]
    st.dataframe(
        frame[columns],
        width="stretch",
        hide_index=True,
        column_config={
            "tuition_due_ugx": st.column_config.NumberColumn(
                "Tuition assessed",
                format="UGX %,.0f",
                help="Assessed tuition recorded for this student.",
            ),
            "tuition_paid_ugx": st.column_config.NumberColumn(
                "Tuition paid",
                format="UGX %,.0f",
                help="Confirmed payments dated on or before the selected evaluation date.",
            ),
            "tuition_balance_ugx": st.column_config.NumberColumn(
                "Tuition balance",
                format="UGX %,.0f",
                help="Assessed tuition less confirmed tuition payments, with a floor of zero.",
            ),
            "tuition_credit_ugx": st.column_config.NumberColumn(
                "Tuition credit",
                format="UGX %,.0f",
                help="Tuition payments above the assessed tuition amount.",
            ),
            "tuition_paid_percent": st.column_config.NumberColumn(
                "Tuition paid",
                format="%.1f%%",
            ),
            "eligible": st.column_config.TextColumn("Rule eligible"),
            "access_enabled": st.column_config.CheckboxColumn("Moodle enabled"),
        },
    )


def dashboard() -> None:
    st.markdown(
        """
        <div class="hero">
          <h1>Fee reconciliation & learning access</h1>
          <p>Match confirmed payments to the documented fee milestones and flag access discrepancies.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption(
        "This prototype simulates account activation only. It does not connect to Alpha or Moodle "
        "and does not change UCU fee rules."
    )
    metrics = analytics.summary(cases)
    columns = st.columns(5)
    columns[0].metric("Students", metrics["students"])
    columns[1].metric("Eligible", metrics["eligible"])
    columns[2].metric("Moodle disabled", metrics["access_disabled"])
    columns[3].metric("Activated this run", metrics["activated"])
    columns[4].metric("Manual review", metrics["manual_review"])

    left, right = st.columns((1.1, 1))
    with left:
        st.subheader("Reconciliation outcomes")
        status_counts = (
            cases["case_status"]
            .value_counts()
            .rename_axis("Outcome")
            .reset_index(name="Students")
        )
        if not status_counts.empty:
            st.plotly_chart(
                px.bar(
                    status_counts,
                    x="Students",
                    y="Outcome",
                    orientation="h",
                    color="Outcome",
                    title="Students by reconciliation outcome",
                ),
                width="stretch",
            )
        else:
            st.info("No student records are available.")
    with right:
        st.subheader("Tuition payment coverage")
        if not cases.empty:
            st.plotly_chart(
                px.bar(
                    cases,
                    x="tuition_paid_percent",
                    y="student_id",
                    orientation="h",
                    labels={
                        "student_id": "Student",
                        "tuition_paid_percent": "Tuition paid (%)",
                    },
                    title="Confirmed payment coverage",
                ),
                width="stretch",
            )
            st.caption(
                f"Mean confirmed payment coverage: "
                f"{metrics['mean_tuition_paid_percent']:.1f}% "
                "(descriptive only; eligibility is determined by the selected rule)."
            )

    st.subheader("Cases requiring attention")
    attention = cases[cases["case_status"].isin(
        ["Manual review", "Missing Moodle account", "Access disabled - tuition below rule"]
    )]
    if attention.empty:
        st.success("No unresolved cases for this rule date.")
    else:
        show_case_table(attention)

    quality = service.data_quality()
    if not quality.empty:
        with st.expander(f"Data-quality findings ({len(quality)})"):
            st.dataframe(quality, width="stretch", hide_index=True)

    turnaround = analytics.turnaround_summary()
    if turnaround["activations_with_payment_history"]:
        st.caption(
            f"Recorded activation turnaround: mean {turnaround['mean_hours']:.1f} hours; "
            f"median {turnaround['median_hours']:.1f} hours across "
            f"{turnaround['activations_with_payment_history']} activation(s)."
        )


if page == "Dashboard":
    dashboard()

elif page == "Reconciliation":
    st.title("Reconciliation cases")
    st.caption(
        f"Rule date: {as_of.isoformat()}. Eligible disabled accounts are simulated as activated "
        "and written to the audit log; accounts are never automatically disabled."
    )
    query = st.text_input("Filter by student ID or name")
    view = cases
    if query.strip():
        mask = (
            view["student_id"].str.contains(query.strip(), case=False, na=False)
            | view["full_name"].str.contains(query.strip(), case=False, na=False)
        )
        view = view[mask]
    show_case_table(view)
    st.download_button(
        "Download reconciliation CSV",
        data=view.to_csv(index=False).encode("utf-8"),
        file_name=f"reconciliation-{as_of.isoformat()}.csv",
        mime="text/csv",
    )
    quality = service.data_quality()
    st.subheader("Unmatched and incomplete records")
    if quality.empty:
        st.success("No data-quality issues were found.")
    else:
        st.dataframe(quality, width="stretch", hide_index=True)
        st.download_button(
            "Download data-quality CSV",
            data=quality.to_csv(index=False).encode("utf-8"),
            file_name="data-quality-findings.csv",
            mime="text/csv",
        )

elif page == "Manage records":
    st.title("Manage prototype records")
    st.info("For the prototype, enter synthetic data only. Payment status must be confirmed before it counts.")
    st.caption(
        "Use the test cases to explore 0%, 20%, 45%, 50%, 75%, and 90% paid, "
        "a split payment, a pending payment, and a fully paid account. Change the "
        "evaluation date in the sidebar to compare milestone cutoffs."
    )
    if st.button("Add synthetic payment test cases"):
        added_count = add_manual_test_scenarios(repository)
        if added_count:
            st.success(f"Added {added_count} synthetic test students.")
        else:
            st.info("The synthetic test students are already present; no records were changed.")
        st.rerun()

    students = repository.list_students()
    with st.expander("Add or update a student", expanded=not students):
        with st.form("student_form"):
            student_id = st.text_input("Student ID")
            full_name = st.text_input("Full name")
            saved_student = st.form_submit_button("Save student")
        if saved_student:
            try:
                repository.upsert_student(Student(student_id, full_name))
            except DataValidationError as error:
                st.error(str(error))
            else:
                st.success("Student record saved.")
                st.rerun()

    students = repository.list_students()
    if not students:
        st.warning("Add a student before adding fees, payments, or Moodle account mappings.")
    else:
        student_ids = [student.student_id for student in students]
        accounts_by_id = {account.student_id: account for account in repository.list_accounts()}
        fee_tab, payment_tab, account_tab = st.tabs(
            ["Tuition assessment", "Tuition payments", "Moodle account mapping"]
        )

        with fee_tab:
            with st.form("assessment_form"):
                assessment_student = st.selectbox("Student", student_ids, key="assessment_student")
                amount_due = st.number_input("Assessed tuition (UGX)", min_value=0.0, step=10000.0)
                save_assessment = st.form_submit_button("Save tuition assessment")
            if save_assessment:
                try:
                    repository.set_assessment(
                        FeeAssessment(
                            assessment_student,
                            amount_due,
                        )
                    )
                except DataValidationError as error:
                    st.error(str(error))
                else:
                    st.success("Tuition assessment saved.")
                    st.rerun()

        with payment_tab:
            with st.form("payment_form"):
                payment_student = st.selectbox("Student", student_ids, key="payment_student")
                payment_reference = st.text_input("Unique payment reference")
                payment_amount = st.number_input("Tuition payment (UGX)", min_value=0.0, step=10000.0)
                payment_status = st.selectbox(
                    "Payment status",
                    [status.value for status in PaymentStatus],
                )
                payment_date = st.date_input("Payment date", value=date.today(), key="payment_date")
                save_payment = st.form_submit_button("Record payment")
            if save_payment:
                try:
                    repository.record_payment(
                        Payment(
                            payment_reference,
                            payment_student,
                            payment_amount,
                            PaymentStatus(payment_status),
                            payment_date.isoformat(),
                        )
                    )
                    repository.add_audit_event(
                        "payment_recorded",
                        {"reference": payment_reference, "status": payment_status},
                        payment_student,
                    )
                except (DataValidationError, ValueError) as error:
                    st.error(str(error))
                else:
                    st.success("Payment record saved.")
                    st.rerun()

        with account_tab:
            existing_ids = set(accounts_by_id)
            missing_ids = [student_id for student_id in student_ids if student_id not in existing_ids]
            if not missing_ids:
                st.info("Every student already has an account mapping.")
            else:
                with st.form("account_form"):
                    account_student = st.selectbox("Student without an account", missing_ids)
                    username = st.text_input("Moodle username")
                    save_account = st.form_submit_button("Add disabled account mapping")
                if save_account:
                    try:
                        repository.upsert_account(MoodleAccount(account_student, username, False))
                    except DataValidationError as error:
                        st.error(str(error))
                    else:
                        st.success("Disabled Moodle account mapping saved.")
                        st.rerun()

        st.subheader("Current student records")
        cases_by_student = {
            row["student_id"]: row for row in cases.to_dict(orient="records")
        }
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "student_id": student.student_id,
                        "full_name": student.full_name,
                        "tuition_due_ugx": cases_by_student[student.student_id][
                            "tuition_due_ugx"
                        ],
                        "tuition_paid_ugx": cases_by_student[student.student_id][
                            "tuition_paid_ugx"
                        ],
                        "tuition_balance_ugx": cases_by_student[student.student_id][
                            "tuition_balance_ugx"
                        ],
                        "tuition_credit_ugx": cases_by_student[student.student_id][
                            "tuition_credit_ugx"
                        ],
                        "moodle_username": accounts_by_id.get(student.student_id).username
                        if student.student_id in accounts_by_id
                        else None,
                        "moodle_enabled": accounts_by_id.get(student.student_id).enabled
                        if student.student_id in accounts_by_id
                        else None,
                    }
                    for student in students
                ]
            ),
            width="stretch",
            hide_index=True,
            column_config={
                "tuition_due_ugx": st.column_config.NumberColumn(
                    "Tuition assessed", format="UGX %,.0f"
                ),
                "tuition_paid_ugx": st.column_config.NumberColumn(
                    "Tuition paid", format="UGX %,.0f"
                ),
                "tuition_balance_ugx": st.column_config.NumberColumn(
                    "Tuition balance", format="UGX %,.0f"
                ),
                "tuition_credit_ugx": st.column_config.NumberColumn(
                    "Tuition credit", format="UGX %,.0f"
                ),
            },
        )

elif page == "Import data":
    st.title("Import source snapshots")
    st.markdown(
        "Upload all four UTF-8 CSV files together. Records are validated and saved in one "
        "transaction; unmatched IDs are retained so the dashboard can flag them."
    )
    st.code(
        "students.csv: student_id, full_name\n"
        "assessments.csv: student_id, amount_due\n"
        "payments.csv: reference, student_id, amount, status, paid_at\n"
        "moodle_accounts.csv: student_id, username, enabled\n\n"
        "Payment statuses: confirmed, pending, rejected",
        language="text",
    )
    uploaded: dict[str, object] = {}
    filenames = {
        "students": "students.csv",
        "assessments": "assessments.csv",
        "payments": "payments.csv",
        "accounts": "moodle_accounts.csv",
    }
    for key, filename in filenames.items():
        uploaded[key] = st.file_uploader(filename, type=["csv"], key=f"upload_{key}")
    if st.button("Validate and import all files", type="primary"):
        missing = [filenames[key] for key, file in uploaded.items() if file is None]
        if missing:
            st.error(f"Select every required file: {', '.join(missing)}.")
        else:
            try:
                datasets = {
                    key: pd.read_csv(file)
                    for key, file in uploaded.items()
                }
                count = repository.import_bundle(datasets)
            except (DataValidationError, pd.errors.ParserError, UnicodeDecodeError, ValueError) as error:
                st.error(f"Import failed; no records were applied. {error}")
            else:
                st.success(f"Imported {count} records. Reconciliation will run automatically.")
                st.rerun()

elif page == "Audit log":
    st.title("Audit log")
    st.caption("Simulated access activations and data-management events are retained in SQLite.")
    events = pd.DataFrame(repository.list_audit_events())
    if events.empty:
        st.info("No audit events have been recorded.")
    else:
        st.dataframe(events, width="stretch", hide_index=True)

else:
    st.title("About this prototype")
    st.markdown(
        """
        **Purpose.** Reconcile synthetic student assessments and confirmed payments with
        Moodle account snapshots, highlight discrepancies, and simulate access restoration
        when the applicable university requirement is verified.

        **Policy boundary.** The rules are based on the supplied 2026 deadline screenshot,
        but this prototype deliberately applies them to tuition only. Other fees,
        accommodation, and late penalties do not affect tuition balances or eligibility.
        Tuition cutoffs remain applicable after their due dates, and later thresholds
        accumulate. The system does not set tuition amounts, change deadlines, or revoke
        access. Dates before the first cutoff and incomplete tuition assessments require
        review.

        **Integration boundary.** This is a local SQLite prototype. It does not access
        Alpha or Moodle. Any production connection or account change requires university
        authorization and approved interfaces.
        """
    )
    st.subheader("Tuition-only milestones applied")
    st.table(
        pd.DataFrame(
            [
                ["14-Aug-2026", "Prompt payment", "At least 45% of assessed tuition"],
                ["11-Sep-2026", "Registration", "At least 45% of assessed tuition"],
                ["09-Oct-2026", "Midterm", "At least 75% of assessed tuition"],
                ["23-Oct-2026", "Full tuition", "100% of assessed tuition"],
                ["24-Oct-2026 onward", "Late period", "100% of assessed tuition"],
            ],
            columns=["Deadline", "Milestone", "Tuition-only requirement applied"],
        )
    )
    st.caption(
        "This prototype intentionally considers tuition only. Other fees, accommodation, "
        "and late penalties do not affect tuition balance or Moodle eligibility. Dates "
        "are cutoffs: thresholds remain due after their dates, and later tuition "
        "thresholds accumulate. Late tuition payments can satisfy the requirements and "
        "enable simulated activation."
    )
