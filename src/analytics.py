"""Operational summaries and payment-to-activation turnaround analysis."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .storage import SQLiteRepository


class ReconciliationAnalytics:
    """Builds descriptive summaries without modifying university policy."""

    def __init__(self, repository: SQLiteRepository) -> None:
        self.repository = repository

    def summary(self, cases: pd.DataFrame) -> dict[str, int | float]:
        if cases.empty:
            return {
                "students": 0,
                "eligible": 0,
                "access_disabled": 0,
                "activated": 0,
                "manual_review": 0,
                "mean_tuition_paid_percent": 0.0,
            }
        percentages = cases["tuition_paid_percent"].dropna().to_numpy(dtype=float)
        mean_paid = float(np.mean(percentages)) if percentages.size else 0.0
        return {
            "students": int(len(cases)),
            "eligible": int(cases["eligible"].eq(True).sum()),
            "access_disabled": int((cases["access_exists"] & ~cases["access_enabled"].fillna(False)).sum()),
            "activated": int(cases["activated_now"].sum()),
            "manual_review": int(cases["eligible"].isna().sum()),
            "mean_tuition_paid_percent": mean_paid,
        }

    def turnaround_hours(self) -> pd.DataFrame:
        """Measure confirmed-payment-to-simulated-activation time per activation."""
        payments = [
            {
                "student_id": payment.student_id,
                "paid_at": payment.paid_at,
                "status": payment.status.value,
            }
            for payment in self.repository.list_payments()
            if payment.status.value == "confirmed"
        ]
        events = [
            {
                "student_id": event["student_id"],
                "activated_at": event["happened_at"],
                "rule": event["details"].get("rule", ""),
            }
            for event in self.repository.list_audit_events()
            if event["event_type"] == "account_activated"
        ]
        columns = ["student_id", "last_confirmed_payment", "activated_at", "turnaround_hours", "rule"]
        if not events or not payments:
            return pd.DataFrame(columns=columns)

        payment_frame = pd.DataFrame(payments)
        event_frame = pd.DataFrame(events)
        payment_frame["paid_at"] = pd.to_datetime(payment_frame["paid_at"], utc=True, errors="coerce")
        event_frame["activated_at"] = pd.to_datetime(event_frame["activated_at"], utc=True, errors="coerce")
        merged = event_frame.merge(payment_frame, on="student_id", how="inner")
        merged = merged[merged["paid_at"] <= merged["activated_at"]]
        if merged.empty:
            return pd.DataFrame(columns=columns)
        last_payments = merged.groupby(["student_id", "activated_at"], as_index=False)["paid_at"].max()
        result = last_payments.merge(event_frame, on=["student_id", "activated_at"], how="left")
        result["turnaround_hours"] = (
            result["activated_at"] - result["paid_at"]
        ).dt.total_seconds() / 3600
        return result.rename(
            columns={"paid_at": "last_confirmed_payment"}
        )[columns].sort_values("activated_at", ascending=False)

    def turnaround_summary(self) -> dict[str, float | int]:
        durations = self.turnaround_hours()["turnaround_hours"].to_numpy(dtype=float)
        if not durations.size:
            return {"activations_with_payment_history": 0, "mean_hours": 0.0, "median_hours": 0.0}
        return {
            "activations_with_payment_history": int(durations.size),
            "mean_hours": float(np.mean(durations)),
            "median_hours": float(np.median(durations)),
        }
