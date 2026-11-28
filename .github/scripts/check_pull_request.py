#!/usr/bin/env python3
# Copyright (c) Tutez. GPLv3: see .github/LICENSE.
"""Checks a pull request to the index.

It may only add mods/<id>/<version>.json files, each checked by the
launcher (--check-entry), or add `yanked` to an indexed one. Writes what the
reviewer reads, the sources' diff against the last indexed version included,
to the job summary, and with the diffs cut shorter to the file $REPORT, which
the Comment workflow posts on the pull request.

After a push, <before> is the branch's head before it: what the push changed
in each entry's sources, against the entry the pull request held then, goes to
the job summary too and to the file $PUSH_REPORT, posted as a comment of its
own. A reviewer who read that entry reads this diff rather than the whole one.

Beside $REPORT, meta.json tells the Comment workflow which pull request this
run checked and the head it had before the push, so that a run is only ever
posted on its own pull request, and a push whose report was never posted is
said.

Usage: check_pull_request.py <launcher> <base> [<before>]
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
# git's empty tree, which every repository knows: read as the source of the attributes, no
# .gitattributes applies.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class Sources(str):
    """The sources' diff of an entry in the report, cut to fit where the report goes."""

    title = "Sources against the last indexed version"


def sources(text, title):
    block = Sources(text)
    block.title = title
    return block


def fenced(text, info=""):
    """Text from the pull request as a code block it cannot close: a mod's sources must not
    turn the rest of the report into Markdown."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return [fence + info, text, fence]


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def pull_request():
    """The pull request of the event this run is for; empty run by hand."""
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return {}
    return json.loads(Path(event_path).read_text()).get("pull_request", {})


def labels():
    return {label["name"] for label in pull_request().get("labels", [])}


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
    return diff_folders(previous, work / "new")


def diff_folders(previous, new):
    """Every file that differs, whole: the reviewer reads this in place of the zip, so the zip
    must not choose what it shows. No rename: a file moved as it is, into src/ say, would be a
    line naming it, without its content. No attribute but git's own: a .gitattributes in the zip
    would have its sources shown as binary files that differ. Windows line ends read as the
    others: a version whose line ends changed would show every line of its files, and the
    launcher refuses them in a new zip."""
    result = subprocess.run(
        ["git", f"--attr-source={EMPTY_TREE}", "diff", "--no-index", "--no-color", "--no-renames", "--ignore-cr-at-eol", "--",
         str(previous), str(new)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    # 1 is git's answer when the folders differ. Anything else wrote no diff: stop there, since
    # an empty diff would read as "nothing changed".
    if result.returncode not in (0, 1):
        sys.exit(f"git diff failed ({result.returncode}): {result.stderr.strip()}")
    # git writes each path under its temporary folder, after a/ or b/: keep it relative to its
    # version.
    text = result.stdout
    for root in (previous, new):
        text = text.replace(str(root) + "/", "/")
    return text


def unshown(text):
    """The sources a diff names without showing them. git only says that a file it takes for
    binary differs, and takes a file for binary as soon as it holds a NUL character, which the
    game reads as the end of the source: the code before it would run unread."""
    lines = []
    for line in text.splitlines():
        if line.startswith("Binary files ") and line.endswith(" differ"):
            lowered = line.lower()
            if "/src/" in lowered or ".hx" in lowered:
                lines.append(line)
    return lines


def readable(commit):
    """Whether a commit is there to read, fetched if need be: a forced push leaves the old head
    out of the branch."""
    def known():
        return subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], capture_output=True).returncode == 0

    if not known():
        subprocess.run(["git", "fetch", "--quiet", "origin", commit], capture_output=True)
    return known()


def entries_at(commit, folder):
    listed = git("ls-tree", "-r", "--name-only", commit, "--", folder + "/").splitlines()
    return {path for path in listed if ENTRY_PATH.fullmatch(path)}


def held_before(before, base, path):
    """The entry of this mod the pull request held at `before`: this path again when its content
    changed, else its highest other version. None when the push left this entry as it was, or
    when the pull request had none for this mod."""
    folder = str(Path(path).parent)
    held = entries_at(before, folder) - entries_at(base, folder)
    if path in held:
        return None if git("show", f"{before}:{path}") == Path(path).read_text() else path
    return max(held, key=lambda entry: tuple(int(part) for part in Path(entry).stem.split(".")), default=None)


def since_push(launcher, before, base, base_dir, before_dir, path, new, work):
    """What the push changed in an entry's sources, as report lines; none when it changed nothing."""
    old = held_before(before, base, path)
    if old is None:
        return []
    lines = [f"## {path}", ""]
    # Its zip is checked against the size and sha256 that entry gave, as any entry's. The entry is
    # named by its whole path: run as an extracted AppImage, the launcher reads a relative one
    # from the folder this script runs in, where the path holds the pull request's entry or none.
    result = subprocess.run(
        [launcher, "--check-entry", str(before_dir / old), str(base_dir), str(work)], capture_output=True, text=True
    )
    version = Path(old).stem
    if not (work / "new").is_dir():
        redone = " A release redone under the same version replaces its zip." if old == path else ""
        said = (result.stdout + result.stderr).replace(f"{before_dir}{os.sep}", "").strip()
        return lines + [
            f"Before the push this pull request held `{old}`, whose zip could not be read again: "
            f"there is nothing to compare with.{redone} The diff against the last indexed version stands.",
            "",
            *fenced(said),
            "",
        ]
    text = diff_folders(work / "new", new)
    if not text.strip():
        return lines + [f"Its sources are those of `{old}`, which this pull request held before the push: only the entry changed.", ""]
    again = " as it was" if old == path else ""
    return lines + [
        f"Against `{old}`{again}, which this pull request held before the push.",
        "",
        sources(text, f"Sources against {version}{again}, before the push"),
    ]


def render(summary, diff_chars, cut):
    """The report, each diff cut past `diff_chars`, saying `cut` where it is."""
    lines = []
    for line in summary:
        if isinstance(line, Sources):
            text = line if len(line) <= diff_chars else line[:diff_chars] + f"\n… cut: {cut}.\n"
            lines += [
                f"<details><summary>{line.title}</summary>",
                "",
                *fenced(text, "diff"),
                "</details>",
                "",
            ]
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def main():
    launcher, base = os.path.abspath(sys.argv[1]), sys.argv[2]
    before = sys.argv[3] if len(sys.argv) > 3 else ""
    takeover = "takeover" in labels()
    problems, summary, pushed = [], ["# Index check", ""], []
    comment = os.environ.get("REPORT")
    if comment:
        # First, so that it is there whatever happens next.
        Path(comment).parent.mkdir(parents=True, exist_ok=True)
        meta = {"pull": pull_request().get("number"), "before": before}
        (Path(comment).parent / "meta.json").write_text(json.dumps(meta) + "\n")
    if before and not readable(before):
        pushed += [f"The head before the push, `{before[:12]}`, can no longer be read: the diff against the last indexed version stands.", ""]
        before = ""

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
        before_dir = Path(temp) / "before"
        if before:
            git("worktree", "add", "--detach", str(before_dir), before)
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
                    text = diff(work)
                    for line in unshown(text):
                        problems.append(f"{path}: a source is not shown in the diff, which only says: {line}")
                    summary.append(Sources(text))
                    if before:
                        held = Path(temp) / f"held{number}"
                        held.mkdir()
                        pushed += since_push(launcher, before, base, base_dir, before_dir, path, work / "new", held)
        finally:
            git("worktree", "remove", "--force", str(base_dir))
            if before:
                git("worktree", "remove", "--force", str(before_dir))

    if not added and not problems and len(summary) == 2:
        problems.append("the pull request changes no entry")
    summary += ["## Result", ""] + ([f"- {problem}" for problem in problems] or ["Every check passed."])
    if pushed:
        pushed = ["# Since the last push", ""] + pushed
    report = render(summary + pushed, MAX_DIFF_CHARS, "read the zips themselves")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a") as file:
            file.write(report)
    else:
        print(report)
    if comment:
        diffs = sum(isinstance(line, Sources) for line in summary)
        Path(comment).write_text(render(summary, COMMENT_DIFF_CHARS // max(1, diffs), "the whole diff is in the run's summary"))
    push_comment = os.environ.get("PUSH_REPORT")
    if push_comment and pushed:
        diffs = sum(isinstance(line, Sources) for line in pushed)
        Path(push_comment).parent.mkdir(parents=True, exist_ok=True)
        Path(push_comment).write_text(render(pushed, COMMENT_DIFF_CHARS // max(1, diffs), "the whole diff is in the run's summary"))
    for problem in problems:
        print(f"::error::{problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
