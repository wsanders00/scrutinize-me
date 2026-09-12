"""Deterministic, provider-neutral adapter used only by contract tests."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from scrutinize_me_skill.contracts import (
    CORE_REVIEWERS,
    MAX_REVIEWERS,
    ContractValidationError,
    REVIEWER_ORDER,
    finding_id,
    jcs_bytes,
    validate_bundle,
    validate_capabilities,
    validate_final_result,
    validate_reviewer_result,
)


FOLLOW_UP_FIELDS = (
    "residual_risks",
    "production_readiness_notes",
    "suggested_refactor_follow_ups",
    "rollout_cautions",
    "intended_behavior_changes",
    "suspected_unintended_regressions",
)
TEST_FIELDS = (
    "coverage_gaps",
    "fragile_tests",
    "highest_value_tests_to_add_first",
)

STABLE_REASONS = {
    "failed": "Reviewer dispatch failed.",
    "timed_out": "Reviewer deadline exceeded.",
    "cancelled": "Reviewer cancelled by host.",
    "not_run": "Reviewer was not invoked.",
}

ADVERSARIAL_TRIGGER_RE = re.compile(
    r"\b(?:auth|authentication|authorization|payment|payments|admin|administrator|"
    r"privilege|privileged|delete|deletion)\b|\bpublic\s+(?:api|apis|endpoint|endpoints)\b",
    re.IGNORECASE,
)


def load_fixture(name: str) -> dict[str, Any]:
    path = Path(__file__).parent / "fixtures" / "contract" / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _ordered(values: set[str]) -> list[str]:
    return sorted(values, key=lambda item: item.encode("utf-8"))


def _issue_key(issue: Mapping[str, Any]) -> bytes:
    return jcs_bytes(issue)


def _project_blockers(results: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[bytes, dict[str, Any]] = {}
    for reviewer_id in REVIEWER_ORDER:
        result = results.get(reviewer_id)
        if result is None:
            continue
        for issue in result["issues"]:
            if not issue["merge_blocking"]:
                continue
            key = _issue_key(issue)
            entry = grouped.setdefault(key, {"issue": issue, "owners": set()})
            entry["owners"].add(reviewer_id)

    blockers: list[dict[str, Any]] = []
    for entry in grouped.values():
        issue = entry["issue"]
        blocker = {
            "severity": issue["severity"],
            "title": issue["title"],
            "owners": sorted(entry["owners"], key=REVIEWER_ORDER.index),
            "file": issue["file"],
            "function": issue["function"],
            "why_it_matters": issue["why_it_matters"],
            "smallest_fix": issue["smallest_fix"],
            "finding_id": finding_id(issue),
        }
        if issue.get("location_context"):
            blocker["location_context"] = issue["location_context"]
        blockers.append(blocker)
    return sorted(
        blockers,
        key=lambda item: (
            ("critical", "high", "medium", "low").index(item["severity"]),
            item["file"].encode("utf-8"),
            item["function"].encode("utf-8"),
            item.get("location_context", "").encode("utf-8"),
            item["title"].encode("utf-8"),
            item["finding_id"],
        ),
    )


def _coverage(
    results: Mapping[str, Mapping[str, Any]],
    statuses: Mapping[str, Mapping[str, str]],
    input_status: str,
) -> tuple[list[str], list[str], list[str]]:
    questions: set[str] = set()
    follow_ups: set[str] = set()
    tests: set[str] = set()
    if input_status == "unavailable":
        questions.add("Missing review input: diff is unavailable.")

    for reviewer_id in REVIEWER_ORDER:
        status = statuses.get(reviewer_id)
        result = results.get(reviewer_id)
        if status and status["status"] != "completed":
            if reviewer_id in CORE_REVIEWERS and input_status == "available":
                questions.add(
                    f"Core reviewer {reviewer_id} ended with {status['status']}: {status['reason']}"
                )
            if reviewer_id not in CORE_REVIEWERS:
                follow_ups.add(f"{reviewer_id.title()} reviewer unavailable.")
            if status["status"] != "completed":
                continue
        if result is None:
            continue
        questions.update(result["open_questions"])
        for issue in result["issues"]:
            if issue["merge_blocking"] is False:
                follow_ups.add(issue["title"])
            tests.add(issue["test_needed"])
        for field in FOLLOW_UP_FIELDS:
            follow_ups.update(result.get(field, []))
        for field in TEST_FIELDS:
            tests.update(result.get(field, []))
    return _ordered(questions), _ordered(follow_ups), _ordered(tests)


class FakeContractAdapter:
    """Run one JSON fixture through the provider-neutral host boundary.

    The adapter is deliberately test-only. Fixture records stand in for typed
    provider events, which lets tests exercise terminal-state precedence and
    budget handling without making network, tool, or clock calls.
    """

    max_in_flight = MAX_REVIEWERS

    @staticmethod
    def _select_reviewers(bundle: Mapping[str, Any]) -> list[str]:
        """Derive the fixture route from normalized bundle trigger signals."""

        selected = list(CORE_REVIEWERS)
        if bundle["diff"]["status"] == "unavailable":
            return selected
        diff_text = bundle["diff"].get("content", bundle["diff"].get("reason", ""))
        signals = "\n".join([bundle["intent"], diff_text, *bundle["touched_files"]])
        if ADVERSARIAL_TRIGGER_RE.search(signals):
            selected.append("adversarial")
        return selected

    @staticmethod
    def _event_outcome(dispatch: Mapping[str, Any]) -> str:
        events = dispatch.get("events")
        if events is None:
            outcome = dispatch.get("outcome")
        else:
            if not isinstance(events, list) or not events:
                raise ValueError("dispatch events must be a non-empty list")
            outcome = events[0]
            if dispatch.get("timeout_cancel_tie") == "timed_out":
                outcome = "timed_out"
        if outcome == "transport":
            return "failed"
        if outcome not in {"completed", "failed", "timed_out", "cancelled", "invalid", "not_run"}:
            raise ValueError(f"unknown fake dispatch outcome: {outcome!r}")
        return outcome

    def dispatch(
        self,
        reviewer_id: str,
        bundle: Mapping[str, Any],
        dispatch: Mapping[str, Any],
        timeout_seconds: int,
    ) -> tuple[dict[str, Any] | None, dict[str, str]]:
        """Map one injected provider event to one terminal reviewer record."""

        del bundle  # The real host passes the bundle; this fake never inspects it here.
        outcome = self._event_outcome(dispatch)
        elapsed = dispatch.get("elapsed_seconds", 0)
        if not isinstance(elapsed, (int, float)) or elapsed < 0:
            raise ValueError("elapsed_seconds must be non-negative")
        if outcome == "completed" and elapsed > timeout_seconds:
            outcome = "timed_out"
        if outcome == "completed":
            try:
                result = validate_reviewer_result(dispatch.get("result"), reviewer_id)
            except ContractValidationError as error:
                path = error.path or "<root>"
                return None, {
                    "reviewer_id": reviewer_id,
                    "status": "invalid",
                    "reason": f"Reviewer result invalid: {error.code} at {path}.",
                }
            return result, {"reviewer_id": reviewer_id, "status": "completed"}
        if outcome == "invalid":
            code = dispatch.get("error_code", "schema_violation")
            path = dispatch.get("error_path", "<root>") or "<root>"
            reason = f"Reviewer result invalid: {code} at {path}."
        else:
            reason = STABLE_REASONS[outcome]
        return None, {"reviewer_id": reviewer_id, "status": outcome, "reason": reason}

    def synthesize(
        self,
        bundle: Mapping[str, Any],
        ordered_results: Mapping[str, Mapping[str, Any]],
        statuses: Mapping[str, Mapping[str, str]],
        synthesis: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Return the injected final candidate or fail closed at the root."""

        del bundle, ordered_results, statuses
        outcome = synthesis.get("outcome", "completed")
        if outcome != "completed" or "final" not in synthesis:
            raise ContractValidationError(
                "synthesis_failed", "", "synthesis produced no final candidate"
            )
        return synthesis["final"]

    def run(self, fixture: Mapping[str, Any]) -> dict[str, Any]:
        bundle = validate_bundle(fixture["bundle"])
        expected = fixture["expected"]
        selected = expected["selected_reviewers"]
        if not isinstance(selected, list):
            raise ContractValidationError(
                "schema_violation", "/expected/selected_reviewers", "selected reviewers must be an array"
            )
        if len(selected) > self.max_in_flight:
            raise ContractValidationError(
                "resource_limit",
                "/expected/selected_reviewers",
                "selected reviewers exceed the in-flight limit",
            )
        if any(reviewer_id not in REVIEWER_ORDER for reviewer_id in selected):
            raise ContractValidationError(
                "schema_violation", "/expected/selected_reviewers", "unknown reviewer identifier"
            )
        if len(set(selected)) != len(selected):
            raise ContractValidationError(
                "preflight_invalid", "/expected/selected_reviewers", "reviewer IDs must be unique"
            )
        if selected != sorted(selected, key=REVIEWER_ORDER.index):
            raise ContractValidationError(
                "cross_field_invariant", "/expected/selected_reviewers", "reviewers are not in canonical order"
            )
        if selected[: len(CORE_REVIEWERS)] != list(CORE_REVIEWERS):
            raise ContractValidationError(
                "cross_field_invariant", "/expected/selected_reviewers", "the five core reviewers are required"
            )
        derived_selected = self._select_reviewers(bundle)
        if selected != derived_selected:
            raise ContractValidationError(
                "cross_field_invariant",
                "/expected/selected_reviewers",
                "selected reviewers do not match the bundle trigger matrix",
            )
        statuses: dict[str, dict[str, str]] = {}
        results: dict[str, dict[str, Any]] = {}

        dispatch_by_id = {item["reviewer_id"]: item for item in fixture["dispatch_results"]}
        execution = fixture.get("execution", {})
        timing = validate_capabilities(execution)
        reviewer_timeout = timing["reviewer_timeout_seconds"]
        synthesis_timeout = timing["synthesis_timeout_seconds"]
        review_deadline = timing["review_deadline_seconds"]
        elapsed = 0
        if bundle["diff"]["status"] == "unavailable":
            for reviewer_id in selected:
                statuses[reviewer_id] = {
                    "reviewer_id": reviewer_id,
                    "status": "not_run",
                    "reason": "Diff unavailable; review was not invoked.",
                }
        else:
            for reviewer_id in selected:
                dispatch = dispatch_by_id.get(reviewer_id)
                if dispatch is None:
                    statuses[reviewer_id] = {
                        "reviewer_id": reviewer_id,
                        "status": "not_run",
                        "reason": STABLE_REASONS["not_run"],
                    }
                    continue
                elapsed += dispatch.get("elapsed_seconds", 0)
                if elapsed > review_deadline:
                    raise ContractValidationError(
                        "synthesis_failed", "", "review deadline expired before synthesis"
                    )
                result, status = self.dispatch(
                    reviewer_id, bundle, dispatch, reviewer_timeout
                )
                if result is not None:
                    results[reviewer_id] = result
                statuses[reviewer_id] = status

        synthesis = fixture.get("synthesis", {"outcome": "completed", "final": fixture["final"]})
        synthesis_elapsed = synthesis.get("elapsed_seconds", 0)
        if not isinstance(synthesis_elapsed, (int, float)) or synthesis_elapsed < 0:
            raise ValueError("synthesis elapsed_seconds must be non-negative")
        if synthesis_elapsed > synthesis_timeout:
            raise ContractValidationError(
                "synthesis_failed", "", "synthesis deadline exceeded"
            )
        if elapsed + synthesis_elapsed > review_deadline:
            raise ContractValidationError(
                "synthesis_failed", "", "review deadline expired before final validation"
            )
        final_candidate = self.synthesize(bundle, results, statuses, synthesis)

        expected_questions, expected_follow_ups, expected_tests = _coverage(
            results, statuses, bundle["diff"]["status"]
        )
        expected_blockers = _project_blockers(results)
        normalized = validate_final_result(
            final_candidate,
            expected_reviewers=selected,
            expected_input_status=bundle["diff"]["status"],
            expected_statuses=list(statuses.values()),
            expected_blockers=expected_blockers,
            expected_open_questions=expected_questions,
            expected_follow_ups=expected_follow_ups,
            expected_tests=expected_tests,
        )
        return normalized
