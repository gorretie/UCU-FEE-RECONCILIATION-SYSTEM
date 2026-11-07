"""Validated domain objects used by the reconciliation system."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .storage_exceptions import DataValidationError


class PaymentStatus(str, Enum):
    CONFIRMED = "confirmed"
    PENDING = "pending"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Student:
    student_id: str
    full_name: str

    def __post_init__(self) -> None:
        if not self.student_id.strip():
            raise DataValidationError("Student ID is required.")
        if not self.full_name.strip():
            raise DataValidationError("Student name is required.")
        object.__setattr__(self, "student_id", self.student_id.strip())
        object.__setattr__(self, "full_name", self.full_name.strip())


@dataclass(frozen=True)
class FeeAssessment:
    student_id: str
    amount_due: float

    def __post_init__(self) -> None:
        if not self.student_id.strip():
            raise DataValidationError("Assessment student ID is required.")
        object.__setattr__(self, "student_id", self.student_id.strip())
        _validate_amount(self.amount_due, "Assessment amount", allow_zero=True)


@dataclass(frozen=True)
class Payment:
    reference: str
    student_id: str
    amount: float
    status: PaymentStatus
    paid_at: str

    def __post_init__(self) -> None:
        if not self.reference.strip():
            raise DataValidationError("Payment reference is required.")
        if not self.student_id.strip():
            raise DataValidationError("Payment student ID is required.")
        if not self.paid_at.strip():
            raise DataValidationError("Payment date is required.")
        if not isinstance(self.status, PaymentStatus):
            raise DataValidationError("Payment status is invalid.")
        try:
            datetime.fromisoformat(self.paid_at.strip().replace("Z", "+00:00"))
        except ValueError as error:
            raise DataValidationError("Payment date must be an ISO 8601 date or timestamp.") from error
        object.__setattr__(self, "reference", self.reference.strip())
        object.__setattr__(self, "student_id", self.student_id.strip())
        object.__setattr__(self, "paid_at", self.paid_at.strip())
        _validate_amount(self.amount, "Payment amount", allow_zero=False)


@dataclass(frozen=True)
class MoodleAccount:
    student_id: str
    username: str
    enabled: bool

    def __post_init__(self) -> None:
        if not self.student_id.strip():
            raise DataValidationError("Moodle account student ID is required.")
        if not self.username.strip():
            raise DataValidationError("Moodle username is required.")
        if not isinstance(self.enabled, bool):
            raise DataValidationError("Moodle enabled status must be true or false.")
        object.__setattr__(self, "student_id", self.student_id.strip())
        object.__setattr__(self, "username", self.username.strip())


def _validate_amount(amount: float, label: str, *, allow_zero: bool) -> None:
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        raise DataValidationError(f"{label} must be a number.")
    if not math.isfinite(amount) or amount < 0 or (not allow_zero and amount == 0):
        requirement = "zero or greater" if allow_zero else "greater than zero"
        raise DataValidationError(f"{label} must be finite and {requirement}.")
