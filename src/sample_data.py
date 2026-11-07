"""Synthetic tuition and Moodle records for exploring reconciliation outcomes."""

from __future__ import annotations

from .domain import FeeAssessment, MoodleAccount, Payment, PaymentStatus, Student
from .storage import SQLiteRepository


def load_demo_data(repository: SQLiteRepository) -> None:
    """Seed an empty database with fictional tuition-only reconciliation cases."""
    if not repository.is_empty():
        return

    students = [
        Student("UCU-2026-001", "Amara Nsubuga"),
        Student("UCU-2026-002", "Brian Okello"),
        Student("UCU-2026-003", "Carol Atim"),
        Student("UCU-2026-004", "David Kato"),
        Student("UCU-2026-005", "Esther Namusoke"),
        Student("UCU-2026-006", "Frank Ouma"),
        Student("UCU-2026-007", "Grace Acan"),
        Student("UCU-2026-008", "Henry Ssemanda"),
        Student("UCU-2026-009", "Irene Nakato"),
    ]
    for student in students:
        repository.upsert_student(student)

    tuition_due = {
        "UCU-2026-001": 600_000,
        "UCU-2026-002": 500_000,
        "UCU-2026-003": 600_000,
        "UCU-2026-004": 600_000,
        "UCU-2026-005": 500_000,
        "UCU-2026-006": 500_000,
        "UCU-2026-007": 400_000,
        "UCU-2026-008": 500_000,
        "UCU-2026-009": 500_000,
    }
    for student_id, amount_due in tuition_due.items():
        repository.set_assessment(FeeAssessment(student_id, amount_due))

    tuition_payments = [
        ("UCU-2026-001", 300_000),
        ("UCU-2026-002", 450_000),
        ("UCU-2026-003", 600_000),
        ("UCU-2026-004", 600_000),
        ("UCU-2026-005", 500_000),
        ("UCU-2026-006", 500_000),
        ("UCU-2026-007", 400_000),
        ("UCU-2026-009", 100_000),
    ]
    for index, (student_id, amount) in enumerate(tuition_payments, start=1):
        repository.record_payment(
            Payment(
                f"DEMO-{index:03d}",
                student_id,
                amount,
                PaymentStatus.CONFIRMED,
                "2026-10-20T10:00:00+00:00",
            )
        )
    repository.record_payment(
        Payment(
            "DEMO-PENDING-001",
            "UCU-2026-002",
            50_000,
            PaymentStatus.PENDING,
            "2026-10-29T10:00:00+00:00",
        )
    )
    repository.record_payment(
        Payment(
            "DEMO-ORPHAN-001",
            "UCU-UNKNOWN-001",
            100_000,
            PaymentStatus.CONFIRMED,
            "2026-10-20T10:00:00+00:00",
        )
    )

    for student in students:
        if student.student_id == "UCU-2026-007":
            continue
        enabled = student.student_id == "UCU-2026-009"
        repository.upsert_account(MoodleAccount(student.student_id, student.student_id.lower(), enabled))
    repository.upsert_account(MoodleAccount("UCU-UNKNOWN-002", "orphan.account", False))
    add_manual_test_scenarios(repository)


def add_manual_test_scenarios(repository: SQLiteRepository) -> int:
    """Add isolated, repeat-safe tuition cases with varied payment coverage."""
    scenarios = (
        ("UCU-TEST-001", "Test Case - 0% Tuition Paid", ()),
        ("UCU-TEST-002", "Test Case - 20% Tuition Paid", (200_000,)),
        ("UCU-TEST-003", "Test Case - 45% Tuition Paid", (450_000,)),
        ("UCU-TEST-004", "Test Case - 50% Split Payment", (200_000, 300_000)),
        ("UCU-TEST-005", "Test Case - 75% Tuition Paid", (750_000,)),
        ("UCU-TEST-006", "Test Case - 90% Tuition Paid", (900_000,)),
        ("UCU-TEST-007", "Test Case - Fully Paid Tuition", (1_000_000,)),
        ("UCU-TEST-008", "Test Case - 25% Confirmed, 25% Pending", (250_000,)),
    )
    existing_student_ids = {student.student_id for student in repository.list_students()}
    added = 0

    for student_id, full_name, payments in scenarios:
        if student_id in existing_student_ids:
            continue

        repository.upsert_student(Student(student_id, full_name))
        repository.set_assessment(FeeAssessment(student_id, 1_000_000))
        repository.upsert_account(MoodleAccount(student_id, student_id.lower(), False))

        for payment_number, amount in enumerate(payments, start=1):
            repository.record_payment(
                Payment(
                    f"{student_id}-PAY-{payment_number:02d}",
                    student_id,
                    amount,
                    PaymentStatus.CONFIRMED,
                    "2026-08-01T10:00:00+00:00",
                )
            )
        if student_id == "UCU-TEST-008":
            repository.record_payment(
                Payment(
                    f"{student_id}-PENDING-01",
                    student_id,
                    250_000,
                    PaymentStatus.PENDING,
                    "2026-08-01T10:00:00+00:00",
                )
            )
        added += 1

    return added
