"""Draft posts about what an agent shipped.

When an agent pane starts working, the hook remembers the HEAD and branch of the repository
the pane itself is in. When it is done, the commits made since then on that branch become a
draft for that repository; a draft that is still waiting grows with the next turn's commits.
A turn drafts nothing when it made no commit, when the branch changed, or when history was
rewritten (a rebase, an amend): then "since then" no longer means "what the agent did".
Drafts are never posted by the hook: you review and edit them in the composer.
"""

import hashlib
import os
import re
import subprocess
import time
from pathlib import Path

from . import herdr, paths
from .text import clean

LIMIT = 4000
DONE = ("done", "idle")
# A turn this old was not seen to end (the agent died, the pane closed): it is dropped.
TURN_TTL = 12 * 3600


def _git(cwd, *args):
    try:
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _is_ancestor(repo, base, head):
    try:
        return subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", base, head], capture_output=True, timeout=10
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _turns_path():
    return paths.state_dir() / "turns.json"


def drafts_dir():
    path = paths.state_dir() / "drafts"
    path.mkdir(exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _draft_path(repo):
    name = re.sub(r"[^A-Za-z0-9._-]", "-", Path(repo).name) or "repo"
    return drafts_dir() / f"{name}-{hashlib.sha256(repo.encode()).hexdigest()[:8]}.json"


def web_url(repo):
    """The repository's https link on GitHub, GitLab or the like, when origin has one."""
    remote = _git(repo, "remote", "get-url", "origin")
    if not remote:
        return None
    match = re.match(r"^(?:git@|ssh://git@)([^:/]+)[:/](.+?)(?:\.git)?/?$", remote)
    if match:
        return f"https://{match.group(1)}/{match.group(2)}"
    match = re.match(r"^https://(?:[^@/]+@)?(.+?)(?:\.git)?/?$", remote)
    return f"https://{match.group(1)}" if match else None


CONVENTIONAL = re.compile(
    r"^(feat|fix|perf|refactor|docs|chore|test|tests|build|ci|style|revert)(\([^)]*\))?!?:\s*", re.IGNORECASE
)
ADDED = re.compile(r"^(add|adds|added|introduce|introduces|ship|ships)\s+", re.IGNORECASE)


def _lead(subjects):
    """The change a post leads with: the first that adds something, else the latest."""
    count = len(subjects)
    subjects = [CONVENTIONAL.sub("", subject).strip() for subject in subjects]
    subjects = [subject for subject in subjects if subject] or ["some changes"]
    return next((subject for subject in subjects if ADDED.match(subject)), subjects[-1]), count


def _lower_first(text):
    """"A popup" → "a popup", but "API keys" stays "API keys"."""
    return text[:1].lower() + text[1:] if text[1:2].islower() or len(text) == 1 else text


def render(draft):
    """A draft post about what was shipped, within /dev/null's 4000 characters.

    It reads like a post, not a git log: you edit it into your own words before posting.
    """
    if isinstance(draft.get("edited"), str):
        return draft["edited"][:LIMIT]  # your own words, from a review the composer couldn't finish
    name = clean(draft["name"])[:100]
    lead, count = _lead([clean(subject) for subject in draft["subjects"]])
    # A huge subject is cut, so the rest of the post (and the link) always fits.
    lead = lead if len(lead) <= 500 else lead[:499] + "…"
    added = ADDED.match(lead)
    if added:
        text = f"Just shipped {_lower_first(lead[added.end():])} in {name} 🚀"
    else:
        text = f"Just shipped in {name}: {lead[:1].upper() + lead[1:]} 🚀"
    if count > 1:
        text += f"\n\nPlus {count - 1} more change{'s' if count > 2 else ''}."
    if draft.get("url"):
        text += f"\n\n{clean(draft['url'])}"
    return text[:LIMIT]


def _where(cwd):
    """(repo, head, branch) of a directory, or None outside a repository with commits."""
    repo = cwd and _git(cwd, "rev-parse", "--show-toplevel")
    head = repo and _git(repo, "rev-parse", "HEAD")
    if not head:
        return None
    return repo, head, _git(repo, "symbolic-ref", "-q", "--short", "HEAD") or ""


def _record(repo, base, head):
    """Adds the commits base..head to the repository's draft. Returns the draft, or None.

    --first-parent keeps out what a merge or pull brought in: only commits made on the branch.
    """
    log = _git(repo, "log", "--reverse", "--first-parent", "--no-merges", "--format=%H %s", f"{base}..{head}")
    if not log:
        return None
    path = _draft_path(repo)
    draft = paths.read_json(path, None)
    if not isinstance(draft, dict) or draft.get("repo") != repo or not _is_ancestor(repo, draft.get("base", ""), head):
        draft = {"repo": repo, "name": Path(repo).name, "base": base, "subjects": [], "commits": []}
    # Two agents in one repository report overlapping turns: each commit is drafted once.
    seen = set(draft.setdefault("commits", []))
    new = [line.split(" ", 1) for line in log.splitlines()]
    new = [(sha, subject[0] if subject else "") for sha, *subject in new if sha not in seen]
    if not new:
        return None
    draft["commits"] += [sha for sha, _ in new]
    draft["subjects"] += [subject for _, subject in new]
    draft["head"] = head
    draft["shortstat"] = _git(repo, "diff", "--shortstat", f"{draft['base']}..{head}") or ""
    draft["url"] = web_url(repo)
    draft["updated"] = time.time()
    paths.write_json(path, draft)
    return draft


def handle(event, cwd_of=herdr.pane_cwd, notify=herdr.notify, now=time.time):
    """Handles one pane.agent_status_changed event. Returns the draft it made, if any."""
    data = event.get("data") if isinstance(event, dict) else None
    if not isinstance(data, dict) or not paths.config()["drafts"]:
        return None
    pane, status = data.get("pane_id"), data.get("agent_status")
    if not isinstance(pane, str) or not pane or not isinstance(status, str):
        return None

    if status == "working":
        # Only the agent pane's own directory: another pane's repository would draft its commits.
        where = _where(cwd_of(pane))
        with paths.lock():
            turns = _turns()
            turn = turns.get(pane)
            # Working → blocked → working is one turn: keep where it started, unless it moved.
            if turn and now() - turn["at"] < TURN_TTL and where and turn["repo"] == where[0]:
                return None
            if where:
                turns[pane] = {"repo": where[0], "head": where[1], "branch": where[2], "at": now()}
            elif turn and now() - turn["at"] < TURN_TTL:
                return None  # herdr or git didn't answer this time: keep the turn under way
            else:
                turns.pop(pane, None)
            paths.write_json(_turns_path(), turns)
        return None

    if status not in DONE and status != "unknown":
        return None
    with paths.lock():
        turns = _turns()
        turn = turns.pop(pane, None)
        if turn is None:
            return None
        paths.write_json(_turns_path(), turns)
    if status == "unknown" or now() - turn["at"] >= TURN_TTL:
        return None

    where = _where(turn["repo"])
    if not where or where[1] == turn["head"] or where[2] != turn["branch"]:
        return None
    if not _is_ancestor(turn["repo"], turn["head"], where[1]):
        return None
    with paths.lock():
        draft = _record(turn["repo"], turn["head"], where[1])
    if draft:
        notify("Draft post ready", f"About what you shipped in {draft['name']}. Review it with “Review /dev/null draft”.")
    return draft


def _turns():
    turns = paths.read_json(_turns_path(), {})
    return {
        pane: turn
        for pane, turn in turns.items()
        if isinstance(turn, dict) and {"repo", "head", "branch", "at"} <= turn.keys()
    }


def newest():
    """(path, draft) of the most recently updated draft, or (None, None)."""
    drafts = [(path, paths.read_json(path, None)) for path in drafts_dir().glob("*.json")]
    drafts = [(path, draft) for path, draft in drafts if isinstance(draft, dict) and draft.get("subjects")]
    if not drafts:
        return None, None
    return max(drafts, key=lambda item: item[1].get("updated", 0))


def save_edit(path, text):
    """Keeps what you wrote while reviewing a draft, so the next review starts from it."""
    with paths.lock():
        draft = paths.read_json(path, None)
        if isinstance(draft, dict):
            draft["edited"] = text
            paths.write_json(path, draft)


def count():
    return len(list(drafts_dir().glob("*.json")))


def discard(path, seen):
    """Removes a draft, unless commits were added to it after `seen` was read.

    Returns False when the draft grew: its new commits have not been reviewed, so it is kept.
    """
    with paths.lock():
        current = paths.read_json(path, None)
        if isinstance(current, dict) and current.get("updated") != seen.get("updated"):
            return False
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    return True
