#!/usr/bin/env python3
"""Apply security gates and write Scribe output to Jira."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from scribe4jira.config import ScribeConfig
from scribe4jira.gates import gate_reason
from scribe4jira.jira_client import IssueLookupError, JiraClient
from scribe4jira.jira_formatting import issue_browse_url, keys_to_linkify, linkify_issue_keys
from scribe4jira.logging_config import get_logger
from scribe4jira.security import dedupe_topics

logger = get_logger(__name__)


@dataclass
class IssueLink:
    key: str
    url: str
    action: str
    title: str


@dataclass
class PostScribeSummary:
    """Summary statistics from post-scribe execution."""

    topics_processed: int = 0
    comments_posted: int = 0
    issues_created: int = 0
    rejected: int = 0
    content_gate_rejections: int = 0
    comment_records: list[tuple[str, str]] = field(default_factory=list)
    created_records: list[tuple[str, str]] = field(default_factory=list)
    issue_links: list[IssueLink] = field(default_factory=list)


def run(
    config: ScribeConfig | None = None,
    jira_client: JiraClient | None = None,
) -> PostScribeSummary:
    """Apply security gates and write LLM-extracted topics to Jira.

        config: Optional ScribeConfig instance (defaults to from_env())
        jira_client: Optional JiraClient instance (defaults to new instance)

    returns: PostScribeSummary with statistics of actions taken

    raises: RuntimeError: If mode invalid or SCRIBE_DRY_RUN not explicitly set
    """
    logger.debug("starting_post_scribe", extra={"stage": "post-scribe"})
    cfg = config or ScribeConfig.from_env()
    logger.debug(
        "config_loaded",
        extra={"mode": cfg.mode, "dry_run": cfg.dry_run, "project": cfg.jira_project_key},
    )
    if cfg.mode not in {"all", "comments_only", "new_issues_only"}:
        raise RuntimeError("SCRIBE_MODE must be all, comments_only, or new_issues_only")

    result_path = cfg.result_file
    if not result_path.exists():
        raise RuntimeError(f"Missing agent result: {result_path}")

    logger.debug("reading_agent_result", extra={"path": str(result_path)})
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["topics"] = dedupe_topics(result.get("topics", []))
    logger.debug(
        "agent_result_loaded",
        extra={
            "topics": len(result.get("topics", [])),
            "new_issues": len(result.get("new_issues", [])),
        },
    )

    logger.debug("initializing_jira_client")
    jira = jira_client or JiraClient(cfg)
    summary = PostScribeSummary()
    known_keys = _load_known_issue_keys(cfg)

    if cfg.dry_run:
        print("DRY RUN — no Jira writes will be performed")

    if cfg.mode != "new_issues_only":
        logger.debug("processing_comments", extra={"count": len(result.get("topics", []))})
        _process_comments(cfg, jira, result.get("topics", []), summary, known_keys)
        logger.debug("comment_processing_complete")
    else:
        print(f"Skipping {len(result.get('topics', []))} comment topic(s) (new_issues_only mode)")

    if cfg.mode != "comments_only":
        logger.debug("processing_new_issues", extra={"count": len(result.get("new_issues", []))})
        _process_new_issues(cfg, jira, result.get("new_issues", []), summary, known_keys)
        logger.debug("new_issue_processing_complete")
    else:
        print(f"Skipping {len(result.get('new_issues', []))} new issue proposal(s)")

    _print_summary(cfg, summary)
    logger.debug("post_scribe_complete")
    return summary


def _process_comments(
    cfg: ScribeConfig,
    jira: JiraClient,
    topics: list[dict],
    summary: PostScribeSummary,
    known_keys: set[str],
) -> None:
    """Process comment topics for existing Jira issues.

    cfg: ScribeConfig instance
    jira: JiraClient instance
    topics: List of topic dictionaries from LLM
    summary: PostScribeSummary to update with statistics
    known_keys: Issue keys from backlog.json / closed-issues.json (for linkify)
    """
    summary.topics_processed = len(topics)
    print(f"Processing {len(topics)} topic(s) for existing Jira issues...")

    for index, topic in enumerate(topics, start=1):
        if topic.get("public_safe") is False:
            _reject_content(summary, index, len(topics), topic.get("public_safe_category"))
            continue

        issue_key = topic.get("existing_issue_id")
        if not issue_key:
            continue

        if topic.get("omit_reason"):
            print(f"  OMITTED: item {index} — {topic['omit_reason']}")
            continue

        title = topic.get("topic", "")
        body = topic.get("summary") or ""
        confidence = float(topic.get("confidence", 0))
        linked = keys_to_linkify(body, known_keys)
        body = linkify_issue_keys(body, known_keys, cfg.jira_server)

        reject_reason = gate_reason(title, body, confidence, cfg.min_confidence, comment=True)
        if reject_reason:
            print(f"  GATE REJECTED: [{title}] — {reject_reason}")
            summary.rejected += 1
            continue

        try:
            issue_found = jira.issue_exists(issue_key)
        except IssueLookupError as exc:
            print(f"  SKIP: [{title}] — could not verify Jira issue {issue_key}: {exc}")
            logger.error(
                "comment_issue_lookup_failed",
                extra={"issue_key": issue_key, "detail": str(exc)},
            )
            summary.rejected += 1
            continue

        if not issue_found:
            print(f"  GATE REJECTED: [{title}] — unknown Jira issue {issue_key}")
            summary.rejected += 1
            continue

        notes_url = _extract_notes_url(body)
        print(f"  PASS: [{title}] → comment on {issue_key} (confidence: {confidence})")
        summary.comment_records.append((issue_key, title))
        issue_url = issue_browse_url(cfg.jira_server, issue_key)
        summary.issue_links.append(
            IssueLink(key=issue_key, url=issue_url, action="updated", title=title)
        )

        if cfg.dry_run:
            print(f"    [DRY RUN] Would comment on {issue_key}")
            if linked:
                print(f"    [DRY RUN] Issue key links: {', '.join(sorted(linked))}")
            continue

        logger.debug("checking_duplicate_comment", extra={"issue_key": issue_key})
        if jira.comment_has_notes_url(issue_key, notes_url):
            print("    SKIP: duplicate comment (notes URL already posted)")
            continue

        logger.debug("posting_comment", extra={"issue_key": issue_key})
        jira.add_comment(issue_key, body, notes_url=notes_url)
        summary.comments_posted += 1
        logger.debug("comment_posted", extra={"issue_key": issue_key})


def _process_new_issues(
    cfg: ScribeConfig,
    jira: JiraClient,
    new_issues: list[dict],
    summary: PostScribeSummary,
    known_keys: set[str],
) -> None:
    """Process and create new Jira issues from LLM proposals.

    cfg: ScribeConfig instance
    jira: JiraClient instance
    new_issues: List of new issue dictionaries from LLM
    summary: PostScribeSummary to update with statistics
    known_keys: Issue keys from backlog.json / closed-issues.json (for linkify)
    """
    print(f"Processing {len(new_issues)} new issue proposal(s)...")

    for index, issue in enumerate(new_issues, start=1):
        if issue.get("public_safe") is False:
            _reject_content(summary, index, len(new_issues), issue.get("public_safe_category"))
            continue

        # Epics must be intentionally designed — reject agent-proposed epics
        if _normalize_issuetype(issue.get("issuetype") or "") in _EPIC_TYPES:
            issue_title = issue.get("summary") or issue.get("title") or "(untitled)"
            print(f"  GATE REJECTED: [{issue_title}] — Epic issuetype is not allowed")
            logger.warning(
                "epic_issuetype_rejected",
                extra={"summary": issue_title},
            )
            summary.rejected += 1
            continue

        summary_text = issue.get("summary") or issue.get("title") or ""
        description = issue.get("description") or issue.get("body") or summary_text
        confidence = float(issue.get("confidence", 0))

        story_points = issue.get("story_points_suggestion")
        linked = keys_to_linkify(description, known_keys)
        description = linkify_issue_keys(description, known_keys, cfg.jira_server)

        reject_reason = gate_reason(
            summary_text,
            description,
            confidence,
            cfg.min_confidence,
            comment=False,
            story_points_suggestion=story_points,
        )
        if reject_reason:
            print(f"  GATE REJECTED: [{summary_text}] — {reject_reason}")
            summary.rejected += 1
            continue
        issue = {**issue, "description": description}
        fields = JiraClient.build_create_fields(issue, cfg)

        # --- parent + issuetype validation / repair ---
        fixup = validate_and_fix_parent(
            fields["issuetype"]["name"],
            fields.get("parent", {}).get("key"),
        )
        if fixup.action:
            print(f"  FIXUP: [{fields['summary']}] — {fixup.action}")
            logger.warning(
                "parent_issuetype_fixup",
                extra={"summary": fields["summary"], "action": fixup.action},
            )
        fields["issuetype"] = {"name": fixup.issuetype}
        if fixup.parent_key:
            fields["parent"] = {"key": fixup.parent_key}
        else:
            fields.pop("parent", None)

        parent = fixup.parent_key

        # --- validate parent exists (live mode only) ---
        if parent and not cfg.dry_run:
            try:
                parent_found = jira.issue_exists(parent)
            except IssueLookupError as exc:
                print(f"  SKIP: [{fields['summary']}] — could not verify parent {parent}: {exc}")
                logger.error(
                    "parent_issue_lookup_failed",
                    extra={
                        "summary": fields["summary"],
                        "parent_key": parent,
                        "detail": str(exc),
                    },
                )
                summary.rejected += 1
                continue

            if not parent_found:
                if _normalize_issuetype(fixup.issuetype) in _SUBTASK_TYPES:
                    # Sub-task can't exist without parent — demote to Task
                    print(
                        f"  FIXUP: [{fields['summary']}] — parent {parent} not found, "
                        f"demoting Sub-task to Task"
                    )
                    logger.warning(
                        "parent_not_found_demoted",
                        extra={"summary": fields["summary"], "parent_key": parent},
                    )
                    fields["issuetype"] = {"name": "Task"}
                    fields.pop("parent", None)
                    parent = None
                else:
                    # Non-subtask — strip the invalid parent and create without it
                    print(
                        f"  FIXUP: [{fields['summary']}] — parent {parent} not found, "
                        f"creating without parent"
                    )
                    logger.warning(
                        "parent_not_found_stripped",
                        extra={"summary": fields["summary"], "parent_key": parent},
                    )
                    fields.pop("parent", None)
                    parent = None

        issuetype_name = fields["issuetype"]["name"]
        print(
            f"  PASS: [{fields['summary']}] → new {issuetype_name} "
            f"in {fields['project']['key']} (confidence: {confidence})"
        )

        if cfg.dry_run:
            parent_info = f" (parent: {parent})" if parent else ""
            print(f"    [DRY RUN] Would create Jira issue{parent_info}")
            if linked:
                print(f"    [DRY RUN] Issue key links: {', '.join(sorted(linked))}")
            summary.created_records.append((fields["project"]["key"], fields["summary"]))
            summary.issue_links.append(
                IssueLink(
                    key=f"{fields['project']['key']} (new)",
                    url="(dry-run — issue not created)",
                    action="created",
                    title=fields["summary"],
                )
            )
            continue

        logger.debug(
            "creating_jira_issue",
            extra={
                "summary": fields["summary"],
                "project": fields["project"]["key"],
                "parent_key": parent,
            },
        )
        try:
            created = jira.create_issue(fields, story_points_suggestion=story_points)
        except RuntimeError as exc:
            # Jira rejected the create — log and skip, never crash the pipeline
            print(f"    FAILED: [{fields['summary']}] — {exc}")
            logger.error(
                "jira_create_failed",
                extra={"summary": fields["summary"], "detail": str(exc)},
            )
            summary.rejected += 1
            continue
        issue_key = created.get("key", "unknown")
        issue_url = issue_browse_url(cfg.jira_server, issue_key)
        print(f"    Created: {issue_url}")
        logger.debug("jira_issue_created", extra={"issue_key": issue_key})
        summary.issues_created += 1
        summary.created_records.append((fields["project"]["key"], fields["summary"]))
        summary.issue_links.append(
            IssueLink(
                key=issue_key,
                url=issue_url,
                action="created",
                title=fields["summary"],
            )
        )


def _reject_content(
    summary: PostScribeSummary, index: int, total: int, category: str | None
) -> None:
    """Record content gate rejection in summary statistics.

    summary: PostScribeSummary to update
    index: Index of rejected item
    total: Total number of items
    category: Rejection category (e.g., "pii", "sensitive")
    """
    label = category or "unspecified"
    print(f"  GATE REJECTED: item {index} of {total} — content gate: {label}")
    summary.rejected += 1
    summary.content_gate_rejections += 1


def _load_known_issue_keys(cfg: ScribeConfig) -> set[str]:
    """Collect issue keys from pre-scribe backlog and closed-issue context files."""
    keys: set[str] = set()
    closed_file = cfg.workspace_dir / "closed-issues.json"
    for path in (cfg.backlog_file, closed_file):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "failed_to_load_issue_keys", extra={"path": str(path), "error": str(exc)}
            )
            continue
        if not isinstance(data, list):
            continue
        for item in data:
            if isinstance(item, dict) and (key := item.get("key")):
                keys.add(str(key))
    return keys


def _normalize_issuetype(name: str) -> str:
    """Normalize an issuetype name for comparison (lowercase, strip hyphens/spaces)."""
    return name.lower().replace("-", "").replace("_", "").replace(" ", "")


# Standard Jira hierarchy rules:
#   Epic → Story / Task / Bug → Sub-task
# Sub-task parent must be a non-Epic type (Task, Story, Bug, etc.).
# Task / Story / Bug parent must be an Epic.
# Epic cannot have a parent (unless Advanced Roadmaps Initiatives are configured).
_SUBTASK_TYPES = frozenset({"subtask"})
_EPIC_TYPES = frozenset({"epic"})


@dataclass
class ParentFixup:
    """Result of parent + issuetype validation / repair."""

    issuetype: str
    parent_key: str | None
    action: str | None = None  # None = no change, otherwise a human-readable message


def validate_and_fix_parent(
    issuetype: str,
    parent_key: str | None,
) -> ParentFixup:
    """Validate parent + issuetype and return a (possibly repaired) result.

    Repair strategy — never crash the pipeline:

    1. Sub-task WITHOUT a valid parent  → demote to Task (parent stripped).
    2. Epic WITH a parent               → strip parent (epics are top-level).
    3. Task/Story/Bug with parent        → keep as-is (Jira will validate
       hierarchy at create time; if the API rejects it the caller handles
       the RuntimeError gracefully).
    """
    norm = _normalize_issuetype(issuetype)

    if norm in _SUBTASK_TYPES and not parent_key:
        return ParentFixup(
            issuetype="Task",
            parent_key=None,
            action="Sub-task demoted to Task — no valid parent_issue_id provided",
        )

    if norm in _EPIC_TYPES and parent_key:
        return ParentFixup(
            issuetype=issuetype,
            parent_key=None,
            action=f"Epic cannot have a parent — stripped parent_issue_id {parent_key}",
        )

    return ParentFixup(issuetype=issuetype, parent_key=parent_key)


def _extract_notes_url(body: str) -> str:
    """Extract meeting notes URL from comment body for duplicate detection.

        body: Comment body text

    returns: Extracted URL or empty string if not found
    """
    match = re.search(r"\[Meeting notes\]\(([^)]+)\)", body or "")
    return match.group(1) if match else ""


def _print_summary(cfg: ScribeConfig, summary: PostScribeSummary) -> None:
    """Print execution summary to stdout.

    cfg: ScribeConfig instance
    summary: PostScribeSummary with statistics
    """
    mode = "DRY RUN" if cfg.dry_run else "LIVE"
    print("")
    print("=== Scribe Post-Script Summary ===")
    print(f"  Run mode: {mode}")
    print(f"  Agent mode: {cfg.mode}")
    print(f"  Topics processed: {summary.topics_processed}")
    print(f"  Comments {'would be ' if cfg.dry_run else ''}posted: {len(summary.comment_records)}")
    print(
        f"  New issues {'would be ' if cfg.dry_run else ''}created: {len(summary.created_records)}"
    )
    print(f"  Gate rejections: {summary.rejected}")
    print(f"    Content gate: {summary.content_gate_rejections}")
    if summary.issue_links:
        print("")
        print("  Jira tickets touched this run:")
        print("  " + "-" * 62)
        print(f"  {'Action':<10} {'Key':<12} URL")
        print("  " + "-" * 62)
        for link in summary.issue_links:
            print(f"  {link.action:<10} {link.key:<12} {link.url}")
        print("  " + "-" * 62)
    print("==================================")


if __name__ == "__main__":
    run()
