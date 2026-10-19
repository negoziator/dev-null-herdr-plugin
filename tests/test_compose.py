import os
import sys
import unittest
from unittest import mock

from devnull_herdr import compose, drafts, paths, script
from tests.helpers import Isolated

FAKE_EDITOR = """\
import os, sys
path = sys.argv[1]
text = os.environ.get("FAKE_EDITOR_TEXT")
if text is not None:
    original = open(path).read()
    open(path, "w").write(text + original[original.index("\\n\\n# ---"):])
sys.exit(int(os.environ.get("FAKE_EDITOR_STATUS", "0")))
"""


class MessageTest(unittest.TestCase):
    def test_keeps_everything_above_the_scissors(self):
        text = compose.template("#laravel is great\n\n# heading too") + "ignored\n"
        self.assertEqual(compose.message(text), "#laravel is great\n\n# heading too")

    def test_without_scissors_keeps_all(self):
        self.assertEqual(compose.message("  hello  \n\n"), "hello")

    def test_control_characters_are_removed(self):
        self.assertEqual(compose.message("hi\x1b[8m hidden\r\nthere\x07"), "hi[8m hidden\nthere")

    def test_problems(self):
        self.assertEqual(compose.problem(""), "Empty commit.")
        self.assertIn("at least 3", compose.problem("hi"))
        self.assertIsNone(compose.problem("hey"))
        self.assertIsNone(compose.problem("x" * 4000))
        self.assertIn("up to 4000", compose.problem("x" * 4001))


class RunTest(Isolated):
    def setUp(self):
        super().setUp()
        editor = self.tmp / "editor.py"
        editor.write_text(FAKE_EDITOR)
        os.environ["VISUAL"] = f"{sys.executable} {editor}"
        os.environ.pop("FAKE_EDITOR_TEXT", None)
        os.environ.pop("FAKE_EDITOR_STATUS", None)

    def answers(self, *keys):
        keys = iter(keys)
        return lambda prompt="": next(keys)

    def draft(self, **fields):
        path = drafts.drafts_dir() / "x.json"
        paths.write_json(path, {"name": "x", "subjects": ["A"], "updated": 1, **fields})
        return path, paths.read_json(path, {})

    def test_empty_commit_aborts(self):
        os.environ["FAKE_EDITOR_TEXT"] = ""
        self.assertEqual(compose.run(ask=self.answers()), 1)

    def test_starting_text_reaches_the_editor_and_back(self):
        with mock.patch.object(compose, "commit", return_value=0) as commit:
            self.assertEqual(compose.run("Shipped it", ask=self.answers("c")), 0)
        self.assertEqual(commit.call_args.args[0], "Shipped it")

    def test_a_failing_editor_aborts(self):
        os.environ["FAKE_EDITOR_STATUS"] = "1"
        with self.assertRaisesRegex(RuntimeError, "status 1"):
            compose.run("Shipped it", ask=self.answers("c"))
        self.assertEqual(list(paths.state_dir().glob("COMMIT_EDITMSG*")), [])

    def test_a_missing_editor_is_reported(self):
        os.environ["VISUAL"] = "/nonexistent/editor"
        with self.assertRaisesRegex(RuntimeError, "Could not start the editor"):
            compose.edit("x")
        os.environ["VISUAL"] = "'unbalanced"
        with self.assertRaisesRegex(RuntimeError, "Cannot read the editor command"):
            compose.edit("x")

    def test_main_keeps_errors_on_screen(self):
        os.environ["VISUAL"] = "/nonexistent/editor"
        args = mock.Mock(reply=None, image=None, text=None, draft=False, dry_run=False)
        with mock.patch.object(compose, "_wait") as wait:
            self.assertEqual(compose.main(args), 1)
        wait.assert_called_once()

    def test_too_long_cannot_be_committed(self):
        os.environ["FAKE_EDITOR_TEXT"] = "x" * 4001
        with mock.patch.object(compose, "commit") as commit:
            self.assertEqual(compose.run(ask=self.answers("c", "q")), 1)
        commit.assert_not_called()

    def test_edit_again(self):
        with mock.patch.object(compose, "edit", side_effect=["no", "yes please"]):
            with mock.patch.object(compose, "commit", return_value=0) as commit:
                compose.run(ask=self.answers("e", "c"))
        self.assertEqual(commit.call_args.args[0], "yes please")

    def test_view_shows_all_of_it(self):
        with mock.patch.object(compose, "_view") as view, mock.patch.object(compose, "commit", return_value=0):
            compose.run("line\n" * 100, ask=self.answers("v", "c"))
        self.assertEqual(view.call_args.args[0].count("line"), 100)

    def test_a_long_single_line_is_cut_to_fit(self):
        size = os.terminal_size((84, 26))
        with mock.patch("shutil.get_terminal_size", return_value=size), mock.patch("builtins.print") as printed:
            compose.preview("x" * 4000, None, [])
        out = "\n".join(str(call.args[0]) for call in printed.call_args_list if call.args)
        self.assertIn("press v", out)
        self.assertEqual(out.count("x"), (26 - 9) * 84)

    def test_dry_run_sends_nothing(self):
        with mock.patch.object(script, "commit") as real, mock.patch.object(script, "check_for_update") as check:
            self.assertEqual(compose.run("Hello world", dry_run=True, ask=self.answers("c")), 0)
        real.assert_not_called()
        check.assert_not_called()

    def test_end_of_input_at_the_update_prompt_commits_nothing(self):
        def eof(prompt=""):
            raise EOFError

        with mock.patch.object(script, "fetch_remote", return_value=b"#!/bin/sh\n# changed\n"):
            with mock.patch.object(script, "commit") as real:
                with self.assertRaises(EOFError):
                    compose.commit("Hello world", None, [], None, False, ask=eof)
        real.assert_not_called()

    def test_interrupted_commit_may_have_been_sent(self):
        draft = self.draft()
        with mock.patch.object(script, "check_for_update"), mock.patch.object(script, "commit", side_effect=KeyboardInterrupt):
            with mock.patch("builtins.print") as printed:
                self.assertEqual(compose.commit("Hello world", None, [], draft, False), 130)
        self.assertIn("may have been sent", printed.call_args.args[0])
        self.assertTrue(draft[0].exists())

    def test_discarding_a_draft_removes_it(self):
        draft = self.draft()
        self.assertEqual(compose.run("Shipped", draft=draft, ask=self.answers("d")), 1)
        self.assertFalse(draft[0].exists())

    def test_a_committed_draft_is_removed_a_failed_one_kept(self):
        draft = self.draft()
        with mock.patch.object(script, "check_for_update"):
            with mock.patch.object(script, "commit", return_value=(False, None, "Slow down")):
                compose.commit("Shipped", None, [], draft, False)
            self.assertTrue(draft[0].exists())
            with mock.patch.object(script, "commit", return_value=(True, "https://dev-null.sh/@me/commits/1", "")):
                compose.commit("Shipped", None, [], draft, False)
        self.assertFalse(draft[0].exists())

    def test_a_draft_that_grew_during_review_is_kept(self):
        draft = self.draft()
        paths.write_json(draft[0], {**draft[1], "subjects": ["A", "B"], "updated": 2})
        with mock.patch.object(script, "check_for_update"):
            with mock.patch.object(script, "commit", return_value=(True, "https://dev-null.sh/@me/commits/1", "")):
                compose.commit("Shipped", None, [], draft, False)
        self.assertTrue(draft[0].exists())


if __name__ == "__main__":
    unittest.main()
