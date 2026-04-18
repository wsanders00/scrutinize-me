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


class SkillContentTests(unittest.TestCase):
    def _section_text(self, text: str, heading: str) -> str:
        self.assertIn(heading, text, f"Missing heading {heading}")
        section_start = text.index(heading)
        next_heading_idx = text.find("\n### ", section_start + 1)
        next_major_idx = text.find("\n## ", section_start + 1)
        boundaries = [idx for idx in (next_heading_idx, next_major_idx) if idx != -1]
        section_end = min(boundaries) if boundaries else len(text)
        return text[section_start:section_end]

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
            heading = f"### {persona}"
            section_text = self._section_text(personas, heading)
            self.assertIn("Output:", section_text, f"Missing Output block for {persona}")
            output_body = section_text.split("Output:", 1)[1]
            self.assertIn("one single valid JSON object", output_body)
            self.assertIn("references/output-schema.md", output_body)
            self.assertIn("Do not include markdown, code fences, headings, or commentary", output_body)

    def test_optional_persona_prompts_require_json_only_output(self) -> None:
        personas = (SKILL_ROOT / "references" / "reviewer-personas.md").read_text()

        for persona in [
            "Adversarial reviewer",
            "Regression reviewer",
            "Test-quality reviewer",
        ]:
            heading = f"### {persona}"
            section_text = self._section_text(personas, heading)
            output_body = section_text.split("Output:", 1)[1]
            self.assertIn("one single valid JSON object", output_body)
            self.assertIn("references/output-schema.md", output_body)
            self.assertIn("Do not include markdown, code fences, headings, or commentary", output_body)

    def test_compact_templates_require_raw_json_only_output(self) -> None:
        template_text = (SKILL_ROOT / "references" / "review-template.md").read_text()
        schema_text = (SKILL_ROOT / "references" / "output-schema.md").read_text()

        self.assertIn("Return one single valid JSON object", template_text)
        self.assertIn(
            "Do not include markdown, code fences, headings, or commentary outside the JSON object.",
            template_text,
        )
        self.assertIn("Return raw JSON only.", schema_text)
        self.assertIn("Do not wrap the response in Markdown code fences.", schema_text)
        self.assertIn("Do not include headings, commentary, or any text outside the JSON object.", schema_text)

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
