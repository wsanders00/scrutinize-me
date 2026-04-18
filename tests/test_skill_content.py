import json
from pathlib import Path
import unittest


SKILL_ROOT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "scrutinize_me_skill"
    / "skill"
    / "scrutinize-me"
)


class SkillContentTests(unittest.TestCase):
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
            section_start = personas.index(heading)
            next_core_idx = personas.find("\n### ", section_start + 1)
            next_major_idx = personas.find("\n## ", section_start + 1)
            boundaries = [idx for idx in (next_core_idx, next_major_idx) if idx != -1]
            section_end = min(boundaries) if boundaries else len(personas)
            section_text = personas[section_start:section_end]

            self.assertIn("Output:", section_text, f"Missing Output block for {persona}")
            output_body = section_text.split("Output:", 1)[1]
            self.assertIn("single valid JSON object", output_body)
            self.assertIn("references/output-schema.md", output_body)
            self.assertIn("Do not include any prose outside the JSON object", output_body)

    def test_skill_text_makes_main_harness_the_orchestrator(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text()

        self.assertIn("orchestrator", skill_text.lower())
        self.assertIn("subagent", skill_text.lower())
        self.assertIn("main harness", skill_text.lower())

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


if __name__ == "__main__":
    unittest.main()
