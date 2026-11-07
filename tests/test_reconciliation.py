from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path

import pandas as pd

from src.analytics import ReconciliationAnalytics
from src.domain import FeeAssessment, MoodleAccount, Payment, PaymentStatus, Student
from src.policies import TuitionMilestoneRule, ucu_2026_rules
from src.sample_data import add_manual_test_scenarios, load_demo_data
from src.service import FeeReconciliationService
from src.storage import DataValidationError, SQLiteRepository


class RuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = ucu_2026_rules()
        self.student = Student("S-1", "Test Student")

    def test_tuition_cutoffs_and_percentages(self) -> None:
        decision = self.rules.assess(date(2026, 8, 14), self.student, 1000, 450)
        self.assertTrue(decision.eligible)
        self.assertEqual(decision.rule_name, "Prompt payment")

        decision = self.rules.assess(date(2026, 10, 19), self.student, 1000, 749)
        self.assertFalse(decision.eligible)
        self.assertIn("75%", decision.explanation)
        self.assertEqual(decision.rule_name, "Midterm")

        decision = self.rules.assess(date(2026, 10, 19), self.student, 1000, 750)
        self.assertTrue(decision.eligible)

    def test_cutoffs_are_cumulative_and_do_not_expire(self) -> None:
        decision = self.rules.assess(date(2026, 10, 19), self.student, 1000, 450)
        self.assertFalse(decision.eligible)
        self.assertIn("Midterm", decision.explanation)

        decision = self.rules.assess(date(2026, 11, 7), self.student, 1000, 999)
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.rule_name, "Late period - full tuition")

        decision = self.rules.assess(date(2026, 11, 7), self.student, 1000, 1000)
        self.assertTrue(decision.eligible)

    def test_missing_assessment_and_date_before_first_cutoff_require_review(self) -> None:
        decision = self.rules.assess(date(2026, 8, 14), self.student, None, 100)
        self.assertIsNone(decision.eligible)
        self.assertIn("tuition assessment is missing", decision.explanation)

        decision = self.rules.assess(date(2026, 8, 13), self.student, 1000, 1000)
        self.assertIsNone(decision.eligible)

    def test_zero_tuition_assessment_requires_review(self) -> None:
        decision = self.rules.assess(date(2026, 8, 14), self.student, 0, 0)
        self.assertIsNone(decision.eligible)
        self.assertIn("greater than zero", decision.explanation)

    def test_cutoff_rule_is_polymorphic_and_retained(self) -> None:
        rule = TuitionMilestoneRule("Tuition cutoff", date(2026, 8, 14), 0.45)
        self.assertFalse(rule.applies_on(date(2026, 8, 13)))
        self.assertTrue(rule.applies_on(date(2026, 8, 14)))
        self.assertTrue(rule.applies_on(date(2026, 11, 7)))
        self.assertTrue(rule.evaluate(self.student, 1000, 500).eligible)


class ReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "test.sqlite3"
        self.repository = SQLiteRepository(self.database_path)
        self.service = FeeReconciliationService(self.repository, ucu_2026_rules())

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def add_tuition_student(
        self,
        student_id: str,
        tuition_due: float = 1000,
        tuition_paid: float = 0,
        *,
        enabled: bool = False,
        paid_at: str = "2026-10-20",
    ) -> None:
        self.repository.upsert_student(Student(student_id, "Test Student"))
        self.repository.set_assessment(FeeAssessment(student_id, tuition_due))
        self.repository.upsert_account(MoodleAccount(student_id, student_id.lower(), enabled))
        if tuition_paid:
            self.repository.record_payment(
                Payment(
                    f"{student_id}-payment",
                    student_id,
                    tuition_paid,
                    PaymentStatus.CONFIRMED,
                    paid_at,
                )
            )

    def test_cleared_tuition_automatically_activates_and_audits_account(self) -> None:
        self.add_tuition_student("S-1", tuition_paid=1000)
        cases = self.service.reconcile(date(2026, 11, 7))
        case = cases.iloc[0]
        self.assertEqual(case["tuition_balance_ugx"], 0)
        self.assertTrue(case["eligible"])
        self.assertEqual(case["case_status"], "Activated automatically")
        self.assertTrue(self.repository.list_accounts()[0].enabled)
        self.assertEqual(self.repository.list_audit_events()[0]["event_type"], "account_activated")

    def test_frank_ouma_demo_case_has_cleared_tuition_and_activates(self) -> None:
        load_demo_data(self.repository)
        cases = self.service.reconcile(date(2026, 11, 7))
        frank = cases.loc[cases["student_id"] == "UCU-2026-006"].iloc[0]
        self.assertEqual(frank["tuition_due_ugx"], 500_000)
        self.assertEqual(frank["tuition_paid_ugx"], 500_000)
        self.assertEqual(frank["tuition_balance_ugx"], 0)
        self.assertTrue(frank["eligible"])
        self.assertEqual(frank["case_status"], "Activated automatically")
        self.assertTrue(
            next(
                account.enabled
                for account in self.repository.list_accounts()
                if account.student_id == "UCU-2026-006"
            )
        )

    def test_payments_in_legacy_other_categories_do_not_affect_tuition(self) -> None:
        self.add_tuition_student("S-2", tuition_paid=450)
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                """INSERT INTO assessments (student_id, category, amount_due)
                VALUES (?, 'other', ?)""",
                ("S-2", 9000),
            )
            connection.execute(
                """INSERT INTO payments
                (reference, student_id, category, amount, status, paid_at)
                VALUES (?, ?, 'other', ?, 'confirmed', ?)""",
                ("other-payment", "S-2", 9000, "2026-10-20"),
            )

        case = self.service.reconcile(date(2026, 10, 20)).iloc[0]
        self.assertEqual(case["tuition_due_ugx"], 1000)
        self.assertEqual(case["tuition_paid_ugx"], 450)
        self.assertEqual(case["tuition_balance_ugx"], 550)
        self.assertFalse(case["eligible"])

    def test_pending_and_future_tuition_payments_do_not_count(self) -> None:
        self.repository.upsert_student(Student("S-3", "Pending Student"))
        self.repository.set_assessment(FeeAssessment("S-3", 1000))
        self.repository.upsert_account(MoodleAccount("S-3", "s-3", False))
        self.repository.record_payment(
            Payment("pending", "S-3", 900, PaymentStatus.PENDING, "2026-08-14")
        )
        self.repository.record_payment(
            Payment("future", "S-3", 500, PaymentStatus.CONFIRMED, "2026-09-01")
        )

        case = self.service.reconcile(date(2026, 8, 14)).iloc[0]
        self.assertEqual(case["tuition_paid_ugx"], 0)
        self.assertEqual(case["tuition_balance_ugx"], 1000)
        self.assertFalse(case["eligible"])

    def test_late_tuition_payment_after_cutoff_restores_access(self) -> None:
        self.add_tuition_student("S-4", tuition_paid=1000, paid_at="2026-11-07")
        case = self.service.reconcile(date(2026, 11, 7)).iloc[0]
        self.assertTrue(case["eligible"])
        self.assertEqual(case["case_status"], "Activated automatically")

    def test_unpaid_tuition_does_not_activate_and_active_access_is_not_revoked(self) -> None:
        self.add_tuition_student("S-5", tuition_paid=500)
        case = self.service.reconcile(date(2026, 11, 7)).iloc[0]
        self.assertFalse(case["eligible"])
        self.assertEqual(case["case_status"], "Access disabled - tuition below rule")
        self.assertFalse(self.repository.list_accounts()[0].enabled)

        self.repository.upsert_account(MoodleAccount("S-5", "s-5", True))
        case = self.service.reconcile(date(2026, 11, 7)).iloc[0]
        self.assertEqual(case["case_status"], "Access active")
        self.assertTrue(self.repository.list_accounts()[0].enabled)

    def test_missing_account_is_reported_without_claiming_activation(self) -> None:
        self.repository.upsert_student(Student("S-6", "No Account"))
        self.repository.set_assessment(FeeAssessment("S-6", 1000))
        self.repository.record_payment(
            Payment("S-6-payment", "S-6", 1000, PaymentStatus.CONFIRMED, "2026-11-07")
        )
        case = self.service.reconcile(date(2026, 11, 7)).iloc[0]
        self.assertTrue(case["eligible"])
        self.assertEqual(case["case_status"], "Missing Moodle account")

    def test_tuition_data_quality_and_empty_analytics(self) -> None:
        load_demo_data(self.repository)
        self.repository.upsert_student(Student("UCU-MISSING-TUITION", "Missing Tuition"))
        findings = self.service.data_quality()
        self.assertIn(
            "Payment student ID UCU-UNKNOWN-001 is unmatched.",
            findings["issue"].tolist(),
        )
        self.assertIn(
            "Missing assessed tuition amount.",
            findings["issue"].tolist(),
        )

        empty_repository = SQLiteRepository(Path(self.temp_dir.name) / "empty.sqlite3")
        empty_service = FeeReconciliationService(empty_repository, ucu_2026_rules())
        summary = ReconciliationAnalytics(empty_repository).summary(
            empty_service.reconcile(date(2026, 11, 7))
        )
        self.assertEqual(summary["students"], 0)
        self.assertEqual(summary["mean_tuition_paid_percent"], 0.0)

    def test_missing_tuition_assessment_is_manual_review(self) -> None:
        self.repository.upsert_student(Student("S-7", "No Assessment"))
        self.repository.upsert_account(MoodleAccount("S-7", "s-7", False))
        case = self.service.reconcile(date(2026, 11, 7)).iloc[0]
        self.assertIsNone(case["eligible"])
        self.assertEqual(case["case_status"], "Manual review")

    def test_manual_test_scenarios_cover_varied_payment_percentages(self) -> None:
        self.assertEqual(add_manual_test_scenarios(self.repository), 8)
        self.assertEqual(add_manual_test_scenarios(self.repository), 0)

        cases = self.service.reconcile(date(2026, 8, 14), auto_activate=False)
        cases_by_id = cases.set_index("student_id")
        self.assertEqual(cases_by_id.loc["UCU-TEST-001", "tuition_paid_percent"], 0)
        self.assertEqual(cases_by_id.loc["UCU-TEST-003", "tuition_paid_percent"], 45)
        self.assertEqual(cases_by_id.loc["UCU-TEST-004", "tuition_paid_percent"], 50)
        self.assertEqual(cases_by_id.loc["UCU-TEST-005", "tuition_paid_percent"], 75)
        self.assertEqual(cases_by_id.loc["UCU-TEST-008", "tuition_paid_percent"], 25)
        self.assertTrue(cases_by_id.loc["UCU-TEST-003", "eligible"])
        self.assertFalse(cases_by_id.loc["UCU-TEST-008", "eligible"])

        late_cases = self.service.reconcile(date(2026, 11, 7), auto_activate=False)
        late_by_id = late_cases.set_index("student_id")
        self.assertTrue(late_by_id.loc["UCU-TEST-007", "eligible"])
        self.assertFalse(late_by_id.loc["UCU-TEST-005", "eligible"])

    def test_manual_test_scenario_can_be_paid_to_full_and_activated(self) -> None:
        add_manual_test_scenarios(self.repository)
        self.repository.record_payment(
            Payment(
                "UCU-TEST-002-PAY-02",
                "UCU-TEST-002",
                800_000,
                PaymentStatus.CONFIRMED,
                "2026-11-07",
            )
        )
        cases = self.service.reconcile(date(2026, 11, 7))
        case = cases.loc[cases["student_id"] == "UCU-TEST-002"].iloc[0]
        self.assertEqual(case["tuition_balance_ugx"], 0)
        self.assertTrue(case["eligible"])
        self.assertEqual(case["case_status"], "Activated automatically")


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = SQLiteRepository(Path(self.temp_dir.name) / "test.sqlite3")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def import_datasets(student_id: str = "S-1") -> dict[str, pd.DataFrame]:
        return {
            "students": pd.DataFrame(
                [{"student_id": student_id, "full_name": "Test Student"}]
            ),
            "assessments": pd.DataFrame(
                [{"student_id": student_id, "amount_due": 100}]
            ),
            "payments": pd.DataFrame(
                [
                    {
                        "reference": "P-1",
                        "student_id": student_id,
                        "amount": 100,
                        "status": "confirmed",
                        "paid_at": "2026-10-01",
                    }
                ]
            ),
            "accounts": pd.DataFrame(
                [{"student_id": student_id, "username": "s1", "enabled": "false"}]
            ),
        }

    def test_import_bundle_is_atomic_when_a_later_record_is_invalid(self) -> None:
        self.repository.upsert_student(Student("EXISTING", "Existing Student"))
        datasets = self.import_datasets()
        datasets["assessments"] = pd.DataFrame(
            [{"student_id": "S-1", "amount_due": -100}]
        )
        with self.assertRaises(DataValidationError):
            self.repository.import_bundle(datasets)
        self.assertEqual(
            [student.student_id for student in self.repository.list_students()],
            ["EXISTING"],
        )
        self.assertEqual(self.repository.list_audit_events(), [])

    def test_import_bundle_replaces_source_snapshots(self) -> None:
        self.repository.upsert_student(Student("OLD", "Old Student"))
        self.repository.import_bundle(self.import_datasets("NEW"))
        self.assertEqual(
            [student.student_id for student in self.repository.list_students()],
            ["NEW"],
        )
        self.assertEqual(
            [payment.reference for payment in self.repository.list_payments()],
            ["P-1"],
        )
        self.assertFalse(self.repository.list_accounts()[0].enabled)

    def test_import_preserves_legacy_non_tuition_database_rows(self) -> None:
        self.repository.upsert_student(Student("OLD", "Old Student"))
        with closing(sqlite3.connect(self.repository.database_path)) as connection, connection:
            connection.execute(
                "INSERT INTO assessments (student_id, category, amount_due) VALUES (?, ?, ?)",
                ("OLD", "other", 200),
            )
            connection.execute(
                """INSERT INTO payments
                (reference, student_id, category, amount, status, paid_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                ("LEGACY-OTHER", "OLD", "other", 50, "confirmed", "2026-10-01"),
            )

        self.repository.import_bundle(self.import_datasets("NEW"))
        with closing(sqlite3.connect(self.repository.database_path)) as connection:
            legacy_assessment = connection.execute(
                "SELECT amount_due FROM assessments WHERE student_id = 'OLD' AND category = 'other'"
            ).fetchone()
            legacy_payment = connection.execute(
                "SELECT amount FROM payments WHERE reference = 'LEGACY-OTHER'"
            ).fetchone()
        self.assertEqual(legacy_assessment[0], 200)
        self.assertEqual(legacy_payment[0], 50)

    def test_import_filters_legacy_categories_to_tuition(self) -> None:
        datasets = self.import_datasets()
        datasets["assessments"] = pd.DataFrame(
            [
                {"student_id": "S-1", "category": "tuition", "amount_due": 100},
                {"student_id": "S-1", "category": "other", "amount_due": 900},
            ]
        )
        datasets["payments"] = pd.DataFrame(
            [
                {
                    "reference": "TUITION-1",
                    "student_id": "S-1",
                    "category": "tuition",
                    "amount": 100,
                    "status": "confirmed",
                    "paid_at": "2026-10-01",
                },
                {
                    "reference": "OTHER-1",
                    "student_id": "S-1",
                    "category": "other",
                    "amount": 900,
                    "status": "confirmed",
                    "paid_at": "2026-10-01",
                },
            ]
        )
        self.repository.import_bundle(datasets)
        assessment = self.repository.list_assessments()[0]
        self.assertEqual(assessment.student_id, "S-1")
        self.assertEqual(assessment.amount_due, 100)
        self.assertEqual(
            [payment.reference for payment in self.repository.list_payments()],
            ["TUITION-1"],
        )

    def test_invalid_domain_values_are_rejected(self) -> None:
        with self.assertRaises(DataValidationError):
            FeeAssessment("S-1", -1)
        with self.assertRaises(DataValidationError):
            FeeAssessment("S-1", float("nan"))
        with self.assertRaises(DataValidationError):
            Payment("bad-date", "S-1", 10, PaymentStatus.CONFIRMED, "not-a-date")

    def test_duplicate_payment_reference_is_reported(self) -> None:
        payment = Payment("duplicate", "S-1", 10, PaymentStatus.CONFIRMED, "2026-10-01")
        self.repository.record_payment(payment)
        with self.assertRaisesRegex(DataValidationError, "reference may already exist"):
            self.repository.record_payment(payment)


if __name__ == "__main__":
    unittest.main()
