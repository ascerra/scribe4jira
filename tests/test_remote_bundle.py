"""Exercise host wrappers using only a copied remote script directory."""

import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def test_script_bundle_pre_validate_and_dry_run(tmp_path):
    class FakeJira(BaseHTTPRequestHandler):
        writes = 0

        def do_GET(self):  # noqa: N802 - standard-library callback
            body = json.dumps({"issues": [], "isLast": True, "total": 0}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802 - standard-library callback
            if "search" in self.path:
                self.do_GET()
            else:
                type(self).writes += 1
                self.send_error(500)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeJira)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source = Path(__file__).resolve().parents[1] / ".fullsend/scripts"
        bundle = tmp_path / "remote-cache/scripts"
        shutil.copytree(source, bundle)
        workspace = tmp_path / "host-workspace"
        notes = tmp_path / "synthetic.txt"
        notes.write_text("Synthetic meeting: document a reproducible integration checklist.")
        iteration = tmp_path / "iteration"
        (iteration / "output").mkdir(parents=True)
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "SCRIBE_PYTHON": sys.executable,
            "SCRIBE_WORKSPACE_DIR": str(workspace),
            "SCRIBE_NOTES_SOURCE": "file",
            "SCRIBE_NOTES_FILE": str(notes),
            "SCRIBE_DRY_RUN": "true",
            "SCRIBE_MODE": "new_issues_only",
            "JIRA_SERVER": f"http://127.0.0.1:{server.server_port}",
            "JIRA_EMAIL": "test@example.com",
            "JIRA_API_TOKEN": "synthetic-test-credential",
            "JIRA_PROJECT_KEY": "TEST",
            "FULLSEND_VALIDATED_ITERATION_DIR": str(iteration),
            "FULLSEND_OUTPUT_SCHEMA": str(bundle / "scribe4jira/data/scribe-result.schema.json"),
        }
        result = {
            "topics": [],
            "new_issues": [
                {
                    "project_id": "TEST",
                    "summary": "Synthetic integration proof",
                    "description": "Document the integration checklist.",
                    "issuetype": "Task",
                    "priority": "Normal",
                    "confidence": 0.99,
                    "public_safe": True,
                }
            ],
            "stats": {
                "notes_processed": 1,
                "topics_extracted": 1,
                "existing_matched": 0,
                "new_proposed": 1,
                "omitted": 0,
            },
        }
        (iteration / "output/agent-result.json").write_text(json.dumps(result))
        for script in ["pre-scribe.sh", "validate-output-schema.sh", "post-scribe.sh"]:
            run = subprocess.run(
                ["bash", str(bundle / script)],
                cwd=iteration,
                env=env,
                capture_output=True,
                text=True,
            )
            assert run.returncode == 0, run.stderr
        assert json.loads((workspace / "meeting-notes.json").read_text())["notes_text"]
        assert FakeJira.writes == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
