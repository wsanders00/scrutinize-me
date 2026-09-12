"""Dependency-free v1 contracts for the exported scrutinize-me skill.

This module is the reference implementation of the portable contract.  It is
deliberately free of I/O: a host owns dispatch, capability availability, and
the synthesis lifecycle, while these helpers validate and normalize the
values crossing those boundaries.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any


CONTRACT_VERSION = "1"

ERROR_CODES = frozenset(
    {
        "invalid_json",
        "unsupported_contract_version",
        "schema_violation",
        "preflight_invalid",
        "resource_limit",
        "capability_unavailable",
        "cross_field_invariant",
        "recommendation_invariant",
        "synthesis_failed",
    }
)

CORE_REVIEWERS = (
    "correctness",
    "security",
    "performance and reliability",
    "architecture and maintainability",
    "contracts and data",
)
REVIEWER_ORDER = CORE_REVIEWERS + ("adversarial", "regression", "test-quality")
REVIEWER_RANK = {name: index for index, name in enumerate(REVIEWER_ORDER)}

MERGE_RECOMMENDATIONS = ("approve", "approve with follow-ups", "request changes")
REVIEWER_STATUSES = ("completed", "failed", "timed_out", "cancelled", "invalid", "not_run")
SEVERITIES = ("critical", "high", "medium", "low")
CONFIDENCES = ("high", "medium", "low")
CAPABILITY_FLAGS = (
    "allow_commands",
    "allow_network",
    "allow_writes",
    "allow_global_install",
)
CAPABILITY_KEYS = frozenset(CAPABILITY_FLAGS + (
    "reviewer_timeout_seconds",
    "synthesis_timeout_seconds",
    "review_deadline_seconds",
))
ARTIFACT_NAMES = ("tests", "acceptance_criteria", "migrations", "logs", "screenshots")
ARTIFACT_ENCODINGS = {name: ("base64" if name == "screenshots" else "text") for name in ARTIFACT_NAMES}

MAX_BUNDLE_BYTES = 10 * 1024 * 1024
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_REVIEWER_BYTES = 512 * 1024
MAX_FINAL_BYTES = 512 * 1024
MAX_DEPTH = 16
MAX_COLLECTION = 1_000
MAX_COLLECTION_ENTRIES = 10_000
MAX_REVIEWERS = 8
MAX_ISSUES = 50
MAX_TEXT_BYTES = 64 * 1024
MAX_REASON_BYTES = 4 * 1024

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

ISSUE_REQUIRED = (
    "severity",
    "title",
    "file",
    "function",
    "confidence",
    "why_it_matters",
    "smallest_fix",
    "test_needed",
    "merge_blocking",
)
ISSUE_OPTIONAL = (
    "location_context",
    "attack_scenario",
    "mitigation_scope",
    "trigger_condition",
    "likely_impact",
    "affected_contract",
    "breakage_scenario",
    "rollout_caution",
    "reproduction_idea",
)
REVIEWER_REQUIRED = ("agent", "summary", "issues", "open_questions")
REVIEWER_OPTIONAL_ARRAYS = (
    "residual_risks",
    "production_readiness_notes",
    "suggested_refactor_follow_ups",
    "rollout_cautions",
    "intended_behavior_changes",
    "suspected_unintended_regressions",
    "coverage_gaps",
    "fragile_tests",
    "highest_value_tests_to_add_first",
)
FINAL_REQUIRED = (
    "contract_version",
    "review_completeness",
    "merge_recommendation",
    "top_must_fix_issues",
    "important_follow_ups",
    "reviewed_with_no_major_issues",
    "suggested_tests_before_merge",
    "open_questions",
    "executive_summary",
)
FINAL_BLOCKER_REQUIRED = (
    "severity",
    "title",
    "owners",
    "file",
    "function",
    "why_it_matters",
    "smallest_fix",
    "finding_id",
)


class ContractValidationError(ValueError):
    """Stable validation failure with a JSON Pointer into the rejected value."""

    def __init__(self, code: str, path: str, message: str) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown contract error code: {code}")
        self.code = code
        self.path = path
        encoded = str(message).encode("utf-8")[:512]
        self.message = encoded.decode("utf-8", errors="ignore")
        super().__init__(f"{code} at {path or '<root>'}: {self.message}")


class _DuplicateKey(ValueError):
    pass


def _pointer(path: str, component: str | int) -> str:
    value = str(component).replace("~", "~0").replace("/", "~1")
    return f"/{value}" if not path else f"{path}/{value}"


def _fail(code: str, path: str, message: str) -> None:
    raise ContractValidationError(code, path, message)


def _line_nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


def _string(value: Any, path: str, *, field: str = "value", strip: bool = False) -> str:
    if not isinstance(value, str):
        _fail("schema_violation", path, f"{field} must be a string")
    result = _line_nfc(value)
    if strip:
        result = result.strip()
    return result


def _nonempty_string(value: Any, path: str, *, field: str, strip: bool = True) -> str:
    result = _string(value, path, field=field, strip=strip)
    if not result:
        _fail("preflight_invalid", path, f"{field} must not be empty after normalization")
    return result


def _mapping(value: Any, path: str, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("schema_violation", path, f"{field} must be an object")
    if any(not isinstance(key, str) for key in value):
        _fail("schema_violation", path, f"{field} object keys must be strings")
    return dict(value)


def _keys(value: Mapping[str, Any], allowed: set[str], required: Sequence[str], path: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        _fail("schema_violation", _pointer(path, unknown[0]), "additional properties are not allowed")
    missing = [key for key in required if key not in value]
    if missing:
        _fail("schema_violation", _pointer(path, missing[0]), "required property is missing")


def _list(value: Any, path: str, *, field: str) -> list[Any]:
    if not isinstance(value, list):
        _fail("schema_violation", path, f"{field} must be an array")
    return value


def _bool(value: Any, path: str, *, field: str) -> bool:
    if type(value) is not bool:
        _fail("schema_violation", path, f"{field} must be a boolean")
    return value


def _integer(value: Any, path: str, *, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or isinstance(value, bool):
        _fail("schema_violation", path, f"{field} must be an integer")
    if not minimum <= value <= maximum:
        _fail("preflight_invalid", path, f"{field} must be between {minimum} and {maximum}")
    return value


def _utf8_size(value: str) -> int:
    return len(value.encode("utf-8"))


def _ensure_text_limit(value: str, path: str, *, reason: bool = False) -> None:
    limit = MAX_REASON_BYTES if reason else MAX_TEXT_BYTES
    if _utf8_size(value) > limit:
        _fail("resource_limit", path, "text field exceeds its resource limit")


def _json_loads(value: str | bytes, *, limit: int) -> tuple[Any, int]:
    if isinstance(value, bytes):
        raw = value
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            _fail("invalid_json", "", f"invalid UTF-8: {exc}")
    elif isinstance(value, str):
        raw = value.encode("utf-8")
    else:
        _fail("schema_violation", "", "contract input must be a mapping, UTF-8 JSON string, or UTF-8 JSON bytes")
    if len(raw) > limit:
        _fail("resource_limit", "", "raw input exceeds its resource limit")

    def pairs(pairs_list: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs_list:
            if key in result:
                raise _DuplicateKey(key)
            result[key] = item
        return result

    try:
        parsed = json.loads(value, object_pairs_hook=pairs, parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    except RecursionError:
        _fail("resource_limit", "", "maximum JSON depth exceeded")
    except (_DuplicateKey, ValueError, json.JSONDecodeError) as exc:
        _fail("invalid_json", "", f"invalid JSON: {exc}")
    return parsed, len(raw)


def _json_number(value: Any) -> str:
    if type(value) is int:
        return str(value)
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("non-finite number")
        # Contract values are integer/string/boolean based. This path keeps
        # the canonicalizer safe if a caller uses it for a general mapping.
        return json.dumps(value, allow_nan=False, separators=(",", ":"))
    raise TypeError(type(value).__name__)


def jcs_bytes(value: Any) -> bytes:
    """Return RFC 8785-compatible canonical bytes for contract JSON values.

    v1 values contain no floating-point fields. Integers are emitted directly;
    strings use JSON escaping and UTF-8, and object keys use RFC 8785's UTF-16
    code-unit ordering.
    """

    def encode(item: Any) -> str:
        if item is None:
            return "null"
        if item is True:
            return "true"
        if item is False:
            return "false"
        if type(item) in (int, float):
            return _json_number(item)
        if isinstance(item, str):
            return json.dumps(_line_nfc(item), ensure_ascii=False, separators=(",", ":"))
        if isinstance(item, Mapping):
            keys = list(item)
            if any(not isinstance(key, str) for key in keys):
                raise TypeError("object keys must be strings")
            keys.sort(key=lambda key: _line_nfc(key).encode("utf-16be"))
            return "{" + ",".join(encode(key) + ":" + encode(item[key]) for key in keys) + "}"
        if isinstance(item, (list, tuple)):
            return "[" + ",".join(encode(element) for element in item) + "]"
        raise TypeError(type(item).__name__)

    return encode(value).encode("utf-8")


def _canonical_size(value: Mapping[str, Any]) -> int:
    try:
        return len(jcs_bytes(value))
    except RecursionError:
        _fail("resource_limit", "", "maximum JSON depth exceeded")
    except (TypeError, UnicodeError, ValueError) as exc:
        _fail("schema_violation", "", f"cannot canonicalize input: {exc}")


def _resource_scan(
    value: Any,
    *,
    path: str,
    raw_size: int,
    limit: int,
    aggregate: bool,
    check_text: bool = True,
) -> None:
    if raw_size > limit:
        _fail("resource_limit", "", "raw input exceeds its resource limit")
    total = 0

    # Mapping inputs are already materialized and may be attacker-controlled.
    # Use an explicit stack so depth validation happens before the recursive
    # canonicalizer or Python's recursion limit can be reached.
    stack: list[tuple[str, Any, str, int, bool]] = [("enter", value, path, 0, False)]
    active_containers: set[int] = set()
    while stack:
        phase, item, current, depth, artifact_content = stack.pop()
        if phase == "exit":
            active_containers.remove(item)
            continue
        if depth > MAX_DEPTH:
            _fail("resource_limit", current, "maximum JSON depth exceeded")
        if isinstance(item, Mapping):
            identity = id(item)
            if identity in active_containers:
                _fail("schema_violation", current, "cyclic input is not JSON")
            if len(item) > MAX_COLLECTION:
                _fail("resource_limit", current, "object collection exceeds its limit")
            total += len(item)
            if total > MAX_COLLECTION_ENTRIES:
                _fail("resource_limit", current, "aggregate collection entries exceed their limit")
            active_containers.add(identity)
            stack.append(("exit", identity, "", 0, False))
            children = list(item.items())
            for key, child in reversed(children):
                child_path = _pointer(current, key)
                stack.append(("enter", child, child_path, depth + 1, key == "content"))
        elif isinstance(item, (list, tuple)):
            identity = id(item)
            if identity in active_containers:
                _fail("schema_violation", current, "cyclic input is not JSON")
            if len(item) > MAX_COLLECTION:
                _fail("resource_limit", current, "array collection exceeds its limit")
            total += len(item)
            if total > MAX_COLLECTION_ENTRIES:
                _fail("resource_limit", current, "aggregate collection entries exceed their limit")
            active_containers.add(identity)
            stack.append(("exit", identity, "", 0, False))
            for index in range(len(item) - 1, -1, -1):
                stack.append(("enter", item[index], _pointer(current, index), depth + 1, artifact_content))
        elif isinstance(item, str) and check_text:
            if artifact_content:
                # Artifact-specific byte limits are checked during semantic
                # validation, where the encoding and field name are known.
                continue
            _ensure_text_limit(_line_nfc(item), current)


def _prepare(value: Any, *, kind: str) -> dict[str, Any]:
    limit = MAX_BUNDLE_BYTES if kind == "bundle" else MAX_REVIEWER_BYTES if kind == "reviewer" else MAX_FINAL_BYTES
    if isinstance(value, Mapping):
        result = dict(value)
        _resource_scan(
            result,
            path="",
            raw_size=0,
            limit=limit,
            aggregate=kind == "bundle",
            check_text=False,
        )
        raw_size = _canonical_size(result)
    else:
        result, raw_size = _json_loads(value, limit=limit)
    if not isinstance(result, Mapping):
        _fail("schema_violation", "", f"{kind} must be a JSON object")
    _resource_scan(result, path="", raw_size=raw_size, limit=limit, aggregate=kind == "bundle")
    return dict(result)


def _normalized_string_list(value: Any, path: str, *, field: str) -> list[str]:
    values = _list(value, path, field=field)
    result: set[str] = set()
    for index, item in enumerate(values):
        normalized = _nonempty_string(item, _pointer(path, index), field=f"{field} item")
        _ensure_text_limit(normalized, _pointer(path, index))
        result.add(normalized)
    return sorted(result, key=lambda text: text.encode("utf-8"))


def _issue_digest(issue: Mapping[str, Any]) -> str:
    """Hash the normalized issue payload used for deterministic ordering."""

    payload = {
        key: value
        for key, value in issue.items()
        if key not in {"agent", "owners", "dedup_key", "finding_id"}
    }
    return hashlib.sha256(jcs_bytes(payload)).hexdigest()


def _sort_key_issue(issue: Mapping[str, Any]) -> tuple[Any, ...]:
    issue_digest = issue.get("finding_id") or _issue_digest(issue)
    return (
        SEVERITIES.index(issue["severity"]),
        CONFIDENCES.index(issue["confidence"]),
        issue.get("file", "").encode("utf-8"),
        issue.get("function", "").encode("utf-8"),
        issue.get("location_context", "").encode("utf-8"),
        issue.get("title", "").encode("utf-8"),
        issue_digest,
    )


def _sort_key_blocker(blocker: Mapping[str, Any]) -> tuple[Any, ...]:
    owners = blocker.get("owners", [])
    first_owner = REVIEWER_RANK.get(owners[0], len(REVIEWER_ORDER)) if owners else len(REVIEWER_ORDER)
    return (
        SEVERITIES.index(blocker["severity"]),
        blocker.get("file", "").encode("utf-8"),
        blocker.get("function", "").encode("utf-8"),
        blocker.get("location_context", "").encode("utf-8"),
        blocker.get("title", "").encode("utf-8"),
        blocker.get("finding_id", ""),
        first_owner,
    )


def _normalize_artifact(value: Any, path: str, *, field: str) -> list[dict[str, str]]:
    items = _list(value, path, field=field)
    if len(items) > MAX_COLLECTION:
        _fail("resource_limit", path, "artifact collection exceeds its limit")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    expected_encoding = ARTIFACT_ENCODINGS[field]
    for index, item in enumerate(items):
        item_path = _pointer(path, index)
        obj = _mapping(item, item_path, field=f"{field} item")
        _keys(obj, {"name", "content", "encoding"}, ("name", "content", "encoding"), item_path)
        name = _nonempty_string(obj["name"], _pointer(item_path, "name"), field="artifact name")
        content = _nonempty_string(obj["content"], _pointer(item_path, "content"), field="artifact content")
        encoding = _string(obj["encoding"], _pointer(item_path, "encoding"), field="artifact encoding")
        if encoding != expected_encoding:
            _fail("schema_violation", _pointer(item_path, "encoding"), f"encoding must be {expected_encoding}")
        if name in seen:
            _fail("preflight_invalid", _pointer(item_path, "name"), "artifact names must be unique")
        seen.add(name)
        if encoding == "text":
            payload_size = _utf8_size(content)
        else:
            try:
                decoded = base64.b64decode(content, validate=True)
            except (ValueError, binascii.Error) as exc:
                _fail("preflight_invalid", _pointer(item_path, "content"), f"invalid base64: {exc}")
            if not decoded or base64.b64encode(decoded).decode("ascii") != content:
                _fail("preflight_invalid", _pointer(item_path, "content"), "base64 must be canonical and non-empty")
            payload_size = len(decoded)
            if _utf8_size(content) > MAX_ARTIFACT_BYTES:
                _fail("resource_limit", _pointer(item_path, "content"), "encoded artifact exceeds its limit")
        if payload_size > MAX_ARTIFACT_BYTES:
            _fail("resource_limit", _pointer(item_path, "content"), "artifact exceeds its limit")
        result.append({"name": name, "content": content, "encoding": encoding})
    return sorted(result, key=lambda item: item["name"].encode("utf-8"))


def _validate_artifact_shape(value: Any, path: str, *, field: str) -> None:
    items = _list(value, path, field=field)
    expected_encoding = ARTIFACT_ENCODINGS[field]
    for index, item in enumerate(items):
        item_path = _pointer(path, index)
        obj = _mapping(item, item_path, field=f"{field} item")
        _keys(obj, {"name", "content", "encoding"}, ("name", "content", "encoding"), item_path)
        _string(obj["name"], _pointer(item_path, "name"), field="artifact name")
        _string(obj["content"], _pointer(item_path, "content"), field="artifact content")
        encoding = _string(obj["encoding"], _pointer(item_path, "encoding"), field="artifact encoding")
        if encoding != expected_encoding:
            _fail("schema_violation", _pointer(item_path, "encoding"), f"encoding must be {expected_encoding}")


def _validate_bundle_shape(obj: Mapping[str, Any]) -> None:
    allowed = {"contract_version", "diff", "touched_files", "intent", "artifact_presence", *ARTIFACT_NAMES}
    _keys(obj, allowed, ("contract_version", "diff", "touched_files", "intent", "artifact_presence"), "")
    version = obj["contract_version"]
    if not isinstance(version, str):
        _fail("schema_violation", "/contract_version", "contract_version must be a string")
    if version != CONTRACT_VERSION:
        _fail("unsupported_contract_version", "/contract_version", "unsupported contract version")

    diff = _mapping(obj["diff"], "/diff", field="diff")
    if set(diff) == {"status", "content"}:
        status = _string(diff["status"], "/diff/status", field="diff status")
        if status != "available":
            _fail("schema_violation", "/diff/status", "available diff must have status available")
        _string(diff["content"], "/diff/content", field="diff content")
    elif set(diff) == {"status", "reason"}:
        status = _string(diff["status"], "/diff/status", field="diff status")
        if status != "unavailable":
            _fail("schema_violation", "/diff/status", "unavailable diff must have status unavailable")
        _string(diff["reason"], "/diff/reason", field="diff reason")
    else:
        _fail("schema_violation", "/diff", "diff must be exactly status/content or status/reason")

    touched_values = _list(obj["touched_files"], "/touched_files", field="touched_files")
    for index, item in enumerate(touched_values):
        _string(item, _pointer("/touched_files", index), field="touched_files item")
    _string(obj["intent"], "/intent", field="intent")

    presence = _mapping(obj["artifact_presence"], "/artifact_presence", field="artifact_presence")
    _keys(presence, set(ARTIFACT_NAMES), ARTIFACT_NAMES, "/artifact_presence")
    for field in ARTIFACT_NAMES:
        state = _string(presence[field], _pointer("/artifact_presence", field), field="artifact presence")
        if state not in ("present", "absent"):
            _fail("schema_violation", _pointer("/artifact_presence", field), "presence must be present or absent")
        if field in obj:
            _validate_artifact_shape(obj[field], _pointer("", field), field=field)


def validate_bundle(value: Any) -> dict[str, Any]:
    """Validate and normalize a v1 review bundle."""

    obj = _prepare(value, kind="bundle")
    _validate_bundle_shape(obj)

    diff = _mapping(obj["diff"], "/diff", field="diff")
    if set(diff) == {"status", "content"}:
        status = _string(diff["status"], "/diff/status", field="diff status")
        if status != "available":
            _fail("schema_violation", "/diff/status", "available diff must have status available")
        content = _nonempty_string(diff["content"], "/diff/content", field="diff content")
        if _utf8_size(content) > MAX_ARTIFACT_BYTES:
            _fail("resource_limit", "/diff/content", "diff content exceeds its limit")
        normalized_diff = {"status": status, "content": content}
    elif set(diff) == {"status", "reason"}:
        status = _string(diff["status"], "/diff/status", field="diff status")
        if status != "unavailable":
            _fail("schema_violation", "/diff/status", "unavailable diff must have status unavailable")
        reason = _nonempty_string(diff["reason"], "/diff/reason", field="diff reason")
        _ensure_text_limit(reason, "/diff/reason")
        normalized_diff = {"status": status, "reason": reason}
    else:
        _fail("schema_violation", "/diff", "diff must be exactly status/content or status/reason")

    touched_values = _list(obj["touched_files"], "/touched_files", field="touched_files")
    touched: list[str] = []
    for index, item in enumerate(touched_values):
        normalized = _nonempty_string(item, _pointer("/touched_files", index), field="touched_files item")
        _ensure_text_limit(normalized, _pointer("/touched_files", index))
        touched.append(normalized.replace("\\", "/"))
    if not touched:
        _fail("preflight_invalid", "/touched_files", "touched_files must not be empty")
    if len(set(touched)) != len(touched):
        _fail("preflight_invalid", "/touched_files", "touched_files must be unique after path normalization")
    touched.sort(key=lambda text: text.encode("utf-8"))
    intent = _nonempty_string(obj["intent"], "/intent", field="intent")
    _ensure_text_limit(intent, "/intent")

    presence = _mapping(obj["artifact_presence"], "/artifact_presence", field="artifact_presence")
    _keys(presence, set(ARTIFACT_NAMES), ARTIFACT_NAMES, "/artifact_presence")
    normalized: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "diff": normalized_diff,
        "touched_files": touched,
        "intent": intent,
    }
    artifacts: dict[str, list[dict[str, str]]] = {}
    for field in ARTIFACT_NAMES:
        if field in obj:
            artifacts[field] = _normalize_artifact(obj[field], _pointer("", field), field=field)
        else:
            artifacts[field] = []
        state = _string(presence[field], _pointer("/artifact_presence", field), field="artifact presence")
        if state not in ("present", "absent"):
            _fail("schema_violation", _pointer("/artifact_presence", field), "presence must be present or absent")
        actual = bool(artifacts[field])
        if (state == "present") != actual:
            _fail("cross_field_invariant", _pointer("/artifact_presence", field), "presence does not match artifact content")
    normalized["artifact_presence"] = {field: presence[field] for field in ARTIFACT_NAMES}
    for field in ARTIFACT_NAMES:
        if artifacts[field]:
            normalized[field] = artifacts[field]
    return normalized


def _validate_string_array_shape(value: Any, path: str, *, field: str) -> None:
    items = _list(value, path, field=field)
    for index, item in enumerate(items):
        _string(item, _pointer(path, index), field=f"{field} item")


def _validate_issue_shape(value: Any, path: str) -> None:
    issue = _mapping(value, path, field="issue")
    allowed = set(ISSUE_REQUIRED + ISSUE_OPTIONAL)
    _keys(issue, allowed, ISSUE_REQUIRED, path)
    for key in ISSUE_REQUIRED:
        item_path = _pointer(path, key)
        if key == "merge_blocking":
            _bool(issue[key], item_path, field=key)
            continue
        text = _string(issue[key], item_path, field=key)
        if key == "severity" and text not in SEVERITIES:
            _fail("schema_violation", item_path, "invalid severity")
        if key == "confidence" and text not in CONFIDENCES:
            _fail("schema_violation", item_path, "invalid confidence")
    for key in ISSUE_OPTIONAL:
        if key not in issue:
            continue
        item_path = _pointer(path, key)
        text = _string(issue[key], item_path, field=key)
        if key == "mitigation_scope" and text not in {"code", "config", "infra"}:
            _fail("schema_violation", item_path, "invalid mitigation scope")


def _validate_reviewer_shape(value: Any) -> None:
    obj = _mapping(value, "", field="reviewer result")
    allowed = set(REVIEWER_REQUIRED + REVIEWER_OPTIONAL_ARRAYS)
    _keys(obj, allowed, REVIEWER_REQUIRED, "")
    agent = _string(obj["agent"], "/agent", field="agent")
    if agent not in REVIEWER_ORDER:
        _fail("schema_violation", "/agent", "unknown reviewer identifier")
    _string(obj["summary"], "/summary", field="summary")
    issues = _list(obj["issues"], "/issues", field="issues")
    for index, item in enumerate(issues):
        _validate_issue_shape(item, _pointer("/issues", index))
    _validate_string_array_shape(obj["open_questions"], "/open_questions", field="open_questions")
    for field in REVIEWER_OPTIONAL_ARRAYS:
        if field in obj:
            _validate_string_array_shape(obj[field], _pointer("", field), field=field)


def _normalize_issue(value: Any, path: str) -> dict[str, Any]:
    issue = _mapping(value, path, field="issue")
    allowed = set(ISSUE_REQUIRED + ISSUE_OPTIONAL)
    _keys(issue, allowed, ISSUE_REQUIRED, path)
    result: dict[str, Any] = {}
    for key in ISSUE_REQUIRED:
        item_path = _pointer(path, key)
        if key == "severity":
            result[key] = _string(issue[key], item_path, field=key)
            if result[key] not in SEVERITIES:
                _fail("schema_violation", item_path, "invalid severity")
        elif key == "confidence":
            result[key] = _string(issue[key], item_path, field=key)
            if result[key] not in CONFIDENCES:
                _fail("schema_violation", item_path, "invalid confidence")
        elif key == "merge_blocking":
            result[key] = _bool(issue[key], item_path, field=key)
        else:
            if key in ("file", "function"):
                result[key] = _string(issue[key], item_path, field=key, strip=True)
            else:
                result[key] = _nonempty_string(issue[key], item_path, field=key)
            _ensure_text_limit(result[key], item_path)
    for key in ISSUE_OPTIONAL:
        if key not in issue:
            continue
        result[key] = _nonempty_string(issue[key], _pointer(path, key), field=key)
        _ensure_text_limit(result[key], _pointer(path, key))
    if "mitigation_scope" in result and result["mitigation_scope"] not in {"code", "config", "infra"}:
        _fail("schema_violation", _pointer(path, "mitigation_scope"), "invalid mitigation scope")
    result["file"] = result["file"].replace("\\", "/")
    return result


def _normalize_reviewer(value: Any) -> dict[str, Any]:
    obj = _prepare(value, kind="reviewer")
    _validate_reviewer_shape(obj)
    allowed = set(REVIEWER_REQUIRED + REVIEWER_OPTIONAL_ARRAYS)
    _keys(obj, allowed, REVIEWER_REQUIRED, "")
    agent = _nonempty_string(obj["agent"], "/agent", field="agent")
    if agent not in REVIEWER_ORDER:
        _fail("schema_violation", "/agent", "unknown reviewer identifier")
    result: dict[str, Any] = {"agent": agent}
    result["summary"] = _nonempty_string(obj["summary"], "/summary", field="summary", strip=False)
    _ensure_text_limit(result["summary"], "/summary")
    issues = _list(obj["issues"], "/issues", field="issues")
    if len(issues) > MAX_ISSUES:
        _fail("resource_limit", "/issues", "issue collection exceeds its limit")
    result["issues"] = [_normalize_issue(item, _pointer("/issues", index)) for index, item in enumerate(issues)]
    result["issues"].sort(key=_sort_key_issue)
    result["open_questions"] = _normalized_string_list(obj["open_questions"], "/open_questions", field="open_questions")
    for field in REVIEWER_OPTIONAL_ARRAYS:
        if field in obj:
            result[field] = _normalized_string_list(obj[field], _pointer("", field), field=field)
    return result


def validate_reviewer_result(value: Any, expected_agent: str) -> dict[str, Any]:
    """Validate a reviewer result and require the host-routed reviewer ID."""

    if expected_agent not in REVIEWER_ORDER:
        raise ValueError("expected_agent must be a known reviewer identifier")
    result = _normalize_reviewer(value)
    if result["agent"] != expected_agent:
        _fail("cross_field_invariant", "/agent", "reviewer result agent does not match routed reviewer")
    return result


def _trusted_names(value: Any, *, field: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{field} must be a trusted sequence of strings")
    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise TypeError(f"{field}[{index}] must be a string")
        item = _line_nfc(item)
        if item not in CAPABILITY_FLAGS:
            _fail("schema_violation", _pointer(f"/{field}", index), "unknown capability name")
        if item in seen:
            _fail("schema_violation", _pointer(f"/{field}", index), "duplicate capability name")
        seen.add(item)
        result.append(item)
    if [CAPABILITY_FLAGS.index(item) for item in result] != sorted(CAPABILITY_FLAGS.index(item) for item in result):
        _fail("schema_violation", f"/{field}", "capabilities must use canonical order")
    return result


def validate_capabilities(
    value: Any,
    required_capabilities: Sequence[str] = (),
    available_capabilities: Sequence[str] = (),
) -> dict[str, Any]:
    """Validate trusted host capability configuration without invoking it."""

    obj = _prepare(value, kind="final")
    _keys(obj, set(CAPABILITY_KEYS), (), "")
    normalized: dict[str, Any] = {}
    for flag in CAPABILITY_FLAGS:
        if flag in obj:
            normalized[flag] = _bool(obj[flag], _pointer("", flag), field=flag)
        else:
            normalized[flag] = False
    normalized["reviewer_timeout_seconds"] = _integer(obj.get("reviewer_timeout_seconds", 60), "/reviewer_timeout_seconds", field="reviewer_timeout_seconds", minimum=1, maximum=300)
    normalized["synthesis_timeout_seconds"] = _integer(obj.get("synthesis_timeout_seconds", 60), "/synthesis_timeout_seconds", field="synthesis_timeout_seconds", minimum=1, maximum=300)
    normalized["review_deadline_seconds"] = _integer(obj.get("review_deadline_seconds", 300), "/review_deadline_seconds", field="review_deadline_seconds", minimum=1, maximum=900)
    required = _trusted_names(required_capabilities, field="required_capabilities")
    available = set(_trusted_names(available_capabilities, field="available_capabilities"))
    for name in required:
        if not normalized[name] or name not in available:
            _fail("capability_unavailable", _pointer("/required_capabilities", name), "required capability is unavailable")
    return normalized


def _normalize_final_blocker(value: Any, path: str) -> dict[str, Any]:
    blocker = _mapping(value, path, field="final blocker")
    allowed = set(FINAL_BLOCKER_REQUIRED) | {"location_context"}
    _keys(blocker, allowed, FINAL_BLOCKER_REQUIRED, path)
    result: dict[str, Any] = {}
    for key in FINAL_BLOCKER_REQUIRED:
        item_path = _pointer(path, key)
        if key == "severity":
            result[key] = _string(blocker[key], item_path, field=key)
            if result[key] not in SEVERITIES:
                _fail("schema_violation", item_path, "invalid severity")
        elif key == "owners":
            owners = _list(blocker[key], item_path, field="owners")
            if not owners:
                _fail("preflight_invalid", item_path, "owners must not be empty")
            result[key] = [_nonempty_string(owner, _pointer(item_path, index), field="owner") for index, owner in enumerate(owners)]
            result[key] = sorted(set(result[key]), key=lambda name: REVIEWER_RANK.get(name, len(REVIEWER_ORDER)))
            if any(owner not in REVIEWER_ORDER for owner in result[key]):
                _fail("schema_violation", item_path, "unknown owner reviewer")
        elif key == "finding_id":
            result[key] = _string(blocker[key], item_path, field=key)
            if not _HEX64.fullmatch(result[key]):
                _fail("schema_violation", item_path, "finding_id must be a lowercase SHA-256 hex digest")
        else:
            if key in ("file", "function"):
                result[key] = _string(blocker[key], item_path, field=key, strip=True)
            else:
                result[key] = _nonempty_string(blocker[key], item_path, field=key)
            _ensure_text_limit(result[key], item_path)
    if "location_context" in blocker:
        result["location_context"] = _nonempty_string(blocker["location_context"], _pointer(path, "location_context"), field="location_context")
        _ensure_text_limit(result["location_context"], _pointer(path, "location_context"))
    result["file"] = result["file"].replace("\\", "/")
    return result


def _validate_reviewer_status_shape(value: Any, path: str) -> None:
    record = _mapping(value, path, field="reviewer status")
    _keys(record, {"reviewer_id", "status", "reason"}, ("reviewer_id", "status"), path)
    _string(record["reviewer_id"], _pointer(path, "reviewer_id"), field="reviewer ID")
    status = _string(record["status"], _pointer(path, "status"), field="status")
    if status not in REVIEWER_STATUSES:
        _fail("schema_violation", _pointer(path, "status"), "invalid reviewer status")
    if "reason" in record:
        _string(record["reason"], _pointer(path, "reason"), field="reason")


def _validate_completeness_shape(value: Any) -> None:
    obj = _mapping(value, "/review_completeness", field="review_completeness")
    allowed = {"input_status", "status", "blocking", "selected_reviewers", "reviewer_statuses"}
    _keys(
        obj,
        allowed,
        ("input_status", "status", "blocking", "selected_reviewers", "reviewer_statuses"),
        "/review_completeness",
    )
    input_status = _string(
        obj["input_status"],
        "/review_completeness/input_status",
        field="input_status",
    )
    if input_status not in ("available", "unavailable"):
        _fail("schema_violation", "/review_completeness/input_status", "invalid input status")
    status = _string(obj["status"], "/review_completeness/status", field="status")
    if status not in ("complete", "incomplete"):
        _fail("schema_violation", "/review_completeness/status", "invalid completeness status")
    _bool(obj["blocking"], "/review_completeness/blocking", field="blocking")
    _validate_string_array_shape(
        obj["selected_reviewers"],
        "/review_completeness/selected_reviewers",
        field="selected reviewers",
    )
    statuses = _list(
        obj["reviewer_statuses"],
        "/review_completeness/reviewer_statuses",
        field="reviewer statuses",
    )
    for index, item in enumerate(statuses):
        _validate_reviewer_status_shape(
            item,
            _pointer("/review_completeness/reviewer_statuses", index),
        )


def _validate_final_blocker_shape(value: Any, path: str) -> None:
    blocker = _mapping(value, path, field="final blocker")
    allowed = set(FINAL_BLOCKER_REQUIRED) | {"location_context"}
    _keys(blocker, allowed, FINAL_BLOCKER_REQUIRED, path)
    for key in FINAL_BLOCKER_REQUIRED:
        item_path = _pointer(path, key)
        if key == "owners":
            _validate_string_array_shape(blocker[key], item_path, field="owners")
        else:
            text = _string(blocker[key], item_path, field=key)
            if key == "severity" and text not in SEVERITIES:
                _fail("schema_violation", item_path, "invalid severity")
            if key == "finding_id" and not _HEX64.fullmatch(text):
                _fail("schema_violation", item_path, "finding_id must be a lowercase SHA-256 hex digest")
    if "location_context" in blocker:
        _string(blocker["location_context"], _pointer(path, "location_context"), field="location_context")


def _validate_final_shape(value: Any) -> None:
    obj = _mapping(value, "", field="final result")
    _keys(obj, set(FINAL_REQUIRED), FINAL_REQUIRED, "")
    _string(obj["contract_version"], "/contract_version", field="contract_version")
    _validate_completeness_shape(obj["review_completeness"])
    recommendation = _string(
        obj["merge_recommendation"],
        "/merge_recommendation",
        field="merge_recommendation",
    )
    if recommendation not in MERGE_RECOMMENDATIONS:
        _fail("schema_violation", "/merge_recommendation", "invalid merge recommendation")
    blockers = _list(obj["top_must_fix_issues"], "/top_must_fix_issues", field="top blockers")
    for index, item in enumerate(blockers):
        _validate_final_blocker_shape(item, _pointer("/top_must_fix_issues", index))
    for field in (
        "important_follow_ups",
        "reviewed_with_no_major_issues",
        "suggested_tests_before_merge",
        "open_questions",
    ):
        _validate_string_array_shape(obj[field], _pointer("", field), field=field)
    _string(obj["executive_summary"], "/executive_summary", field="executive_summary")


def _normalize_completeness(value: Any) -> dict[str, Any]:
    obj = _mapping(value, "/review_completeness", field="review_completeness")
    allowed = {"input_status", "status", "blocking", "selected_reviewers", "reviewer_statuses"}
    _keys(
        obj,
        allowed,
        ("input_status", "status", "blocking", "selected_reviewers", "reviewer_statuses"),
        "/review_completeness",
    )
    input_status = _string(obj["input_status"], "/review_completeness/input_status", field="input_status")
    status = _string(obj["status"], "/review_completeness/status", field="status")
    if input_status not in ("available", "unavailable"):
        _fail("schema_violation", "/review_completeness/input_status", "invalid input status")
    if status not in ("complete", "incomplete"):
        _fail("schema_violation", "/review_completeness/status", "invalid completeness status")
    blocking = _bool(obj["blocking"], "/review_completeness/blocking", field="blocking")
    selected = _list(obj["selected_reviewers"], "/review_completeness/selected_reviewers", field="selected_reviewers")
    if not selected or len(selected) > MAX_REVIEWERS:
        _fail("preflight_invalid", "/review_completeness/selected_reviewers", "selected reviewers count is invalid")
    selected_ids = [_nonempty_string(item, _pointer("/review_completeness/selected_reviewers", index), field="reviewer ID") for index, item in enumerate(selected)]
    if any(item not in REVIEWER_ORDER for item in selected_ids):
        _fail("schema_violation", "/review_completeness/selected_reviewers", "unknown reviewer identifier")
    if len(set(selected_ids)) != len(selected_ids):
        _fail("preflight_invalid", "/review_completeness/selected_reviewers", "reviewer IDs must be unique")
    if selected_ids != sorted(selected_ids, key=REVIEWER_RANK.__getitem__):
        _fail("cross_field_invariant", "/review_completeness/selected_reviewers", "reviewers are not in canonical order")
    if selected_ids[: len(CORE_REVIEWERS)] != list(CORE_REVIEWERS):
        _fail("cross_field_invariant", "/review_completeness/selected_reviewers", "the five core reviewers are required")
    statuses = _list(obj["reviewer_statuses"], "/review_completeness/reviewer_statuses", field="reviewer_statuses")
    if len(statuses) != len(selected_ids):
        _fail("cross_field_invariant", "/review_completeness/reviewer_statuses", "one status is required per selected reviewer")
    normalized_statuses: list[dict[str, str]] = []
    for index, item in enumerate(statuses):
        path = _pointer("/review_completeness/reviewer_statuses", index)
        record = _mapping(item, path, field="reviewer status")
        _keys(record, {"reviewer_id", "status", "reason"}, ("reviewer_id", "status"), path)
        reviewer_id = _nonempty_string(record["reviewer_id"], _pointer(path, "reviewer_id"), field="reviewer ID")
        state = _string(record["status"], _pointer(path, "status"), field="status")
        if reviewer_id != selected_ids[index]:
            _fail("cross_field_invariant", _pointer(path, "reviewer_id"), "status reviewer does not match selected reviewer")
        if state not in REVIEWER_STATUSES:
            _fail("schema_violation", _pointer(path, "status"), "invalid reviewer status")
        record_result: dict[str, str] = {"reviewer_id": reviewer_id, "status": state}
        if state != "completed":
            if "reason" not in record:
                _fail("schema_violation", _pointer(path, "reason"), "reason is required for non-completed status")
            reason = _nonempty_string(record["reason"], _pointer(path, "reason"), field="reason")
            if _utf8_size(reason) > MAX_REASON_BYTES:
                _fail("resource_limit", _pointer(path, "reason"), "status reason exceeds its limit")
            record_result["reason"] = reason
        elif "reason" in record:
            _fail("schema_violation", _pointer(path, "reason"), "completed status must not include a reason")
        normalized_statuses.append(record_result)
    return {
        "input_status": input_status,
        "status": status,
        "blocking": blocking,
        "selected_reviewers": selected_ids,
        "reviewer_statuses": normalized_statuses,
    }


def _normalize_expected_status_record(value: Any, path: str) -> dict[str, str]:
    item = _mapping(value, path, field="expected status")
    _keys(item, {"status", "reason"}, ("status",), path)
    status = _string(item["status"], _pointer(path, "status"), field="expected status")
    if status not in REVIEWER_STATUSES:
        _fail("cross_field_invariant", "/review_completeness", "expected statuses have an invalid status")
    result = {"status": status}
    if status == "completed":
        if "reason" in item:
            _fail(
                "cross_field_invariant",
                "/review_completeness",
                "completed expected status must not include a reason",
            )
    else:
        if "reason" not in item:
            _fail(
                "cross_field_invariant",
                "/review_completeness",
                "non-completed expected status requires a reason",
            )
        reason = _nonempty_string(
            item["reason"],
            _pointer(path, "reason"),
            field="expected status reason",
        )
        if _utf8_size(reason) > MAX_REASON_BYTES:
            _fail("cross_field_invariant", "/review_completeness", "expected status reason exceeds its limit")
        result["reason"] = reason
    return result


def _expected_status_records(
    value: Any,
) -> dict[str, dict[str, str]] | list[dict[str, str]]:
    if isinstance(value, Mapping):
        result: dict[str, dict[str, str]] = {}
        for reviewer_id, expected in value.items():
            if not isinstance(reviewer_id, str):
                _fail("cross_field_invariant", "/review_completeness", "expected reviewer IDs must be strings")
            if isinstance(expected, str):
                _fail(
                    "cross_field_invariant",
                    "/review_completeness",
                    "expected statuses require complete status records, including reasons",
                )
            path = _pointer("/expected_statuses", reviewer_id)
            result[reviewer_id] = _normalize_expected_status_record(expected, path)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        result_list: list[dict[str, str]] = []
        seen: set[str] = set()
        for index, record in enumerate(value):
            path = _pointer("/expected_statuses", index)
            item = _mapping(record, path, field="expected status")
            _keys(item, {"reviewer_id", "status", "reason"}, ("reviewer_id", "status"), path)
            reviewer_id = _nonempty_string(
                item["reviewer_id"],
                _pointer(path, "reviewer_id"),
                field="expected reviewer ID",
            )
            if reviewer_id in seen:
                _fail("cross_field_invariant", "/review_completeness", "expected reviewer IDs must be unique")
            seen.add(reviewer_id)
            expected_record = _normalize_expected_status_record(
                {key: item[key] for key in ("status", "reason") if key in item},
                path,
            )
            result_list.append({"reviewer_id": reviewer_id, **expected_record})
        return result_list
    _fail("cross_field_invariant", "/review_completeness", "expected statuses must be an object or array")


def _compare_expected_statuses(
    actual: Sequence[Mapping[str, str]],
    expected: Mapping[str, Mapping[str, str]] | Sequence[Mapping[str, str]],
) -> None:
    if isinstance(expected, Sequence) and not isinstance(expected, (str, bytes)):
        if len(actual) != len(expected) or any(
            dict(actual_record) != dict(expected_record)
            for actual_record, expected_record in zip(actual, expected)
        ):
            _fail(
                "cross_field_invariant",
                "/review_completeness/reviewer_statuses",
                "reviewer statuses do not match host-derived statuses",
            )
        return
    actual_map = {record["reviewer_id"]: dict(record) for record in actual}
    if set(actual_map) != set(expected):
        _fail("cross_field_invariant", "/review_completeness/reviewer_statuses", "reviewer statuses do not match host-derived statuses")
    for reviewer_id, expected_record in expected.items():
        actual_record = actual_map[reviewer_id]
        expected_full = {"reviewer_id": reviewer_id, **expected_record}
        if actual_record != expected_full:
            _fail("cross_field_invariant", "/review_completeness/reviewer_statuses", "reviewer statuses do not match host-derived statuses")


def _compare_expected(actual: Any, expected: Any, path: str, *, label: str) -> None:
    try:
        equal = jcs_bytes(actual) == jcs_bytes(expected)
    except (TypeError, ValueError):
        equal = actual == expected
    if not equal:
        _fail("cross_field_invariant", path, f"{label} does not match host-derived coverage")


def _expected_blockers(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        _fail("cross_field_invariant", "/top_must_fix_issues", "expected blockers must be an array")
    return [_normalize_final_blocker(item, _pointer("/top_must_fix_issues", index)) for index, item in enumerate(value)]


def validate_final_result(
    value: Any,
    expected_reviewers: Sequence[str] | None = None,
    expected_input_status: str | None = None,
    expected_statuses: Mapping[str, Mapping[str, str]] | Sequence[Mapping[str, str]] | None = None,
    expected_blockers: Sequence[Mapping[str, Any]] | None = None,
    expected_open_questions: Sequence[str] | None = None,
    expected_follow_ups: Sequence[str] | None = None,
    expected_tests: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Validate, normalize, and cross-check a synthesized final result."""

    obj = _prepare(value, kind="final")
    _keys(obj, set(FINAL_REQUIRED), FINAL_REQUIRED, "")
    version = obj["contract_version"]
    if not isinstance(version, str):
        _fail("schema_violation", "/contract_version", "contract_version must be a string")
    if version != CONTRACT_VERSION:
        _fail("unsupported_contract_version", "/contract_version", "unsupported contract version")
    _validate_final_shape(obj)
    completeness = _normalize_completeness(obj["review_completeness"])
    result: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "review_completeness": completeness,
    }
    recommendation = _string(obj["merge_recommendation"], "/merge_recommendation", field="merge_recommendation")
    if recommendation not in MERGE_RECOMMENDATIONS:
        _fail("schema_violation", "/merge_recommendation", "invalid merge recommendation")
    result["merge_recommendation"] = recommendation
    blockers = [_normalize_final_blocker(item, _pointer("/top_must_fix_issues", index)) for index, item in enumerate(_list(obj["top_must_fix_issues"], "/top_must_fix_issues", field="top_must_fix_issues"))]
    if len(blockers) > MAX_ISSUES:
        _fail("resource_limit", "/top_must_fix_issues", "blocker collection exceeds its limit")
    result["top_must_fix_issues"] = sorted(blockers, key=_sort_key_blocker)
    result["important_follow_ups"] = _normalized_string_list(obj["important_follow_ups"], "/important_follow_ups", field="important_follow_ups")
    result["reviewed_with_no_major_issues"] = [_nonempty_string(item, _pointer("/reviewed_with_no_major_issues", index), field="reviewer ID") for index, item in enumerate(_list(obj["reviewed_with_no_major_issues"], "/reviewed_with_no_major_issues", field="reviewed_with_no_major_issues"))]
    if any(item not in REVIEWER_ORDER for item in result["reviewed_with_no_major_issues"]):
        _fail("schema_violation", "/reviewed_with_no_major_issues", "unknown reviewer identifier")
    result["reviewed_with_no_major_issues"] = sorted(
        set(result["reviewed_with_no_major_issues"]), key=REVIEWER_RANK.__getitem__
    )
    result["suggested_tests_before_merge"] = _normalized_string_list(obj["suggested_tests_before_merge"], "/suggested_tests_before_merge", field="suggested_tests_before_merge")
    result["open_questions"] = _normalized_string_list(obj["open_questions"], "/open_questions", field="open_questions")
    result["executive_summary"] = _nonempty_string(obj["executive_summary"], "/executive_summary", field="executive_summary", strip=False)
    _ensure_text_limit(result["executive_summary"], "/executive_summary")

    selected = completeness["selected_reviewers"]
    statuses = {record["reviewer_id"]: record["status"] for record in completeness["reviewer_statuses"]}
    for index, reviewer_id in enumerate(result["reviewed_with_no_major_issues"]):
        path = _pointer("/reviewed_with_no_major_issues", index)
        if reviewer_id not in selected:
            _fail("cross_field_invariant", path, "reviewer was not selected")
        if statuses[reviewer_id] != "completed":
            _fail("cross_field_invariant", path, "reviewer did not complete")
    core_failed = any(reviewer in statuses and statuses[reviewer] != "completed" for reviewer in CORE_REVIEWERS)
    optional_failed = any(reviewer not in CORE_REVIEWERS and state != "completed" for reviewer, state in statuses.items())
    if completeness["input_status"] == "unavailable":
        if selected != list(CORE_REVIEWERS):
            _fail("recommendation_invariant", "/review_completeness/selected_reviewers", "unavailable input routes exactly the five core reviewers")
        for index, record in enumerate(completeness["reviewer_statuses"]):
            if record["status"] != "not_run" or record.get("reason") != "Diff unavailable; review was not invoked.":
                _fail("recommendation_invariant", _pointer("/review_completeness/reviewer_statuses", index), "unavailable input requires exact not_run statuses")
    # `blocking` describes incomplete review evidence.  A completed review can
    # still require changes because it found a merge-blocking issue; that is a
    # recommendation concern, not an execution-completeness concern.
    blocking = completeness["input_status"] == "unavailable" or core_failed or bool(result["open_questions"])
    expected_status = "complete" if completeness["input_status"] == "available" and not any(state != "completed" for state in statuses.values()) and not result["open_questions"] else "incomplete"
    if completeness["status"] != expected_status or completeness["blocking"] != blocking:
        _fail("recommendation_invariant", "/review_completeness", "completeness envelope contradicts its derived state")
    if completeness["input_status"] == "unavailable":
        required_question = "Missing review input: diff is unavailable."
        if required_question not in result["open_questions"]:
            _fail("recommendation_invariant", "/open_questions", "unavailable input requires the missing-input question")
        if not result["executive_summary"].startswith("Review incomplete: diff unavailable; approval is prohibited."):
            _fail("recommendation_invariant", "/executive_summary", "unavailable input requires the fixed summary prefix")
    elif core_failed:
        if not result["executive_summary"].startswith(
            "Review incomplete: core reviewer failure; approval is prohibited."
        ):
            _fail("recommendation_invariant", "/executive_summary", "core failure requires the fixed summary prefix")
    elif result["open_questions"]:
        if not result["executive_summary"].startswith(
            "Review incomplete: blocking questions remain; approval is prohibited."
        ):
            _fail("recommendation_invariant", "/executive_summary", "blocking questions require the fixed summary prefix")

    if blocking or result["top_must_fix_issues"]:
        required_recommendation = "request changes"
    elif optional_failed or result["important_follow_ups"] or result["suggested_tests_before_merge"]:
        required_recommendation = "approve with follow-ups"
    else:
        required_recommendation = "approve"
    if recommendation != required_recommendation:
        _fail("recommendation_invariant", "/merge_recommendation", "recommendation does not match completeness and coverage")

    if expected_reviewers is not None:
        _compare_expected(selected, list(expected_reviewers), "/review_completeness/selected_reviewers", label="selected reviewers")
    if expected_input_status is not None and completeness["input_status"] != expected_input_status:
        _fail("cross_field_invariant", "/review_completeness/input_status", "input status does not match host context")
    if expected_statuses is not None:
        _compare_expected_statuses(completeness["reviewer_statuses"], _expected_status_records(expected_statuses))
    if expected_blockers is not None:
        _compare_expected(result["top_must_fix_issues"], _expected_blockers(expected_blockers), "/top_must_fix_issues", label="blockers")
    if expected_open_questions is not None:
        _compare_expected(result["open_questions"], _normalized_string_list(list(expected_open_questions), "/open_questions", field="expected open questions"), "/open_questions", label="open questions")
    if expected_follow_ups is not None:
        _compare_expected(result["important_follow_ups"], _normalized_string_list(list(expected_follow_ups), "/important_follow_ups", field="expected follow-ups"), "/important_follow_ups", label="follow-ups")
    if expected_tests is not None:
        _compare_expected(result["suggested_tests_before_merge"], _normalized_string_list(list(expected_tests), "/suggested_tests_before_merge", field="expected tests"), "/suggested_tests_before_merge", label="tests")
    return result


def finding_id(issue: Mapping[str, Any]) -> str:
    """Return the deterministic SHA-256 ID for a normalized reviewer issue."""

    normalized = _normalize_issue(dict(issue), "")
    return _issue_digest(normalized)


__all__ = [
    "CONTRACT_VERSION",
    "ContractValidationError",
    "ERROR_CODES",
    "finding_id",
    "jcs_bytes",
    "validate_bundle",
    "validate_capabilities",
    "validate_final_result",
    "validate_reviewer_result",
]
