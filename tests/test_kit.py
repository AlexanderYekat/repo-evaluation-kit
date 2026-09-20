"""Synthetic fixtures exercise mechanics; no third-party repository is executed."""

import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".agents" / "skills" / "repo-evaluation" / "scripts" / "kit.py"
spec = importlib.util.spec_from_file_location("kit", SCRIPT)
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)
COMMIT = "a" * 40
URL = "https://github.com/fixture/example"


class KitTests(unittest.TestCase):
    def setUp(self):
        # Owned temporary directory only, never the immutable example or real code.
        self.temporary = tempfile.TemporaryDirectory(prefix="repo-kit-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.request_path = self.root / "request.json"
        self.data = {"goal": "fixture: select a searchable notes store", "repositories": [URL]}
        kit.save_json(self.request_path, self.data)
        self.run = self.root / "result"

    def invoke(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = kit.main(list(map(str, args)))
        return code, out.getvalue() + err.getvalue()

    def init(self, data=None):
        if data is not None:
            kit.save_json(self.request_path, data)
        code, output = self.invoke("init", "--request", self.request_path, "--output", self.run)
        self.assertEqual(code, 0, output)
        return kit.read_json(self.run / "INVENTORY.json")

    def pin(self):
        inventory = self.init()
        repo = inventory["repositories"][0]
        repo.update(access="AVAILABLE", access_reason="fixture: local recorded availability", canonical_url=URL,
                    default_branch="main", commit=COMMIT, commit_source=f"{URL}/commit/{COMMIT}", scope="OVERVIEW")
        kit.save_json(self.run / "INVENTORY.json", inventory)
        return inventory

    def evidence(self, status="PASS"):
        self.pin()
        (self.run / "logs" / "fixture.txt").write_text("fixture: asserted output equals expected\n", encoding="utf-8")
        return {
            "schema_version": "1.0", "sources": [], "claims": [{
                "id": "c01", "repository_id": "r01", "status": "RUN", "statement": "fixture: query returns expected record",
                "source_ids": [], "run_ids": ["run01"], "limitations": "fixture: isolated scenario only",
            }], "runs": [{
                "id": "run01", "repository_id": "r01", "commit": COMMIT, "status": status, "kind": "TEST",
                "command": "fixture-check", "environment": "synthetic fixture", "expected": "one expected record",
                "result": "one expected record", "log": "logs/fixture.txt", "tests": 1, "assertions": 1,
                "skipped": 0, "mocks_only": False, "reason": None,
            }],
        }

    def check_evidence(self, evidence):
        kit.save_json(self.run / "EVIDENCE.json", evidence)
        return self.invoke("check-run", self.run)

    def test_minimal_input_and_honest_scaffold(self):
        inventory = self.init()
        self.assertEqual(kit.read_json(self.run / "request.json"), self.data)
        self.assertEqual(inventory["repositories"][0]["access"], "NOT_CHECKED")
        self.assertIsNone(inventory["repositories"][0]["commit"])
        code, output = self.invoke("check-run", self.run)
        self.assertEqual(code, 0, output)
        self.assertIn("WARNING", output)
        self.assertIn("scaffold", output)

    def test_schema_rejects_invalid_request_fields_and_types(self):
        invalid = [
            {}, {"goal": "", "repositories": [URL]}, {"goal": " \n", "repositories": [URL]},
            {"goal": "x", "repositories": []}, {"goal": "x", "repositories": URL},
            self.data | {"unknown": True}, self.data | {"depth": "deep"},
            self.data | {"permissions": {"run_tests": "false"}},
            self.data | {"permissions": {"shell": True}}, self.data | {"constraints": "none"},
            self.data | {"repositories": ["file:///tmp/repo"]},
            self.data | {"repositories": ["https://token@example.org/repo"]},
            self.data | {"repositories": ["https://example.org/repo?token=secret"]},
            self.data | {"repositories": ["https://example.org/repo#readme"]},
            self.data | {"project_id": "../elsewhere"}, self.data | {"repositories": [False]},
        ]
        for data in invalid:
            with self.subTest(data=data):
                kit.save_json(self.request_path, data)
                code, output = self.invoke("validate-request", self.request_path)
                self.assertEqual(code, 1, output)

    def test_duplicate_json_keys_rejected(self):
        self.request_path.write_text('{"goal":"x","goal":"y","repositories":["https://example.org/r"]}', encoding="utf-8")
        code, output = self.invoke("validate-request", self.request_path)
        self.assertEqual(code, 1)
        self.assertIn("duplicate JSON key", output)

    def test_duplicate_inputs_and_unavailable_are_preserved(self):
        data = self.data | {"repositories": [URL, URL, "https://github.com/fixture/unavailable"]}
        inventory = self.init(data)
        self.assertEqual(inventory["input_count"], 3)
        self.assertEqual(inventory["repositories"][1]["duplicate_of"], "r01")
        inventory["repositories"][2].update(access="UNAVAILABLE", access_reason="fixture: access denied")
        kit.save_json(self.run / "INVENTORY.json", inventory)
        code, output = self.invoke("check-run", self.run)
        self.assertEqual(code, 0, output)
        self.assertEqual(len(list((self.run / "reports").glob("*.md"))), 3)

    def test_dropped_or_changed_input_rejected(self):
        inventory = self.init()
        inventory["repositories"][0]["input_url"] = "https://example.org/replaced"
        kit.save_json(self.run / "INVENTORY.json", inventory)
        self.assertEqual(self.invoke("check-run", self.run)[0], 1)
        inventory["repositories"] = []
        kit.save_json(self.run / "INVENTORY.json", inventory)
        self.assertEqual(self.invoke("check-run", self.run)[0], 1)

    def test_missing_card_and_link_rejected(self):
        self.init()
        card = self.run / "reports" / "r01.md"
        card.unlink()
        self.assertEqual(self.invoke("check-run", self.run)[0], 1)
        card.write_text("# fixture\n[missing](../missing.md)\n", encoding="utf-8")
        code, output = self.invoke("check-run", self.run)
        self.assertEqual(code, 1)
        self.assertIn("broken local link", output)

    def test_local_link_cannot_escape_run(self):
        self.init()
        (self.run / "REPORT.md").write_text("[outside](../request.json)\n", encoding="utf-8")
        self.assertEqual(self.invoke("check-run", self.run)[0], 1)

    def test_pass_requires_real_test_assertions(self):
        evidence = self.evidence()
        self.assertEqual(self.check_evidence(evidence)[0], 0)
        evidence["runs"][0]["assertions"] = 0
        code, output = self.check_evidence(evidence)
        self.assertEqual(code, 1)
        self.assertIn("PASS needs real assertions", output)

    def test_skipped_only_not_pass_but_mixed_tests_valid(self):
        evidence = self.evidence()
        evidence["runs"][0]["skipped"] = 1
        self.assertEqual(self.check_evidence(evidence)[0], 0)
        evidence["runs"][0]["tests"] = 0
        code, output = self.check_evidence(evidence)
        self.assertEqual(code, 1)
        self.assertIn("PASS needs executed tests", output)

    def test_mock_only_integration_not_pass(self):
        evidence = self.evidence()
        evidence["runs"][0].update(kind="INTEGRATION", mocks_only=True)
        code, output = self.check_evidence(evidence)
        self.assertEqual(code, 1)
        self.assertIn("mocks-only", output)

    def test_run_requires_command_environment_expected_result_and_log(self):
        evidence = self.evidence()
        for key in ("command", "environment", "expected", "result", "log"):
            with self.subTest(key=key):
                invalid = copy.deepcopy(evidence)
                invalid["runs"][0][key] = None
                self.assertEqual(self.check_evidence(invalid)[0], 1)
        evidence["runs"][0]["log"] = "logs/absent.txt"
        self.assertEqual(self.check_evidence(evidence)[0], 1)

    def test_not_run_is_honest_but_cannot_confirm_claim(self):
        evidence = self.evidence()
        evidence["runs"][0].update(status="NOT_RUN", tests=0, assertions=0, skipped=0, reason="fixture: execution not authorized", result=None, log=None)
        self.assertEqual(self.check_evidence(evidence)[0], 1)
        evidence["claims"][0].update(status="UNKNOWN", run_ids=[], limitations="fixture: no execution evidence")
        code, output = self.check_evidence(evidence)
        self.assertEqual(code, 0, output)
        self.assertIn("NOT_RUN", output)

    def test_unexecuted_run_cannot_contain_observed_result_or_log(self):
        evidence = self.evidence()
        evidence["claims"] = []
        evidence["runs"][0].update(status="BLOCKED", tests=0, assertions=0, skipped=0,
                                   reason="fixture: missing isolated environment", result=None, log=None)
        self.assertEqual(self.check_evidence(evidence)[0], 0)
        for key, value in (("result", "observed success"), ("log", "logs/fixture.txt"), ("mocks_only", True)):
            with self.subTest(key=key):
                invalid = copy.deepcopy(evidence)
                invalid["runs"][0][key] = value
                self.assertEqual(self.check_evidence(invalid)[0], 1)

    def test_failed_assertion_can_confirm_observed_failure(self):
        evidence = self.evidence(status="FAIL")
        evidence["claims"][0]["statement"] = "fixture: tested input is rejected unexpectedly"
        self.assertEqual(self.check_evidence(evidence)[0], 0)
        evidence["runs"][0]["assertions"] = 0
        self.assertEqual(self.check_evidence(evidence)[0], 1)
        evidence["claims"] = []  # setup failure itself may still be recorded as FAIL.
        self.assertEqual(self.check_evidence(evidence)[0], 0)

    def test_run_commit_mismatch_is_rejected(self):
        evidence = self.evidence()
        evidence["runs"][0]["commit"] = "b" * 40
        code, output = self.check_evidence(evidence)
        self.assertEqual(code, 1)
        self.assertIn("commit differs", output)

    def test_repository_commit_needs_full_hash_and_provenance(self):
        inventory = self.pin()
        for commit, source in (("abc123", URL), (COMMIT, None)):
            with self.subTest(commit=commit, source=source):
                inventory["repositories"][0].update(commit=commit, commit_source=source)
                kit.save_json(self.run / "INVENTORY.json", inventory)
                self.assertEqual(self.invoke("check-run", self.run)[0], 1)

    def test_source_lines_pin_and_documented_claim_are_separate(self):
        self.pin()
        source = {"id": "s01", "repository_id": "r01", "kind": "DOCUMENTATION", "commit": COMMIT,
                  "url": f"{URL}/blob/{COMMIT}/README.md#L1-L3", "path": "README.md", "lines": [1, 3],
                  "captured_at": "2026-09-16", "note": "fixture: docs promise search; only selected source files inspected"}
        claim = {"id": "c01", "repository_id": "r01", "status": "DOCUMENTATION", "statement": "fixture: README promises search",
                 "source_ids": ["s01"], "run_ids": [], "limitations": "fixture: implementation not found in inspected scope"}
        evidence = {"schema_version": "1.0", "sources": [source], "claims": [claim], "runs": []}
        self.assertEqual(self.check_evidence(evidence)[0], 0)
        claim["status"] = "SOURCE"
        self.assertEqual(self.check_evidence(evidence)[0], 1)
        source["kind"] = "SOURCE"
        self.assertEqual(self.check_evidence(evidence)[0], 0)
        source["lines"] = [3, 1]
        self.assertEqual(self.check_evidence(evidence)[0], 1)
        source["lines"] = [1, 3]
        source["commit"] = "c" * 40
        self.assertEqual(self.check_evidence(evidence)[0], 1)

    def test_source_with_unknown_version_needs_explicit_limitation(self):
        self.init()
        source = {"id": "s01", "repository_id": "r01", "kind": "SOURCE", "commit": None,
                  "url": None, "path": "src/query.py", "lines": [10, 20], "captured_at": "2026-09-16",
                  "note": "fixture: allowed local snapshot lacks Git metadata; version is unknown"}
        claim = {"id": "c01", "repository_id": "r01", "status": "SOURCE", "statement": "fixture: snapshot contains a query function",
                 "source_ids": ["s01"], "run_ids": [], "limitations": "fixture: source snapshot only; unknown commit"}
        evidence = {"schema_version": "1.0", "sources": [source], "claims": [claim], "runs": []}
        code, output = self.check_evidence(evidence)
        self.assertEqual(code, 0, output)
        self.assertIn("source version is unpinned", output)
        source["note"] = ""
        self.assertEqual(self.check_evidence(evidence)[0], 1)

    def test_duplicate_cannot_have_conflicting_confirmed_identity(self):
        inventory = self.init(self.data | {"repositories": [URL, URL]})
        inventory["repositories"][0]["canonical_url"] = URL
        inventory["repositories"][1]["canonical_url"] = "https://github.com/fixture/different"
        kit.save_json(self.run / "INVENTORY.json", inventory)
        code, output = self.invoke("check-run", self.run)
        self.assertEqual(code, 1)
        self.assertIn("different canonical URL", output)

    def test_unknown_fork_upstream_kept_with_warning(self):
        inventory = self.init()
        inventory["repositories"][0]["origin"] = {"kind": "FORK", "upstream_url": None, "comparison": "NOT_CHECKED"}
        kit.save_json(self.run / "INVENTORY.json", inventory)
        code, output = self.invoke("check-run", self.run)
        self.assertEqual(code, 0, output)
        self.assertIn("fork upstream is unknown", output)

    def test_existing_output_never_overwritten(self):
        self.run.mkdir()
        sentinel = self.run / "keep.txt"
        sentinel.write_text("user content", encoding="utf-8")
        code, output = self.invoke("init", "--request", self.request_path, "--output", self.run)
        self.assertEqual(code, 1)
        self.assertIn("already exists", output)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "user content")

    def test_traversal_and_reserved_output_ids_rejected(self):
        for flag, value in (("--project", "../outside"), ("--run", "../../outside"), ("--run", "CON")):
            with self.subTest(flag=flag, value=value):
                code, output = self.invoke("init", "--request", self.request_path, flag, value)
                self.assertEqual(code, 1, output)

    def test_skill_and_existing_sample_destinations_rejected(self):
        portable = self.root / "portable-kit"
        skill = portable / ".agents" / "skills" / "repo-evaluation"
        shutil.copytree(kit.SKILL, skill, ignore=shutil.ignore_patterns("__pycache__"))
        sample = portable / "1c-migration-audit"
        sample.mkdir()
        sentinel = sample / "keep.txt"
        sentinel.write_text("fixture: immutable sample", encoding="utf-8")
        paths = [skill / "new-output", sample / "forbidden-output"]
        for destination in paths:
            with self.subTest(destination=destination):
                result = subprocess.run([sys.executable, str(skill / "scripts" / "kit.py"), "init",
                                         "--request", str(self.request_path), "--output", str(destination)],
                                        cwd=self.root, text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertFalse(destination.exists())
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "fixture: immutable sample")

    def test_separate_projects_and_runs_and_external_sources_parent(self):
        previous = Path.cwd()
        os.chdir(self.root)
        try:
            for project, run in (("alpha", "one"), ("alpha", "two"), ("beta", "one")):
                code, output = self.invoke("init", "--request", self.request_path, "--project", project, "--run", run)
                self.assertEqual(code, 0, output)
                self.assertTrue((self.root / "evaluations" / project / run / "STATE.md").is_file())
        finally:
            os.chdir(previous)
        external = self.root / "sources" / "research-output"
        code, output = self.invoke("init", "--request", self.request_path, "--output", external)
        self.assertEqual(code, 0, output)
        self.assertEqual(self.invoke("check-run", external)[0], 0)

    def test_relocated_standalone_skill_needs_no_original_sample(self):
        relocated = self.root / "copied-skill"
        shutil.copytree(kit.SKILL, relocated, ignore=shutil.ignore_patterns("__pycache__"))
        script = relocated / "scripts" / "kit.py"
        for arguments in (["check-kit", str(relocated)],
                          ["init", "--request", str(self.request_path), "--output", str(self.run)],
                          ["check-run", str(self.run)]):
            result = subprocess.run([sys.executable, str(script), *arguments], cwd=self.root, text=True,
                                    encoding="utf-8", errors="replace", capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def git(self, cwd, *arguments):
        result = subprocess.run(["git", *arguments], cwd=cwd, text=True, encoding="utf-8",
                                errors="replace", capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def assert_workspace(self, path):
        gitignore = (path / ".gitignore").read_text(encoding="utf-8")
        self.assertNotIn("/evaluations/", gitignore)
        self.assertIn("evaluations/**/sources/", gitignore)
        self.assertTrue((path / ".agents" / "skills" / "repo-evaluation" / "SKILL.md").is_file())
        self.assertTrue((path / "workspace.json").is_file())
        self.assertEqual(kit.read_json(path / "workspace.json")["kind"], "repo-evaluation-workspace")
        self.assertFalse((path / "tests").exists())
        self.assertTrue((path / ".git").is_dir())
        empty = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, text=True, encoding="utf-8",
                               errors="replace", capture_output=True, check=False)
        self.assertNotEqual(empty.returncode, 0)

    def test_new_workspace_is_research_project_not_kit_clone(self):
        destination = self.root / "research-project"
        code, output = self.invoke("new-project", "--output", destination)
        self.assertEqual(code, 0, output)
        self.assert_workspace(destination)
        self.assertTrue((destination / "prompts" / "START-AUDIT.md").is_file())
        self.assertTrue((destination / "request.example.json").is_file())
        self.assertTrue((destination / "evaluations" / ".gitkeep").is_file())
        self.assertEqual(self.invoke("check-kit", destination)[0], 0)
        sentinel = destination / "keep.txt"
        sentinel.write_text("user", encoding="utf-8")
        code, output = self.invoke("new-workspace", "--output", destination)
        self.assertEqual(code, 1, output)
        self.assertIn("already exists", output)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "user")

    def test_project_name_creates_under_parent(self):
        parent = self.root / "MyProjects"
        code, output = self.invoke("new-project", "alpha", "--parent", parent)
        self.assertEqual(code, 0, output)
        self.assert_workspace(parent / "alpha")
        self.assertIn("Open this folder", output)
        code, output = self.invoke("new-project")
        self.assertEqual(code, 1, output)
        self.assertIn("project name required", output)
        code, output = self.invoke("new-project", "alpha", "--output", self.root / "other")
        self.assertEqual(code, 1, output)
        code, output = self.invoke("new-project", "../escape", "--parent", parent)
        self.assertEqual(code, 1, output)

    def test_default_projects_dir_uses_env_or_myprojects(self):
        previous = os.environ.pop("REPO_EVALUATION_PROJECTS_DIR", None)
        try:
            expected = Path("C:/MyProjects") if os.name == "nt" else Path.home() / "MyProjects"
            self.assertEqual(kit.default_projects_dir(), expected)
            os.environ["REPO_EVALUATION_PROJECTS_DIR"] = str(self.root / "from-env")
            self.assertEqual(kit.default_projects_dir(), self.root / "from-env")
        finally:
            if previous is None:
                os.environ.pop("REPO_EVALUATION_PROJECTS_DIR", None)
            else:
                os.environ["REPO_EVALUATION_PROJECTS_DIR"] = previous

    def test_adopt_workspace_replaces_kit_identity_and_optional_git(self):
        copied = self.root / "copied-kit"
        shutil.copytree(kit.SKILL, copied / ".agents" / "skills" / "repo-evaluation",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (copied / ".gitignore").write_text("/evaluations/\n/request.json\n", encoding="utf-8")
        (copied / "README.md").write_text("kit clone", encoding="utf-8")
        research = copied / "evaluations" / "main"
        research.mkdir(parents=True)
        (research / "STATE.md").write_text("started", encoding="utf-8")
        self.git(copied, "init")
        self.git(copied, "config", "user.email", "fixture@example.test")
        self.git(copied, "config", "user.name", "fixture")
        self.git(copied, "add", ".gitignore")
        self.git(copied, "-c", "commit.gpgsign=false", "commit", "-m", "kit history")
        code, output = self.invoke("new-project", "--output", copied, "--adopt")
        self.assertEqual(code, 0, output)
        self.assertIn("history unchanged", output)
        gitignore = (copied / ".gitignore").read_text(encoding="utf-8")
        self.assertNotIn("/evaluations/", gitignore)
        self.assertEqual((copied / "README.md").read_text(encoding="utf-8"),
                         (kit.ASSETS / "WORKSPACE.md").read_text(encoding="utf-8"))
        self.assertEqual((research / "STATE.md").read_text(encoding="utf-8"), "started")
        log = self.git(copied, "log", "-1", "--format=%s")
        self.assertIn("kit history", log.stdout)
        (copied / "README.md").write_text("custom", encoding="utf-8")
        code, output = self.invoke("new-project", "--output", copied, "--adopt", "--reset-git")
        self.assertEqual(code, 0, output)
        self.assertEqual((copied / "README.md").read_text(encoding="utf-8"), "custom")
        empty = subprocess.run(["git", "rev-parse", "HEAD"], cwd=copied, text=True, encoding="utf-8",
                               errors="replace", capture_output=True, check=False)
        self.assertNotEqual(empty.returncode, 0)

    def test_adopt_refuses_to_reset_git_of_named_kit_without_force(self):
        named = self.root / "repo-evaluation-kit"
        shutil.copytree(kit.SKILL, named / ".agents" / "skills" / "repo-evaluation",
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.git(named, "init")
        marker = named / ".git" / "HEAD"
        self.assertTrue(marker.is_file())
        code, output = self.invoke("new-project", "--output", named, "--adopt", "--reset-git")
        self.assertEqual(code, 1, output)
        self.assertIn("--force", output)
        self.assertTrue(marker.is_file())
        code, output = self.invoke("new-project", "--output", named, "--adopt", "--reset-git", "--force")
        self.assertEqual(code, 0, output)
        empty = subprocess.run(["git", "rev-parse", "HEAD"], cwd=named, text=True, encoding="utf-8",
                               errors="replace", capture_output=True, check=False)
        self.assertNotEqual(empty.returncode, 0)


if __name__ == "__main__":
    unittest.main()
