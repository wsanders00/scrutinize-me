import json
from pathlib import Path
import re
import sys
import unittest

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scrutinize_me_skill import __version__


SKILL_ROOT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "scrutinize_me_skill"
    / "skill"
    / "scrutinize-me"
)
SHARED_ISSUE_KEYS = [
    "severity",
    "title",
    "file",
    "function",
    "confidence",
    "why_it_matters",
    "smallest_fix",
    "test_needed",
]


class SkillContentTests(unittest.TestCase):
    def _section_text(self, text: str, heading: str) -> str:
        self.assertIn(heading, text, f"Missing heading {heading}")
        section_start = text.index(heading)
        next_heading_idx = text.find("\n### ", section_start + 1)
        next_major_idx = text.find("\n## ", section_start + 1)
        boundaries = [idx for idx in (next_heading_idx, next_major_idx) if idx != -1]
        section_end = min(boundaries) if boundaries else len(text)
        return text[section_start:section_end]

    def _output_body(self, text: str, heading: str) -> str:
        section_text = self._section_text(text, heading)
        self.assertIn("Output:", section_text, f"Missing Output block for {heading}")
        return section_text.split("Output:", 1)[1]

    def _assert_json_only_output_rules(self, text: str) -> None:
        lowered = text.lower()
        self.assertIn("references/output-schema.md", text)
        self.assertRegex(lowered, r"return .*json (?:object|only)")
        self.assertIn("do not include", lowered)
        self.assertIn("markdown", lowered)
        self.assertIn("code fences", lowered)
        self.assertIn("commentary", lowered)
        self.assertIn("outside the json object", lowered)

    def _assert_shared_key_contract(self, text: str) -> None:
        lowered = text.lower()
        self.assertIn("references/output-schema.md", text)
        if re.search(r"shared (?:required fields|issue keys)", lowered):
            return
        for key in SHARED_ISSUE_KEYS:
            self.assertIn(key, text, f"Missing shared issue key {key}")

    def test_skill_references_orchestrator_docs(self) -> None:
        self.assertTrue((SKILL_ROOT / "references" / "reviewer-personas.md").exists())
        self.assertTrue((SKILL_ROOT / "references" / "orchestrator-playbook.md").exists())
        self.assertTrue((SKILL_ROOT / "references" / "output-schema.md").exists())

    def test_core_persona_prompts_require_json_only_output(self) -> None:
        personas = (SKILL_ROOT / "references" / "reviewer-personas.md").read_text()
        core_personas = [
            "Correctness",
            "Security",
            "Performance and reliability",
            "Architecture and maintainability",
            "Contracts, data, and migrations",
        ]

        for persona in core_personas:
            output_body = self._output_body(personas, f"### {persona}")
            self._assert_json_only_output_rules(output_body)

    def test_optional_persona_prompts_require_json_only_output(self) -> None:
        personas = (SKILL_ROOT / "references" / "reviewer-personas.md").read_text()

        for persona in [
            "Adversarial reviewer",
            "Regression reviewer",
            "Test-quality reviewer",
        ]:
            output_body = self._output_body(personas, f"### {persona}")
            self._assert_json_only_output_rules(output_body)
            self._assert_shared_key_contract(output_body)

    def test_persona_prompts_reference_shared_issue_keys(self) -> None:
        personas = (SKILL_ROOT / "references" / "reviewer-personas.md").read_text()

        for persona in [
            "Correctness",
            "Security",
            "Performance and reliability",
            "Architecture and maintainability",
            "Contracts, data, and migrations",
        ]:
            heading = f"### {persona}"
            section_text = self._section_text(personas, heading)
            self._assert_shared_key_contract(section_text)

    def test_compact_templates_reference_schema_and_json_only_output_rules(self) -> None:
        template_text = (SKILL_ROOT / "references" / "review-template.md").read_text()
        schema_text = (SKILL_ROOT / "references" / "output-schema.md").read_text()

        self._assert_json_only_output_rules(template_text)
        self._assert_shared_key_contract(template_text)
        for key in SHARED_ISSUE_KEYS:
            self.assertIn(key, schema_text, f"Missing shared issue key {key} in output schema")

        lowered_schema = schema_text.lower()
        self.assertIn("raw json", lowered_schema)
        self.assertIn("code fences", lowered_schema)
        self.assertIn("outside the json object", lowered_schema)

    def test_output_schema_documents_persona_specific_fields(self) -> None:
        schema_text = (SKILL_ROOT / "references" / "output-schema.md").read_text()

        for field in [
            "attack_scenario",
            "mitigation_scope",
            "trigger_condition",
            "likely_impact",
            "affected_contract",
            "breakage_scenario",
            "rollout_caution",
            "residual_risks",
            "production_readiness_notes",
            "suggested_refactor_follow_ups",
            "rollout_cautions",
        ]:
            self.assertIn(field, schema_text)

    def test_skill_text_makes_main_harness_the_orchestrator(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text()

        self.assertIn("orchestrator", skill_text.lower())
        self.assertIn("subagent", skill_text.lower())
        self.assertIn("main harness", skill_text.lower())

    def test_skill_frontmatter_version_matches_package_version(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text()
        frontmatter = skill_text.split("---", 2)[1]

        name_match = re.search(r"^name:\s*(.+)$", frontmatter, re.MULTILINE)
        version_match = re.search(
            r"scrutinize_me_version:\s*\"([^\"]+)\"",
            frontmatter,
        )

        self.assertIsNotNone(name_match)
        self.assertIsNotNone(version_match)
        self.assertEqual(name_match.group(1).strip(), "scrutinize-me")
        self.assertEqual(version_match.group(1), __version__)

    def test_evals_cover_required_review_routing_cases(self) -> None:
        evals = json.loads((SKILL_ROOT / "evals" / "evals.json").read_text())
        eval_names = {entry["name"] for entry in evals}

        self.assertTrue(
            {
                "default-five-reviewers",
                "auth-change-adds-adversarial-review",
                "api-change-adds-regression-review",
                "large-refactor-adds-test-quality-review",
                "clean-refactor-no-findings",
            }.issubset(eval_names)
        )

    def test_evals_have_required_keys_and_non_empty_values(self) -> None:
        evals = json.loads((SKILL_ROOT / "evals" / "evals.json").read_text())
        names: set[str] = set()

        for entry in evals:
            self.assertEqual(set(entry), {"name", "prompt", "checks"})
            self.assertTrue(entry["name"])
            self.assertTrue(entry["prompt"])
            self.assertIsInstance(entry["checks"], list)
            self.assertTrue(entry["checks"])

            for check in entry["checks"]:
                self.assertIsInstance(check, str)
                self.assertTrue(check)

            self.assertNotIn(entry["name"], names)
            names.add(entry["name"])


if __name__ == "__main__":
    unittest.main()
