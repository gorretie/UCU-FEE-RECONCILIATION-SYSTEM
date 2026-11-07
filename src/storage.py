"""SQLite persistence and transactional CSV snapshot imports."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Mapping

import pandas as pd

from .domain import FeeAssessment, MoodleAccount, Payment, PaymentStatus, Student
from .storage_exceptions import DataValidationError


_SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    student_id TEXT PRIMARY KEY,
    full_name TEXT NOT NULL,
    is_resident INTEGER NOT NULL CHECK (is_resident IN (0, 1))
);
CREATE TABLE IF NOT EXISTS assessments (
    student_id TEXT NOT NULL,
    category TEXT NOT NULL,
    amount_due REAL NOT NULL CHECK (amount_due >= 0),
    PRIMARY KEY (student_id, category)
);
CREATE TABLE IF NOT EXISTS payments (
    reference TEXT PRIMARY KEY,
    student_id TEXT NOT NULL,
    category TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount > 0),
    status TEXT NOT NULL CHECK (status IN ('confirmed', 'pending', 'rejected')),
    paid_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS moodle_accounts (
    student_id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT,
    event_type TEXT NOT NULL,
    details TEXT NOT NULL,
    happened_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payments_student ON payments(student_id);
CREATE INDEX IF NOT EXISTS idx_assessments_student ON assessments(student_id);
"""

_IMPORT_COLUMNS: dict[str, set[str]] = {
    "students": {"student_id", "full_name"},
    "assessments": {"student_id", "amount_due"},
    "payments": {"reference", "student_id", "amount", "status", "paid_at"},
    "accounts": {"student_id", "username", "enabled"},
}
_LEGACY_CATEGORIES = {"tuition", "other", "accommodation", "late_penalty"}


class SQLiteRepository:
    """Stores the prototype's synthetic/source snapshots and audit events."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(_SCHEMA)

    def is_empty(self) -> bool:
        return not self.list_students()

    def upsert_student(self, student: Student) -> None:
        with self._connection() as connection:
            self._upsert_student(connection, student)

    def _upsert_student(self, connection: sqlite3.Connection, student: Student) -> None:
        connection.execute(
            """INSERT INTO students (student_id, full_name, is_resident) VALUES (?, ?, ?)
            ON CONFLICT(student_id) DO UPDATE SET full_name = excluded.full_name""",
            (student.student_id, student.full_name, 0),
        )

    def set_assessment(self, assessment: FeeAssessment) -> None:
        with self._connection() as connection:
            self._set_assessment(connection, assessment)

    def _set_assessment(self, connection: sqlite3.Connection, assessment: FeeAssessment) -> None:
        connection.execute(
            """INSERT INTO assessments (student_id, category, amount_due) VALUES (?, ?, ?)
            ON CONFLICT(student_id, category) DO UPDATE SET amount_due = excluded.amount_due""",
            (assessment.student_id, "tuition", assessment.amount_due),
        )

    def record_payment(self, payment: Payment) -> None:
        try:
            with self._connection() as connection:
                self._record_payment(connection, payment)
        except sqlite3.IntegrityError as error:
            raise DataValidationError(
                "Payment could not be saved; its reference may already exist."
            ) from error

    def _record_payment(self, connection: sqlite3.Connection, payment: Payment) -> None:
        connection.execute(
            """INSERT INTO payments (reference, student_id, category, amount, status, paid_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (
                payment.reference,
                payment.student_id,
                "tuition",
                payment.amount,
                payment.status.value,
                payment.paid_at,
            ),
        )

    def upsert_account(self, account: MoodleAccount) -> None:
        now = _utc_now()
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO moodle_accounts (student_id, username, enabled, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(student_id) DO UPDATE SET
                    username = excluded.username, enabled = excluded.enabled,
                    updated_at = excluded.updated_at""",
                (account.student_id, account.username, int(account.enabled), now),
            )

    def activate_account(self, student_id: str, rule_name: str) -> bool:
        """Enable a disabled account and audit the action in one transaction."""
        now = _utc_now()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT enabled FROM moodle_accounts WHERE student_id = ?",
                (student_id,),
            ).fetchone()
            if row is None or row["enabled"]:
                return False
            connection.execute(
                "UPDATE moodle_accounts SET enabled = 1, updated_at = ? WHERE student_id = ?",
                (now, student_id),
            )
            self._insert_audit(
                connection,
                student_id,
                "account_activated",
                {"actor": "system", "rule": rule_name, "mode": "simulation"},
                now,
            )
            return True

    def add_audit_event(
        self,
        event_type: str,
        details: Mapping[str, object],
        student_id: str | None = None,
    ) -> None:
        with self._connection() as connection:
            self._insert_audit(connection, student_id, event_type, details, _utc_now())

    def _insert_audit(
        self,
        connection: sqlite3.Connection,
        student_id: str | None,
        event_type: str,
        details: Mapping[str, object],
        happened_at: str,
    ) -> None:
        connection.execute(
            "INSERT INTO audit_log (student_id, event_type, details, happened_at) VALUES (?, ?, ?, ?)",
            (student_id, event_type, json.dumps(details, sort_keys=True), happened_at),
        )

    def list_students(self) -> list[Student]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT student_id, full_name FROM students ORDER BY student_id"
            ).fetchall()
        return [Student(row["student_id"], row["full_name"]) for row in rows]

    def list_assessments(self) -> list[FeeAssessment]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT student_id, amount_due FROM assessments
                WHERE category = 'tuition' ORDER BY student_id"""
            ).fetchall()
        return [FeeAssessment(row["student_id"], row["amount_due"]) for row in rows]

    def list_payments(self) -> list[Payment]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT reference, student_id, category, amount, status, paid_at
                FROM payments WHERE category = 'tuition' ORDER BY paid_at, reference"""
            ).fetchall()
        return [
            Payment(
                row["reference"],
                row["student_id"],
                row["amount"],
                PaymentStatus(row["status"]),
                row["paid_at"],
            )
            for row in rows
        ]

    def list_accounts(self) -> list[MoodleAccount]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT student_id, username, enabled FROM moodle_accounts ORDER BY student_id"
            ).fetchall()
        return [
            MoodleAccount(row["student_id"], row["username"], bool(row["enabled"]))
            for row in rows
        ]

    def list_audit_events(self) -> list[dict[str, object]]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT id, student_id, event_type, details, happened_at
                FROM audit_log ORDER BY id DESC"""
            ).fetchall()
        return [
            {
                "id": row["id"],
                "student_id": row["student_id"],
                "event_type": row["event_type"],
                "details": json.loads(row["details"]),
                "happened_at": row["happened_at"],
            }
            for row in rows
        ]

    def import_bundle(self, datasets: Mapping[str, pd.DataFrame]) -> int:
        """Validate all CSV tables before atomically applying the imported snapshot."""
        if set(datasets) != set(_IMPORT_COLUMNS):
            raise DataValidationError(
                "Import requires students, assessments, payments and accounts CSV files."
            )
        records: dict[str, list[dict[str, object]]] = {}
        for name, required_columns in _IMPORT_COLUMNS.items():
            frame = datasets[name]
            missing = required_columns - set(frame.columns)
            if missing:
                raise DataValidationError(
                    f"{name} CSV is missing columns: {', '.join(sorted(missing))}."
                )
            if frame.empty:
                raise DataValidationError(f"{name} CSV must contain at least one record.")
            if name in {"assessments", "payments"} and "category" in frame.columns:
                categories = frame["category"].astype("string").str.strip().str.casefold()
                if categories.isna().any() or not categories.isin(_LEGACY_CATEGORIES).all():
                    raise DataValidationError(
                        f"{name} CSV contains an empty or unsupported legacy category."
                    )
                frame = frame.loc[categories.eq("tuition")]
            if frame[list(required_columns)].isna().any().any():
                raise DataValidationError(f"{name} CSV contains empty required values.")
            records[name] = frame.to_dict(orient="records")

        imported_count = sum(len(rows) for rows in records.values())
        try:
            with self._connection() as connection:
                connection.execute("DELETE FROM payments WHERE category = 'tuition'")
                connection.execute("DELETE FROM assessments WHERE category = 'tuition'")
                connection.execute("DELETE FROM moodle_accounts")
                connection.execute("DELETE FROM students")
                for row in records["students"]:
                    student = Student(
                        str(row["student_id"]),
                        str(row["full_name"]),
                    )
                    self._upsert_student(connection, student)
                for row in records["assessments"]:
                    assessment = FeeAssessment(
                        str(row["student_id"]),
                        _number(row["amount_due"], "amount_due"),
                    )
                    self._set_assessment(connection, assessment)
                for row in records["payments"]:
                    payment = Payment(
                        str(row["reference"]),
                        str(row["student_id"]),
                        _number(row["amount"], "amount"),
                        PaymentStatus(str(row["status"]).strip().lower()),
                        str(row["paid_at"]),
                    )
                    self._record_payment(connection, payment)
                for row in records["accounts"]:
                    account = MoodleAccount(
                        str(row["student_id"]),
                        str(row["username"]),
                        _parse_bool(row["enabled"], "enabled"),
                    )
                    connection.execute(
                        """INSERT INTO moodle_accounts (student_id, username, enabled, updated_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(student_id) DO UPDATE SET
                            username = excluded.username, enabled = excluded.enabled,
                            updated_at = excluded.updated_at""",
                        (account.student_id, account.username, int(account.enabled), _utc_now()),
                    )
                self._insert_audit(
                    connection,
                    None,
                    "data_imported",
                    {"record_count": imported_count},
                    _utc_now(),
                )
        except (sqlite3.IntegrityError, ValueError, TypeError) as error:
            raise DataValidationError(f"Import was not applied: {error}") from error
        return imported_count


def _number(value: object, label: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise DataValidationError(f"{label} must be a number.") from error


def _parse_bool(value: object, label: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "yes", "1"}:
        return True
    if normalized in {"false", "no", "0"}:
        return False
    raise DataValidationError(f"{label} must be true or false.")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
