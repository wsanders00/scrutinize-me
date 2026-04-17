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
