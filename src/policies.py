"""Cumulative tuition-only cutoff rules from the supplied UCU 2026 schedule."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

from .domain import Student


@dataclass(frozen=True)
class RuleDecision:
    eligible: bool | None
    rule_name: str
    explanation: str


class EligibilityRule(ABC):
    """Abstract tuition rule activated on its cutoff and retained afterward."""

    def __init__(self, name: str, effective_date: date) -> None:
        self.name = name
        self.effective_date = effective_date

    def applies_on(self, as_of: date) -> bool:
        return self.effective_date <= as_of

    @abstractmethod
    def evaluate(self, student: Student, tuition_due: float | None, tuition_paid: float) -> RuleDecision:
        """Check the student's confirmed tuition payments against assessed tuition."""


class TuitionMilestoneRule(EligibilityRule):
    """A tuition payment threshold attached to a university deadline."""

    def __init__(self, name: str, effective_date: date, required_fraction: float) -> None:
        super().__init__(name, effective_date)
        if not 0 < required_fraction <= 1:
            raise ValueError("Tuition threshold must be greater than zero and at most one.")
        self.required_fraction = required_fraction

    def evaluate(self, student: Student, tuition_due: float | None, tuition_paid: float) -> RuleDecision:
        if tuition_due is None:
            return RuleDecision(None, self.name, "Manual review: tuition assessment is missing.")
        if tuition_due <= 0:
            return RuleDecision(None, self.name, "Manual review: assessed tuition must be greater than zero.")

        fraction_paid = tuition_paid / tuition_due
        if fraction_paid + 1e-9 < self.required_fraction:
            return RuleDecision(
                False,
                self.name,
                f"Tuition paid: {fraction_paid:.1%}; at least {self.required_fraction:.0%} is required.",
            )
        return RuleDecision(
            True,
            self.name,
            f"Tuition payment requirement of at least {self.required_fraction:.0%} is met.",
        )


class EligibilityRuleSet:
    """Accumulates each reached tuition cutoff, with the latest threshold controlling."""

    def __init__(self, rules: tuple[EligibilityRule, ...]) -> None:
        if not rules:
            raise ValueError("At least one eligibility rule is required.")
        self._rules = rules

    def assess(
        self,
        as_of: date,
        student: Student,
        tuition_due: float | None,
        tuition_paid: float,
    ) -> RuleDecision:
        applicable = sorted(
            (rule for rule in self._rules if rule.applies_on(as_of)),
            key=lambda rule: rule.effective_date,
        )
        if not applicable:
            return RuleDecision(
                None,
                "No documented rule",
                "No automatic access decision: the first supplied tuition cutoff has not been reached.",
            )

        decisions = [
            rule.evaluate(student, tuition_due, tuition_paid) for rule in applicable
        ]
        latest_rule = applicable[-1].name
        missing_assessment = next(
            (decision for decision in decisions if decision.eligible is None),
            None,
        )
        if missing_assessment is not None:
            return RuleDecision(None, latest_rule, missing_assessment.explanation)

        unmet = [decision for decision in decisions if decision.eligible is False]
        if unmet:
            return RuleDecision(
                False,
                latest_rule,
                "Unmet cumulative tuition requirement(s): "
                + " ".join(
                    f"{decision.rule_name}: {decision.explanation}" for decision in unmet
                ),
            )

        return RuleDecision(
            True,
            latest_rule,
            f"All cumulative tuition requirements through {latest_rule} are met.",
        )

    @property
    def rules(self) -> tuple[EligibilityRule, ...]:
        return self._rules


def ucu_2026_rules() -> EligibilityRuleSet:
    """Build tuition-only thresholds using the dates in the supplied schedule."""
    return EligibilityRuleSet(
        (
            TuitionMilestoneRule("Prompt payment", date(2026, 8, 14), 0.45),
            TuitionMilestoneRule("Registration", date(2026, 9, 11), 0.45),
            TuitionMilestoneRule("Midterm", date(2026, 10, 9), 0.75),
            TuitionMilestoneRule("Full tuition", date(2026, 10, 23), 1.0),
            TuitionMilestoneRule("Late period - full tuition", date(2026, 10, 24), 1.0),
        )
    )
