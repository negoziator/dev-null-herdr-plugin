import json
import os
import stat
import unittest

from devnull_herdr import cli, drafts, paths
from tests.helpers import Isolated


def event(status, pane="w1:p1"):
    return {"event": "pane_agent_status_changed", "data": {"pane_id": pane, "agent_status": status}}


class HookTest(Isolated):
    def setUp(self):
        super().setUp()
        self.repo = self.git_repo()
        self.notes = []
        self.clock = 1_000_000.0

    def handle(self, status, pane="w1:p1", cwd=None):
        return drafts.handle(
            event(status, pane),
            cwd_of=lambda _: None if cwd is False else str(cwd or self.repo),
            notify=lambda *args, **kwargs: self.notes.append(args),
            now=lambda: self.clock,
        )

    def test_drafts_the_commits_of_a_turn(self):
        self.handle("working")
        self.commit(self.repo, "Add the composer")
        self.commit(self.repo, "Fix the hook")
        draft = self.handle("done")
        self.assertEqual(draft["subjects"], ["Add the composer", "Fix the hook"])
        self.assertIn("2 files changed", draft["shortstat"])
        self.assertEqual(len(self.notes), 1)
        self.assertEqual(self.notes[0][0], "Draft post ready")
        self.assertIn("project", self.notes[0][1])

    def test_a_turn_without_commits_drafts_nothing(self):
        self.handle("working")
        self.assertIsNone(self.handle("done"))
        self.assertEqual(self.notes, [])
        self.assertEqual(drafts.count(), 0)

    def test_done_without_a_start_drafts_nothing(self):
        self.commit(self.repo, "Before herdr saw it")
        self.assertIsNone(self.handle("done"))

    def test_blocked_keeps_the_start_of_the_turn(self):
        self.handle("working")
        self.commit(self.repo, "First")
        self.handle("blocked")
        self.handle("working")
        self.commit(self.repo, "Second")
        self.assertEqual(self.handle("done")["subjects"], ["First", "Second"])

    def test_a_waiting_draft_grows(self):
        for subject in ("One", "Two"):
            self.handle("working")
            self.commit(self.repo, subject)
            draft = self.handle("idle")
        self.assertEqual(draft["subjects"], ["One", "Two"])
        self.assertEqual(drafts.count(), 1)

    def test_panes_are_tracked_separately(self):
        other = self.git_repo("other")
        self.handle("working", pane="a")
        self.handle("working", pane="b", cwd=other)
        self.commit(other, "Other work")
        self.assertIsNone(self.handle("done", pane="a"))
        self.assertEqual(self.handle("done", pane="b", cwd=other)["name"], "other")

    def test_outside_a_repository_does_nothing(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        self.handle("working", cwd=plain)
        self.assertIsNone(self.handle("done", cwd=plain))

    def test_an_unknown_pane_directory_records_nothing(self):
        self.handle("working", cwd=False)
        self.commit(self.repo, "Not the agent's")
        self.assertIsNone(self.handle("done"))

    def test_a_failed_lookup_keeps_the_turn_under_way(self):
        self.handle("working")
        self.commit(self.repo, "First")
        self.handle("blocked")
        self.handle("working", cwd=False)
        self.commit(self.repo, "Second")
        self.assertEqual(self.handle("done")["subjects"], ["First", "Second"])

    def test_overlapping_turns_draft_each_commit_once(self):
        self.handle("working", pane="a")
        self.handle("working", pane="b")
        self.commit(self.repo, "Shared")
        self.handle("done", pane="a")
        self.commit(self.repo, "Only b's")
        self.assertEqual(self.handle("done", pane="b")["subjects"], ["Shared", "Only b's"])

    def test_turned_off(self):
        paths.save_config({"drafts": False})
        self.handle("working")
        self.commit(self.repo, "Hidden")
        self.assertIsNone(self.handle("done"))

    def test_unknown_ends_the_turn(self):
        self.handle("working")
        self.handle("unknown")
        self.commit(self.repo, "Later, by hand")
        self.handle("working")
        self.assertIsNone(self.handle("done"))

    def test_a_stale_turn_is_replaced(self):
        self.handle("working")
        self.commit(self.repo, "Days of your own work")
        self.clock += drafts.TURN_TTL + 1
        self.handle("working")
        self.commit(self.repo, "The agent's")
        self.assertEqual(self.handle("done")["subjects"], ["The agent's"])

    def test_a_stale_turn_drafts_nothing(self):
        self.handle("working")
        self.commit(self.repo, "Whatever")
        self.clock += drafts.TURN_TTL + 1
        self.assertIsNone(self.handle("done"))

    def test_moving_to_another_repository_starts_over(self):
        other = self.git_repo("other")
        self.handle("working")
        self.handle("working", cwd=other)
        self.commit(other, "There")
        self.assertEqual(self.handle("done")["name"], "other")

    def test_a_branch_switch_drafts_nothing(self):
        self.handle("working")
        self.git(self.repo, "checkout", "-q", "-b", "feature")
        self.commit(self.repo, "On another branch")
        self.assertIsNone(self.handle("done"))

    def test_rewritten_history_drafts_nothing(self):
        self.commit(self.repo, "To be amended")
        self.handle("working")
        self.git(self.repo, "commit", "-q", "--amend", "-m", "Amended")
        self.assertIsNone(self.handle("done"))

    def test_a_pull_drafts_only_the_branch_own_commits(self):
        upstream = self.git_repo("upstream")
        self.git(self.repo, "pull", "-q", "--no-rebase", "--allow-unrelated-histories", "--no-edit", str(upstream), "main")
        self.handle("working")
        self.commit(upstream, "Someone else's", "theirs.txt")
        self.commit(self.repo, "Mine", "mine.txt")
        self.git(self.repo, "pull", "-q", "--no-rebase", "--no-edit", str(upstream), "main")
        self.assertEqual(self.handle("done")["subjects"], ["Mine"])

    def test_newest(self):
        self.assertEqual(drafts.newest(), (None, None))
        self.handle("working")
        self.commit(self.repo, "Ship")
        self.handle("done")
        path, draft = drafts.newest()
        self.assertEqual(draft["subjects"], ["Ship"])

    def test_files_are_private(self):
        self.handle("working")
        self.commit(self.repo, "Secret project")
        self.handle("done")
        path, _ = drafts.newest()
        for checked in (path, paths.state_dir() / "turns.json", paths.state_dir() / ".lock"):
            self.assertEqual(stat.S_IMODE(os.stat(checked).st_mode), 0o600, checked)
        self.assertEqual(stat.S_IMODE(os.stat(paths.state_dir()).st_mode), 0o700)


class DiscardTest(Isolated):
    def test_a_draft_that_grew_is_kept(self):
        path = drafts.drafts_dir() / "x.json"
        paths.write_json(path, {"subjects": ["A"], "updated": 1})
        seen = paths.read_json(path, {})
        paths.write_json(path, {"subjects": ["A", "B"], "updated": 2})
        self.assertFalse(drafts.discard(path, seen))
        self.assertTrue(path.exists())
        self.assertTrue(drafts.discard(path, paths.read_json(path, {})))
        self.assertFalse(path.exists())


class OddInputTest(Isolated):
    def run_hook(self, event_json, turns=None):
        if turns is not None:
            (paths.state_dir() / "turns.json").write_text(turns)
        os.environ["HERDR_PLUGIN_EVENT_JSON"] = event_json
        return cli.main(["hook"])

    def test_never_fails(self):
        for event_json in ["", "not json", "[]", "null", '{"data": []}', '{"data": {"pane_id": 1, "agent_status": "done"}}',
                           '{"data": {"pane_id": "p", "agent_status": ["done"]}}']:
            self.assertEqual(self.run_hook(event_json), 0, event_json)

    def test_broken_state_files(self):
        done = json.dumps(event("done"))
        for turns in ["[]", "{", '{"w1:p1": "nope"}', '{"w1:p1": {"repo": "/x"}}']:
            self.assertEqual(self.run_hook(done, turns), 0, turns)
        (paths.config_dir() / "config.json").write_text("[]")
        self.assertEqual(self.run_hook(done), 0)


class RenderTest(Isolated):
    def test_reads_like_a_post(self):
        text = drafts.render(
            {"name": "app", "subjects": ["Fix the hook", "Add a popup composer"], "url": "https://github.com/me/app"}
        )
        self.assertEqual(text, "Just shipped a popup composer in app 🚀\n\nPlus 1 more change.\n\nhttps://github.com/me/app")

    def test_leads_with_the_latest_when_nothing_was_added(self):
        text = drafts.render({"name": "api", "subjects": ["Tidy up", "fix(api): handle empty payloads"]})
        self.assertEqual(text, "Just shipped in api: Handle empty payloads 🚀\n\nPlus 1 more change.")

    def test_conventional_prefixes_are_dropped(self):
        self.assertEqual(drafts.render({"name": "x", "subjects": ["feat!: add dark mode"]}), "Just shipped dark mode in x 🚀")

    def test_acronyms_and_ordinary_colons_are_kept(self):
        self.assertEqual(drafts.render({"name": "x", "subjects": ["Add API keys"]}), "Just shipped API keys in x 🚀")
        self.assertEqual(drafts.render({"name": "x", "subjects": ["README: new badge"]}),
                         "Just shipped in x: README: new badge 🚀")

    def test_a_huge_subject_keeps_the_link(self):
        text = drafts.render({"name": "x", "subjects": ["Add " + "y" * 5000], "url": "https://github.com/me/x"})
        self.assertTrue(text.endswith("https://github.com/me/x"))
        self.assertLessEqual(len(text), 4000)

    def test_a_huge_name_keeps_the_link(self):
        text = drafts.render({"name": "n" * 5000, "subjects": ["Add x"], "url": "https://github.com/me/x"})
        self.assertTrue(text.endswith("https://github.com/me/x"))

    def test_no_git_log_in_the_post(self):
        text = drafts.render({"name": "x", "subjects": ["Add a", "b", "c"], "shortstat": "3 files changed"})
        self.assertNotIn("files changed", text)
        self.assertNotIn("- ", text)
        self.assertIn("Plus 2 more changes.", text)

    def test_escape_sequences_never_reach_the_draft(self):
        text = drafts.render({"name": "app\x1b[2K", "subjects": ["fix\x1b[8m my-token"]})
        self.assertNotIn("\x1b", text)
        self.assertIn("Fix[8m my-token", text)

    def test_invisible_reordering_characters_never_reach_the_draft(self):
        text = drafts.render({"name": "app", "subjects": ["Ship pair 👨\u200d💻\u202e\u200b"]})
        self.assertEqual(text, "Just shipped pair 👨\u200d💻 in app 🚀")

    def test_never_over_the_limit(self):
        self.assertLessEqual(len(drafts.render({"name": "app", "subjects": ["x" * 3000] * 3})), 4000)

    def test_web_url(self):
        repo = self.git_repo()
        for remote, expected in [
            ("git@github.com:negoziator/dev-null-herdr-plugin.git", "https://github.com/negoziator/dev-null-herdr-plugin"),
            ("https://github.com/a/b.git", "https://github.com/a/b"),
            ("https://token@gitlab.com/a/b", "https://gitlab.com/a/b"),
            ("ssh://git@github.com/a/b.git", "https://github.com/a/b"),
        ]:
            self.git(repo, "remote", "remove", "origin") if "origin" in self.git(repo, "remote") else None
            self.git(repo, "remote", "add", "origin", remote)
            self.assertEqual(drafts.web_url(str(repo)), expected, remote)


if __name__ == "__main__":
    unittest.main()
