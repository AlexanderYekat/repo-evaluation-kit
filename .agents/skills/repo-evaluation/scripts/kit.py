#!/usr/bin/env python3
"""Local scaffolding and consistency checks. Never fetches or executes repositories."""

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

SKILL = Path(__file__).resolve().parents[1]
ASSETS = SKILL / "assets"
DOCUMENTS = ("BRIEF.md", "CAPABILITY-MAP.md", "REPORT.md", "VALIDATION.md", "STATE.md")
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
SHA = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
EXCLUDED = {"1c-migration-audit", "evaluations", "cache", "_sources", ".git", ".tmp", "__pycache__", "node_modules", ".venv"}
WORKSPACE_DOCS = ((".gitignore", "workspace.gitignore"), ("README.md", "WORKSPACE.md"), ("AGENTS.md", "WORKSPACE-AGENTS.md"))
PROTECTED_WORKSPACE_NAMES = {"repo-evaluation-kit"}


class Invalid(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Invalid(message)


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=unique)


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def valid_url(value, repository=False):
    if not isinstance(value, str) or re.search(r"\s", value):
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme in {"https", "http", "ssh"} and bool(parsed.hostname)
                and parsed.username is None and parsed.password is None
                and (not repository or (not parsed.query and not parsed.fragment)) and parsed.port != 0)
    except ValueError:
        return False


def schema_check(value, schema, label="request"):
    """Validate the deliberately small JSON Schema vocabulary used by request.schema.json."""
    types = {"object": dict, "array": list, "string": str, "boolean": bool}
    kind = schema["type"]
    require(type(value) is types[kind], f"{label}: expected {kind}")
    if "enum" in schema:
        require(value in schema["enum"], f"{label}: expected one of {schema['enum']}")
    if kind == "object":
        properties = schema.get("properties", {})
        require(all(key in value for key in schema.get("required", [])), f"{label}: missing required field")
        if schema.get("additionalProperties") is False:
            require(not set(value) - set(properties), f"{label}: unknown fields {sorted(set(value) - set(properties))}")
        for key, item in value.items():
            schema_check(item, properties[key], f"{label}.{key}")
    elif kind == "array":
        require(len(value) >= schema.get("minItems", 0), f"{label}: too few items")
        for index, item in enumerate(value):
            schema_check(item, schema["items"], f"{label}[{index}]")
    elif kind == "string":
        require(len(value) >= schema.get("minLength", 0), f"{label}: empty string")
        if "pattern" in schema:
            require(re.search(schema["pattern"], value) is not None, f"{label}: invalid format")
        if schema.get("format") == "uri":
            require(valid_url(value, repository=True), f"{label}: expected http/https/ssh URL without credentials, query or fragment")


def request(path):
    value = read_json(path)
    schema_check(value, read_json(ASSETS / "request.schema.json"))
    return value


def safe_id(value, label):
    require(isinstance(value, str) and ID.fullmatch(value), f"{label}: unsafe identifier")
    require(value.upper().split(".")[0] not in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}, f"{label}: reserved identifier")
    return value


def local_path(root, value, label, exists=True):
    require(isinstance(value, str) and value.strip(), f"{label}: local path required")
    require("\\" not in value and not re.match(r"^[A-Za-z]:", value), f"{label}: use relative forward-slash paths")
    path = Path(value)
    require(not path.is_absolute(), f"{label}: absolute path forbidden")
    resolved = (root / path).resolve()
    require(resolved.is_relative_to(root.resolve()), f"{label}: escapes run directory")
    if exists:
        require(resolved.is_file(), f"{label}: missing file {value}")
    return resolved


def initialize(args):
    data = request(args.request)
    project = safe_id(args.project or data.get("project_id") or "project", "project")
    run = safe_id(args.run or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"), "run")
    require(not (args.output and (args.project or args.run)), "--output cannot be combined with --project/--run")
    destination = (Path(args.output) if args.output else Path.cwd() / "evaluations" / project / run).resolve()
    require(not destination.is_relative_to(SKILL), "output must be outside the skill")
    # In a kit checkout protect actual sample/source/template locations. A portable
    # skill has no dependency on the sample and generic external folder names are OK.
    if SKILL.parent.name == "skills" and SKILL.parent.parent.name == ".agents":
        kit_root = SKILL.parents[2]
        for name in ("1c-migration-audit", "_sources", "sources", "templates", ".git"):
            protected = kit_root / name
            if protected.is_dir():
                require(not destination.is_relative_to(protected.resolve()), "output targets protected sample/source/template directory")
    require(not destination.exists(), f"output already exists: {destination}")
    required_assets = [*DOCUMENTS, "REPOSITORY.md", "INVENTORY.json", "EVIDENCE.json"]
    for name in required_assets:
        require((ASSETS / name).is_file(), f"missing template: {name}")
    # Validate all templates before creating anything. No overwrite/cleanup of user paths.
    inventory = read_json(ASSETS / "INVENTORY.json")
    evidence = read_json(ASSETS / "EVIDENCE.json")
    require(inventory == {"schema_version": "1.0", "input_count": 0, "repositories": []}, "inventory template must be empty")
    require(evidence == {"schema_version": "1.0", "sources": [], "claims": [], "runs": []}, "evidence template must be empty")
    text_templates = {name: (ASSETS / name).read_text(encoding="utf-8") for name in [*DOCUMENTS, "REPOSITORY.md"]}
    replacements = {"PROJECT_ID": project, "RUN_ID": run, "GOAL": data["goal"], "DEPTH": data.get("depth", "overview")}
    def render(template, extra=None):
        values = replacements | (extra or {})
        # One pass: user content cannot introduce another template substitution.
        return re.sub(r"\{\{([A-Z_]+)\}\}", lambda match: values.get(match[1], match[0]), template)
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "reports").mkdir()
    (destination / "sources").mkdir()
    (destination / "logs").mkdir()
    save_json(destination / "request.json", data)
    for name in DOCUMENTS:
        (destination / name).write_text(render(text_templates[name]), encoding="utf-8")
    seen = {}
    for index, url in enumerate(data["repositories"], 1):
        repo_id = f"r{index:02d}"
        record = {
            "id": repo_id, "input_index": index, "input_url": url, "canonical_url": None,
            "access": "NOT_CHECKED", "access_reason": "Доступность ещё не проверялась.",
            "default_branch": None, "commit": None, "commit_source": None,
            "origin": {"kind": "UNKNOWN", "upstream_url": None, "comparison": "NOT_CHECKED"},
            "project_type": None, "roles": [], "priority": None, "scope": "PENDING",
            "duplicate_of": seen.get(url), "report": f"reports/{repo_id}.md",
        }
        inventory["repositories"].append(record)
        seen.setdefault(url, repo_id)
        card = render(text_templates["REPOSITORY.md"], {"REPOSITORY_ID": repo_id, "REPOSITORY_URL": url})
        (destination / record["report"]).write_text(card, encoding="utf-8")
    inventory["input_count"] = len(data["repositories"])
    save_json(destination / "INVENTORY.json", inventory)
    save_json(destination / "EVIDENCE.json", evidence)
    (destination / "sources" / "README.md").write_text("# Источники\n\nСохраняйте разрешённые выдержки и метаданные; связывайте их с EVIDENCE.json. Источники ещё не собраны.\n", encoding="utf-8")
    print(f"Created {destination}\nScaffold only: no repository has been researched or executed.")


def layout_root():
    if SKILL.parent.name == "skills" and SKILL.parent.parent.name == ".agents":
        return SKILL.parents[2]
    return None


def remove_tree(path):
    def handle(func, item, _):
        Path(item).chmod(0o700)
        func(item)
    shutil.rmtree(path, onerror=handle)


def git_init(destination):
    git = shutil.which("git")
    require(git is not None, "git is required to create a workspace repository")
    result = subprocess.run([git, "init"], cwd=destination, capture_output=True, text=True)
    require(result.returncode == 0, result.stderr.strip() or result.stdout.strip() or "git init failed")


def write_workspace_identity(destination):
    first = not (destination / "workspace.json").is_file()
    for name, asset in WORKSPACE_DOCS:
        if name != ".gitignore" and not first:
            continue
        require((ASSETS / asset).is_file(), f"missing workspace template: {asset}")
        shutil.copyfile(ASSETS / asset, destination / name)
    save_json(destination / "workspace.json", {"kind": "repo-evaluation-workspace", "schema_version": "1.0"})


def default_projects_dir():
    configured = os.environ.get("REPO_EVALUATION_PROJECTS_DIR", "").strip()
    if configured:
        return Path(configured)
    if os.name == "nt":
        return Path("C:/MyProjects")
    return Path.home() / "MyProjects"


def workspace_destination(args):
    require(not (args.output and args.name), "project name cannot be combined with --output")
    require(not (args.output and args.parent), "--parent cannot be combined with --output")
    if args.output:
        return Path(args.output).resolve()
    require(args.name, "project name required")
    name = safe_id(args.name, "project")
    parent = Path(args.parent).resolve() if args.parent else default_projects_dir().resolve()
    parent.mkdir(parents=True, exist_ok=True)
    return (parent / name).resolve()


def new_workspace(args):
    destination = workspace_destination(args)
    require(not destination.is_relative_to(SKILL), "output must be outside the skill")
    if args.adopt:
        require(destination.is_dir(), f"adopt target does not exist: {destination}")
        require((destination / ".agents" / "skills" / "repo-evaluation" / "SKILL.md").is_file(), "adopt target is not a kit/workspace checkout")
        write_workspace_identity(destination)
        (destination / "evaluations").mkdir(exist_ok=True)
        if args.reset_git:
            require(destination.name not in PROTECTED_WORKSPACE_NAMES or args.force, "refusing to replace git in repo-evaluation-kit without --force")
            git_dir = destination / ".git"
            if git_dir.exists():
                remove_tree(git_dir)
            git_init(destination)
            git_note = "Git: replaced with a new empty repository, no commits, no remote."
        elif (destination / ".git").exists():
            git_note = "Git: history unchanged; pass --reset-git to replace the kit repository."
        else:
            git_init(destination)
            git_note = "Git: new empty repository, no commits, no remote."
        print(f"Adopted {destination}\n{git_note}\nOpen this folder and write the goal and repository links in chat.")
        return
    require(not destination.exists(), f"output already exists: {destination}")
    require(not args.reset_git and not args.force, "--reset-git and --force apply only to --adopt")
    for _, asset in WORKSPACE_DOCS:
        require((ASSETS / asset).is_file(), f"missing workspace template: {asset}")
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copytree(SKILL, destination / ".agents" / "skills" / "repo-evaluation", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
    root = layout_root()
    if root is not None:
        if (root / "prompts").is_dir():
            shutil.copytree(root / "prompts", destination / "prompts", ignore=shutil.ignore_patterns("__pycache__"))
        if (root / "request.example.json").is_file():
            shutil.copyfile(root / "request.example.json", destination / "request.example.json")
    if not (destination / "request.example.json").is_file():
        shutil.copyfile(ASSETS / "request.minimal.json", destination / "request.example.json")
    write_workspace_identity(destination)
    (destination / "evaluations").mkdir()
    (destination / "evaluations" / ".gitkeep").write_text("", encoding="utf-8")
    git_init(destination)
    print(f"Created {destination}\nOpen this folder and write the goal and repository links in chat.")


def fields(record, required, label):
    require(type(record) is dict, f"{label}: expected object")
    require(set(record) == set(required.split()), f"{label}: fields must be {required}")


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def nullable_text(value):
    return value is None or nonempty(value)


def checksum(value):
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def string_array(value):
    return type(value) is list and all(nonempty(item) for item in value)


def index_records(records, label):
    require(type(records) is list, f"{label}: expected array")
    result = {}
    for item in records:
        require(type(item) is dict and "id" in item, f"{label}: record needs id")
        identifier = safe_id(item["id"], label)
        require(identifier not in result, f"{label}: duplicate id {identifier}")
        result[identifier] = item
    return result


def check_inventory(root, data, inventory, warnings):
    fields(inventory, "schema_version input_count repositories", "inventory")
    require(inventory["schema_version"] == "1.0", "inventory: unsupported version")
    repositories = index_records(inventory["repositories"], "repositories")
    require(type(inventory["input_count"]) is int and inventory["input_count"] == len(data["repositories"]) == len(repositories), "inventory: input count mismatch")
    seen = {}
    for index, (repo_id, repo) in enumerate(repositories.items(), 1):
        label = f"inventory {repo_id}"
        fields(repo, "id input_index input_url canonical_url access access_reason default_branch commit commit_source origin project_type roles priority scope duplicate_of report", label)
        require(repo_id == f"r{index:02d}" and type(repo["input_index"]) is int and repo["input_index"] == index, f"{label}: ids/order must preserve input positions")
        require(repo["input_url"] == data["repositories"][index - 1], f"{label}: changed input URL")
        for key in ("canonical_url", "commit_source"):
            require(repo[key] is None or valid_url(repo[key]), f"{label}: invalid {key}")
        require(repo["access"] in {"NOT_CHECKED", "AVAILABLE", "UNAVAILABLE"}, f"{label}: invalid access")
        require(nonempty(repo["access_reason"]), f"{label}: access_reason required")
        for key in ("default_branch", "project_type", "priority"):
            require(nullable_text(repo[key]), f"{label}: invalid {key}")
        require(repo["commit"] is None or checksum(repo["commit"]), f"{label}: invalid repository commit")
        require(repo["commit"] is None or repo["commit_source"] is not None, f"{label}: commit needs provenance URL")
        fields(repo["origin"], "kind upstream_url comparison", f"{label}.origin")
        origin = repo["origin"]
        require(origin["kind"] in {"UNKNOWN", "ORIGINAL", "FORK"}, f"{label}: invalid origin")
        require(origin["comparison"] in {"NOT_CHECKED", "CHECKED", "NOT_APPLICABLE"}, f"{label}: invalid fork comparison")
        require(origin["upstream_url"] is None or valid_url(origin["upstream_url"]), f"{label}: invalid upstream URL")
        require(origin["upstream_url"] is not None or origin["comparison"] != "CHECKED", f"{label}: checked comparison needs known upstream")
        require(origin["kind"] != "UNKNOWN" or origin["comparison"] == "NOT_CHECKED", f"{label}: unknown origin cannot be compared")
        require(origin["kind"] != "FORK" or origin["comparison"] != "NOT_APPLICABLE", f"{label}: fork comparison cannot be not-applicable")
        if origin["kind"] == "FORK" and origin["upstream_url"] is None:
            warnings.append(f"{repo_id}: fork upstream is unknown; comparison remains unchecked")
        require(string_array(repo["roles"]), f"{label}: roles must be strings")
        require(repo["scope"] in {"PENDING", "OVERVIEW", "DEEP_STATIC"}, f"{label}: invalid scope")
        duplicate = repo["duplicate_of"]
        require(duplicate is None or duplicate in seen.values(), f"{label}: duplicate must reference an earlier input")
        if duplicate is not None:
            earlier_url = repositories[duplicate]["canonical_url"]
            require(repo["canonical_url"] is None or earlier_url is None or repo["canonical_url"] == earlier_url, f"{label}: duplicate has a different canonical URL")
        require(repo["input_url"] not in seen or duplicate == seen[repo["input_url"]], f"{label}: duplicate input not marked")
        seen.setdefault(repo["input_url"], repo_id)
        require(repo["report"] == f"reports/{repo_id}.md", f"{label}: incorrect report path")
        local_path(root, repo["report"], label)
        if repo["access"] != "AVAILABLE" or repo["scope"] == "PENDING" or repo["commit"] is None:
            warnings.append(f"{repo_id}: access/scope/version incomplete; no completed audit implied")
    return repositories


def check_evidence(root, evidence, repositories, warnings):
    fields(evidence, "schema_version sources claims runs", "evidence")
    require(evidence["schema_version"] == "1.0", "evidence: unsupported version")
    sources = index_records(evidence["sources"], "sources")
    runs = index_records(evidence["runs"], "runs")
    claims = index_records(evidence["claims"], "claims")
    def repository(record, label):
        require(record["repository_id"] in repositories, f"{label}: unknown repository")
        return repositories[record["repository_id"]]
    def pinned(record, repo, label, required=False):
        commit = record["commit"]
        require((commit is None and not required) or checksum(commit), f"{label}: valid pinned commit required")
        require(commit is None or commit == repo["commit"], f"{label}: commit differs from inventory")
    for identifier, source in sources.items():
        label = f"source {identifier}"
        fields(source, "id repository_id kind commit url path lines captured_at note", label)
        repo = repository(source, label)
        require(source["kind"] in {"DOCUMENTATION", "SOURCE", "METADATA"}, f"{label}: invalid kind")
        pinned(source, repo, label)
        require(source["url"] is None or valid_url(source["url"]), f"{label}: invalid URL")
        require(isinstance(source["note"], str), f"{label}: note must be string")
        try:
            dt.datetime.fromisoformat(source["captured_at"].replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            raise Invalid(f"{label}: captured_at must be ISO date/datetime")
        if source["path"] is not None:
            local_path(root, source["path"], label, exists=False)
        lines = source["lines"]
        require(lines is None or (type(lines) is list and len(lines) == 2 and all(type(item) is int and item > 0 for item in lines) and lines[1] >= lines[0]), f"{label}: invalid line range")
        located = source["path"] is not None and lines is not None
        require(source["kind"] != "SOURCE" or located, f"{label}: source evidence needs path/lines")
        if source["kind"] == "SOURCE" and source["commit"] is None:
            require(nonempty(source["note"]), f"{label}: unpinned source needs an explicit explanation")
            warnings.append(f"{identifier}: source version is unpinned; do not reuse as a fact about a verified commit")
        require(source["kind"] != "METADATA" or source["url"] is not None, f"{label}: metadata needs URL")
        require(located or source["url"] is not None, f"{label}: URL or commit/path/lines required")
        if source["commit"] is None and source["kind"] == "DOCUMENTATION":
            warnings.append(f"{identifier}: documentation is captured but unpinned")
    for identifier, run in runs.items():
        label = f"run {identifier}"
        fields(run, "id repository_id commit status kind command environment expected result log tests assertions skipped mocks_only reason", label)
        repo = repository(run, label)
        require(run["status"] in {"PASS", "FAIL", "NOT_RUN", "BLOCKED"}, f"{label}: invalid status")
        require(run["kind"] in {"BUILD", "TEST", "INTEGRATION", "BENCHMARK", "OTHER"}, f"{label}: invalid kind")
        executed = run["status"] in {"PASS", "FAIL"}
        pinned(run, repo, label, executed)
        for key in ("tests", "assertions", "skipped"):
            require(type(run[key]) is int and run[key] >= 0, f"{label}: {key} must be nonnegative integer")
        require(type(run["mocks_only"]) is bool, f"{label}: mocks_only must be boolean")
        for key in ("command", "environment", "expected", "result", "log", "reason"):
            require(nullable_text(run[key]), f"{label}: invalid {key}")
        if executed:
            for key in ("command", "environment", "expected", "result", "log"):
                require(nonempty(run[key]), f"{label}: executed run needs {key}")
            local_path(root, run["log"], label)
        else:
            require(nonempty(run["reason"]), f"{label}: unexecuted run needs reason")
            require(run["tests"] == run["assertions"] == run["skipped"] == 0, f"{label}: unexecuted run cannot have test counters")
            require(run["result"] is None and run["log"] is None and not run["mocks_only"], f"{label}: unexecuted run cannot have observed result, log or mocks")
            warnings.append(f"{identifier}: {run['status']} — {run['reason']}")
        if run["status"] == "PASS":
            require(run["assertions"] > 0, f"{label}: PASS needs real assertions")
            if run["kind"] in {"TEST", "INTEGRATION"}:
                require(run["tests"] > 0, f"{label}: PASS needs executed tests (skips excluded)")
            require(run["kind"] != "INTEGRATION" or not run["mocks_only"], f"{label}: mocks-only run is not real integration PASS")
    for identifier, claim in claims.items():
        label = f"claim {identifier}"
        fields(claim, "id repository_id status statement source_ids run_ids limitations", label)
        repository(claim, label)
        require(claim["status"] in {"DOCUMENTATION", "SOURCE", "RUN", "INFERENCE", "UNKNOWN"}, f"{label}: invalid status")
        require(nonempty(claim["statement"]) and isinstance(claim["limitations"], str), f"{label}: statement/limitations required")
        for key, records in (("source_ids", sources), ("run_ids", runs)):
            require(string_array(claim[key]) and len(set(claim[key])) == len(claim[key]), f"{label}: invalid {key}")
            for reference in claim[key]:
                require(reference in records, f"{label}: unknown evidence {reference}")
                require(records[reference]["repository_id"] == claim["repository_id"], f"{label}: evidence belongs to another repository")
        status = claim["status"]
        if status in {"DOCUMENTATION", "SOURCE"}:
            require(any(sources[item]["kind"] == status for item in claim["source_ids"]), f"{label}: status lacks matching source")
        if status == "RUN":
            require(claim["run_ids"] and all(runs[item]["status"] in {"PASS", "FAIL"} and runs[item]["assertions"] > 0 for item in claim["run_ids"]), f"{label}: RUN claim requires executed assertion evidence")
            if any(runs[item]["status"] == "FAIL" for item in claim["run_ids"]):
                require(nonempty(claim["limitations"]), f"{label}: observed failure needs scope/conditions in limitations")
        if status == "INFERENCE":
            require(claim["source_ids"] or claim["run_ids"], f"{label}: inference needs explicit basis")
            require(nonempty(claim["limitations"]), f"{label}: inference needs limitations")
        if status == "UNKNOWN":
            require(nonempty(claim["limitations"]), f"{label}: unknown needs explanation")
    if not claims:
        warnings.append("No recorded claims: this is a scaffold or incomplete investigation")


def markdown_files(root, excluded=()):
    # Do not descend into the immutable sample, downloaded code, caches, or symlinks.
    for item in root.iterdir():
        if item.is_symlink() or item.name in excluded:
            continue
        if item.is_dir():
            yield from markdown_files(item, excluded)
        elif item.suffix.lower() == ".md":
            yield item


def check_links(root, files):
    for file in files:
        content = file.read_text(encoding="utf-8-sig")
        # Fenced/inline examples are not links; ordinary inline and reference links are checked.
        content = re.sub(r"```.*?```|~~~.*?~~~", "", content, flags=re.S)
        content = re.sub(r"`[^`\n]*`", "", content)
        links = re.findall(r"!?\[[^\]\n]*\]\((<[^>]+>|[^\s)]+)(?:\s+\"[^\"]*\")?\)", content)
        links += re.findall(r"^\s*\[[^\]\n]+\]:\s*(<[^>]+>|\S+)", content, re.M)
        for target in links:
            target = target.strip("<>")
            if target.startswith("#"):
                continue
            parsed = urlsplit(target)
            if parsed.scheme and not re.match(r"^[A-Za-z]:", target):
                continue
            target = unquote(target.split("#", 1)[0].split("?", 1)[0])
            if "{{" in target:
                continue
            require(not Path(target).is_absolute() and not re.match(r"^[A-Za-z]:", target), f"{file.relative_to(root)}: absolute local link {target}")
            resolved = (file.parent / target).resolve()
            require(resolved.is_relative_to(root.resolve()), f"{file.relative_to(root)}: local link escapes package/run: {target}")
            require(resolved.exists(), f"{file.relative_to(root)}: broken local link {target}")


def check_run(path):
    root = Path(path).resolve()
    require(root.is_dir(), "run directory does not exist")
    for name in ["request.json", "INVENTORY.json", "EVIDENCE.json", *DOCUMENTS]:
        require((root / name).is_file(), f"missing run file: {name}")
    warnings = []
    repositories = check_inventory(root, request(root / "request.json"), read_json(root / "INVENTORY.json"), warnings)
    check_evidence(root, read_json(root / "EVIDENCE.json"), repositories, warnings)
    check_links(root, markdown_files(root, {"sources", ".git", "cache", "__pycache__", "history"}))
    print("PASS: structural consistency only; evidence truth and audit completion require review.")
    for warning in warnings:
        print(f"WARNING: {warning}")


def check_kit(path):
    root = Path(path).resolve()
    skill = root if (root / "SKILL.md").is_file() else root / ".agents" / "skills" / "repo-evaluation"
    for name in ["SKILL.md", "references/formats.md", "scripts/kit.py", "assets/request.schema.json", "assets/request.minimal.json", "assets/INVENTORY.json", "assets/EVIDENCE.json", *(f"assets/{name}" for name in [*DOCUMENTS, "REPOSITORY.md"]), "assets/workspace.gitignore", "assets/WORKSPACE.md", "assets/WORKSPACE-AGENTS.md"]:
        require((skill / name).is_file(), f"missing skill file: {name}")
    content = (skill / "SKILL.md").read_text(encoding="utf-8-sig")
    require(content.startswith("---\n") and re.search(r"(?m)^name:\s*repo-evaluation\s*$", content) and re.search(r"(?m)^description:\s*\S", content), "SKILL.md needs YAML name/description")
    schema_check(read_json(skill / "assets" / "request.minimal.json"), read_json(skill / "assets" / "request.schema.json"))
    # Asset links describe their rendered destinations, so validate those after init.
    files = [file for file in markdown_files(root, EXCLUDED) if not file.is_relative_to(skill / "assets")]
    check_links(root, files)
    check_links(skill, [file for file in markdown_files(skill, EXCLUDED | {"assets"})])
    print("PASS: kit files, minimal input, local links, and standalone skill boundary.")
    print("Template destination links are checked by check-run after init; content quality requires review.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate-request", "check-run", "check-kit"):
        commands.add_parser(name).add_argument("path")
    initialize_parser = commands.add_parser("init")
    initialize_parser.add_argument("--request", required=True)
    initialize_parser.add_argument("--output")
    initialize_parser.add_argument("--project")
    initialize_parser.add_argument("--run")
    workspace_parser = commands.add_parser("new-project", aliases=["new-workspace"])
    workspace_parser.add_argument("name", nargs="?", help="project folder name under the projects directory")
    workspace_parser.add_argument("--output", help="full path; do not combine with a project name")
    workspace_parser.add_argument("--parent", help="parent directory; default C:\\MyProjects on Windows")
    workspace_parser.add_argument("--adopt", action="store_true")
    workspace_parser.add_argument("--reset-git", action="store_true")
    workspace_parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "validate-request":
            request(args.path)
            print("PASS: request is valid; missing depth=overview, missing permissions=false.")
        elif args.command == "init":
            initialize(args)
        elif args.command == "check-run":
            check_run(args.path)
        elif args.command in {"new-project", "new-workspace"}:
            new_workspace(args)
        else:
            check_kit(args.path)
    except (Invalid, OSError, ValueError, TypeError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    # Stable CLI output in Windows terminals and redirected log files.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    sys.exit(main())
