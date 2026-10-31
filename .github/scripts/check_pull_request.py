#!/usr/bin/env python3
# Copyright (c) Tutez. GPLv3: see .github/LICENSE.
"""Checks a pull request to the index.

It may only add mods/<id>/<version>.json files, each checked by the
launcher (--check-entry), or add `yanked` to an indexed one. Writes what the
reviewer reads, the sources' diff against the last indexed version included,
to the job summary, and with the diffs cut shorter to the file $REPORT, which
the Comment workflow posts on the pull request.

Usage: check_pull_request.py <launcher> <base-sha>
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ENTRY_PATH = re.compile(r"mods/[a-z0-9_]+/[0-9]+\.[0-9]+\.[0-9]+\.json")
MAX_DIFF_CHARS = 200_000
# A comment holds 65,536 characters; the diffs share this much of it.
COMMENT_DIFF_CHARS = 50_000


class Sources(str):
    """The sources' diff of an entry in the report, cut to fit where the report goes."""


def fenced(text, info=""):
    """Text from the pull request as a code block it cannot close: a mod's sources must not
    turn the rest of the report into Markdown."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return [fence + info, text, fence]


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def labels():
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return set()
    event = json.loads(Path(event_path).read_text())
    return {label["name"] for label in event.get("pull_request", {}).get("labels", [])}


def check_yank(base, path):
    """A merged entry only ever gets `yanked`."""
    before = json.loads(git("show", f"{base}:{path}"))
    after = json.loads(Path(path).read_text())
    reason = after.pop("yanked", None)
    if "yanked" in before:
        return f"{path}: already yanked"
    if before != after:
        return f"{path}: a merged entry only gets yanked, nothing else changes"
    if not isinstance(reason, str) or not reason.strip():
        return f"{path}: yanked must say why"
    return None


def diff(work):
    """The sources against the last indexed version, or all of them."""
    previous = work / "previous"
    if not previous.is_dir():
        previous = work / "empty"
        previous.mkdir(exist_ok=True)
    result = subprocess.run(
        ["git", "diff", "--no-index", "--no-color", "--", str(previous), str(work / "new")],
        capture_output=True,
        text=True,
    )
    # git writes each path under its temporary folder, after a/ or b/, bare in a rename's lines:
    # keep it relative to its version.
    text = result.stdout
    for root in (previous, work / "new"):
        text = text.replace(str(root) + "/", "/")
    return re.sub(r"^(rename|copy) (from|to) /", r"\1 \2 ", text, flags=re.MULTILINE)


def render(summary, diff_chars, cut):
    """The report, each diff cut past `diff_chars`, saying `cut` where it is."""
    lines = []
    for line in summary:
        if isinstance(line, Sources):
            text = line if len(line) <= diff_chars else line[:diff_chars] + f"\n… cut: {cut}.\n"
            lines += [
                "<details><summary>Sources against the last indexed version</summary>",
                "",
                *fenced(text, "diff"),
                "</details>",
                "",
            ]
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def main():
    launcher, base = sys.argv[1], sys.argv[2]
    takeover = "takeover" in labels()
    problems, summary = [], ["# Index check", ""]

    added = []
    for line in git("diff", "--name-status", "--no-renames", base, "HEAD").splitlines():
        status, path = line.split("\t", 1)
        if not ENTRY_PATH.fullmatch(path):
            problems.append(f"{path}: a pull request only touches mods/<id>/<version>.json")
        elif status == "A":
            added.append(path)
        elif status == "M":
            problem = check_yank(base, path)
            if problem:
                problems.append(problem)
            else:
                summary += [f"## Yanks {path}", "", *fenced(json.loads(Path(path).read_text())["yanked"]), ""]
        else:
            problems.append(f"{path}: an indexed entry is never removed")

    with tempfile.TemporaryDirectory() as temp:
        base_dir = Path(temp) / "base"
        git("worktree", "add", "--detach", str(base_dir), base)
        try:
            for number, path in enumerate(added):
                work = Path(temp) / f"work{number}"
                work.mkdir()
                command = [launcher, "--check-entry", path, str(base_dir), str(work)]
                if takeover:
                    command.append("--takeover")
                result = subprocess.run(command, capture_output=True, text=True)
                output = (result.stdout + result.stderr).strip()
                print(output)
                if result.returncode != 0:
                    problems.append(f"{path}: the launcher's checks failed")
                summary += [f"## {path}", "", *fenced(output), ""]
                if (work / "new").is_dir():
                    summary.append(Sources(diff(work)))
        finally:
            git("worktree", "remove", "--force", str(base_dir))

    if not added and not problems and len(summary) == 2:
        problems.append("the pull request changes no entry")
    summary += ["## Result", ""] + ([f"- {problem}" for problem in problems] or ["Every check passed."])
    report = render(summary, MAX_DIFF_CHARS, "read the zips themselves")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a") as file:
            file.write(report)
    else:
        print(report)
    comment = os.environ.get("REPORT")
    if comment:
        diffs = sum(isinstance(line, Sources) for line in summary)
        Path(comment).parent.mkdir(parents=True, exist_ok=True)
        Path(comment).write_text(render(summary, COMMENT_DIFF_CHARS // max(1, diffs), "the whole diff is in the run's summary"))
    for problem in problems:
        print(f"::error::{problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
