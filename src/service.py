"""Tuition reconciliation orchestration and access activation simulation."""

from __future__ import annotations

from datetime import date
from typing import Mapping

import numpy as np
import pandas as pd

from .domain import PaymentStatus, Student
from .policies import EligibilityRuleSet
from .storage import SQLiteRepository


class FeeReconciliationService:
    """Reconcile assessed tuition, confirmed tuition payments, and Moodle access."""

    def __init__(self, repository: SQLiteRepository, rules: EligibilityRuleSet) -> None:
        self.repository = repository
        self.rules = rules

    def reconcile(self, as_of: date, *, auto_activate: bool = True) -> pd.DataFrame:
        students = self.repository.list_students()
        if not students:
            return pd.DataFrame(columns=self._result_columns())

        student_frame = pd.DataFrame(
            [
                {"student_id": student.student_id, "full_name": student.full_name}
                for student in students
            ]
        )
        assessment_frame = pd.DataFrame(
            [
                {"student_id": item.student_id, "tuition_due_ugx": item.amount_due}
                for item in self.repository.list_assessments()
            ],
            columns=["student_id", "tuition_due_ugx"],
        )
        payment_frame = self._confirmed_tuition_payments(as_of)
        account_frame = pd.DataFrame(
            [
                {
                    "student_id": account.student_id,
                    "account_username": account.username,
                    "access_enabled": account.enabled,
                }
                for account in self.repository.list_accounts()
            ],
            columns=["student_id", "account_username", "access_enabled"],
        )
        joined = (
            student_frame.merge(assessment_frame, on="student_id", how="left")
            .merge(payment_frame, on="student_id", how="left")
            .merge(account_frame, on="student_id", how="left", indicator="account_match")
        )
        joined["tuition_paid_ugx"] = joined["tuition_paid_ugx"].fillna(0.0)
        self._add_tuition_percentages(joined)

        cases: list[dict[str, object]] = []
        for row in joined.to_dict(orient="records"):
            student = Student(row["student_id"], row["full_name"])
            tuition_due = (
                float(row["tuition_due_ugx"])
                if pd.notna(row["tuition_due_ugx"])
                else None
            )
            tuition_paid = float(row["tuition_paid_ugx"])
            decision = self.rules.assess(as_of, student, tuition_due, tuition_paid)
            assessed_total = tuition_due or 0.0
            access_exists = row["account_match"] == "both"
            access_enabled = bool(row["access_enabled"]) if access_exists else None
            activated = False

            if decision.eligible is True and access_exists and not access_enabled and auto_activate:
                activated = self.repository.activate_account(student.student_id, decision.rule_name)
                if activated:
                    access_enabled = True

            if not access_exists:
                status = "Missing Moodle account"
                action = "Review account mapping; no access change made."
            elif decision.eligible is None:
                status = "Manual review"
                action = "No automatic access change."
            elif activated:
                status = "Activated automatically"
                action = f"Simulated activation under the {decision.rule_name} tuition rule."
            elif access_enabled:
                status = "Access active"
                action = "No action required."
            elif decision.eligible:
                status = "Eligible - awaiting activation"
                action = "Run automatic reconciliation to simulate activation."
            else:
                status = "Access disabled - tuition below rule"
                action = "Do not activate; the documented tuition threshold is not met."

            cases.append(
                {
                    **row,
                    "tuition_paid_ugx": tuition_paid,
                    "tuition_balance_ugx": max(assessed_total - tuition_paid, 0.0),
                    "tuition_credit_ugx": max(tuition_paid - assessed_total, 0.0),
                    "as_of_date": as_of.isoformat(),
                    "rule_name": decision.rule_name,
                    "eligible": decision.eligible,
                    "explanation": decision.explanation,
                    "access_exists": access_exists,
                    "access_enabled": access_enabled,
                    "activated_now": activated,
                    "case_status": status,
                    "recommended_action": action,
                }
            )
        return pd.DataFrame(cases, columns=self._result_columns())

    def data_quality(self) -> pd.DataFrame:
        students = {student.student_id for student in self.repository.list_students()}
        rows: list[dict[str, str]] = []

        for payment in self.repository.list_payments():
            if payment.student_id not in students:
                rows.append(
                    {
                        "record_type": "Tuition payment",
                        "record_id": payment.reference,
                        "issue": f"Payment student ID {payment.student_id} is unmatched.",
                        "severity": "High",
                    }
                )
        for account in self.repository.list_accounts():
            if account.student_id not in students:
                rows.append(
                    {
                        "record_type": "Moodle account",
                        "record_id": account.username or account.student_id,
                        "issue": f"Account student ID {account.student_id} is unmatched.",
                        "severity": "High",
                    }
                )

        assessed_student_ids = {
            assessment.student_id for assessment in self.repository.list_assessments()
        }
        for student in self.repository.list_students():
            if student.student_id not in assessed_student_ids:
                rows.append(
                    {
                        "record_type": "Student",
                        "record_id": student.student_id,
                        "issue": "Missing assessed tuition amount.",
                        "severity": "Medium",
                    }
                )

        return pd.DataFrame(rows, columns=["record_type", "record_id", "issue", "severity"])

    def _confirmed_tuition_payments(self, as_of: date) -> pd.DataFrame:
        payments = [
            {
                "student_id": payment.student_id,
                "amount": payment.amount,
            }
            for payment in self.repository.list_payments()
            if payment.status is PaymentStatus.CONFIRMED
            and date.fromisoformat(payment.paid_at[:10]) <= as_of
        ]
        frame = pd.DataFrame(payments, columns=["student_id", "amount"])
        if frame.empty:
            return pd.DataFrame(columns=["student_id", "tuition_paid_ugx"])
        return (
            frame.groupby("student_id", as_index=False)["amount"]
            .sum()
            .rename(columns={"amount": "tuition_paid_ugx"})
        )

    @staticmethod
    def _add_tuition_percentages(frame: pd.DataFrame) -> None:
        tuition_due = frame["tuition_due_ugx"].to_numpy(dtype=float, na_value=np.nan)
        tuition_paid = frame["tuition_paid_ugx"].to_numpy(dtype=float, copy=True)
        frame["tuition_paid_percent"] = np.divide(
            tuition_paid * 100,
            tuition_due,
            out=np.full(len(frame), np.nan),
            where=tuition_due > 0,
        )

    @staticmethod
    def _result_columns() -> list[str]:
        return [
            "student_id",
            "full_name",
            "tuition_due_ugx",
            "tuition_paid_ugx",
            "tuition_balance_ugx",
            "tuition_credit_ugx",
            "tuition_paid_percent",
            "as_of_date",
            "rule_name",
            "eligible",
            "explanation",
            "account_username",
            "access_exists",
            "access_enabled",
            "activated_now",
            "case_status",
            "recommended_action",
        ]
