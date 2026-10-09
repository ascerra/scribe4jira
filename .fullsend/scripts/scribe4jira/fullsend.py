"""Host entry points for the remotely fetched Fullsend script bundle."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import jsonschema

from scribe4jira.config import ScribeConfig


def check_project_scope(result: dict, project: str) -> None:
    """Reject output that attempts to write outside the configured Jira project."""
    for issue in result.get("new_issues", []):
        if issue.get("project_id") not in (None, "", project):
            raise ValueError("New issue targets a different Jira project")
        parent = issue.get("parent_issue_id")
        if parent and not parent.startswith(project + "-"):
            raise ValueError("Parent issue targets a different Jira project")
    for topic in result.get("topics", []):
        key = topic.get("existing_issue_id")
        if key and not key.startswith(project + "-"):
            raise ValueError("Comment targets a different Jira project")


def main() -> None:
    command = sys.argv[1]
    if command == "validate":
        result = json.loads(Path(sys.argv[2]).read_text())
        schema = json.loads(Path(sys.argv[3]).read_text())
        jsonschema.validate(result, schema)
        print("Scribe output schema validation passed")
        return

    config = ScribeConfig.from_env(load_dotenv=False)
    if command == "pre":
        from scribe4jira.pre_scribe import run

        workspace = run(config)
        metadata = json.loads(config.meta_file.read_text())
        metadata["default_issue_type"] = config.jira_default_issue_type
        metadata["default_priority"] = config.jira_default_priority
        metadata["notes_text"] = "\n\n---\n\n".join(
            p.read_text() for p in sorted(config.notes_dir.glob("doc-*.txt"))
        )
        if not metadata["notes_text"].strip():
            raise ValueError("No meeting notes were found")
        (workspace / "meeting-notes.json").write_text(json.dumps(metadata))
        return

    if command == "post":
        from scribe4jira.post_scribe import run

        result_path = Path(sys.argv[2])
        result = json.loads(result_path.read_text())
        schema = json.loads((Path(__file__).parent / "data/scribe-result.schema.json").read_text())
        jsonschema.validate(result, schema)
        check_project_scope(result, config.jira_project_key)
        shutil.copyfile(result_path, config.result_file)
        summary = run(config)
        if summary.rejected and not (summary.issues_created or summary.comments_posted):
            raise ValueError("All proposed writes were rejected")
        return
    raise ValueError("Unknown Scribe host command")


if __name__ == "__main__":
    main()
