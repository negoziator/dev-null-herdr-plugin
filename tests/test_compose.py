import unittest
from unittest import mock

from devnull_herdr import compose, drafts, paths, script
from tests.helpers import Isolated


class MessageTest(unittest.TestCase):
    def test_trims_and_cleans(self):
        self.assertEqual(compose.message("  #laravel is great  \n\n"), "#laravel is great")
        self.assertEqual(compose.message("hi\x1b[8m hidden\r\nthere\x07"), "hi[8m hidden\nthere")

    def test_problems(self):
        self.assertIn("Write something", compose.problem(""))
        self.assertIn("at least 3", compose.problem("hi"))
        self.assertIsNone(compose.problem("hey"))
        self.assertIsNone(compose.problem("x" * 4000))
        self.assertIn("1 characters too long", compose.problem("x" * 4001))

    def test_header(self):
        self.assertEqual(compose.header(None, [], None, False), "/dev/null · new post")
        self.assertEqual(compose.header("01a1-uuid", [], None, False, "@nuno: hi"), "/dev/null · reply to @nuno: hi")
        self.assertEqual(
            compose.header("0be0130", ["a.png"], ("p", {}), True),
            "/dev/null · reply to 0be0130 (draft) · 1 image · DRY RUN, nothing is sent",
        )


class RunTest(Isolated):
    def ui(self, *results):
        """A text box that answers with `results`, one per time it is shown."""
        results, self.shown = iter(results), []

        def show(text, title, draft, rescue=None):
            self.shown.append((text, title, draft))
            self.rescue = rescue
            return next(results)

        return show

    def answers(self, *keys):
        keys = iter(keys)
        return lambda prompt="": next(keys)

    def draft(self, **fields):
        path = drafts.drafts_dir() / "x.json"
        paths.write_json(path, {"name": "x", "subjects": ["A"], "updated": 1, **fields})
        return path, paths.read_json(path, {})

    def test_closing_posts_nothing(self):
        with mock.patch.object(compose, "post") as post:
            self.assertEqual(compose.run(ui=self.ui((compose.CANCEL, "half a thought"))), 1)
        post.assert_not_called()

    def test_posts_what_the_box_shows(self):
        with mock.patch.object(compose, "post", return_value=0) as post:
            self.assertEqual(compose.run("Shipped it", ui=self.ui((compose.POST, "Shipped it! 🚀  "))), 0)
        self.assertEqual(self.shown[0][0], "Shipped it")
        self.assertEqual(post.call_args.args[0], "Shipped it! 🚀")

    def test_a_failed_post_can_be_edited_again(self):
        ui = self.ui((compose.POST, "First try"), (compose.POST, "Second try"))
        with mock.patch.object(compose, "post", side_effect=[1, 0]) as post:
            self.assertEqual(compose.run(ui=ui, ask=self.answers("e")), 0)
        self.assertEqual(self.shown[1][0], "First try")
        self.assertEqual(post.call_args.args[0], "Second try")

    def test_a_failed_post_can_be_left(self):
        with mock.patch.object(compose, "post", return_value=1):
            self.assertEqual(compose.run(ui=self.ui((compose.POST, "Nope")), ask=self.answers("")), 1)

    def test_dry_run_sends_nothing(self):
        with mock.patch.object(script, "commit") as real, mock.patch.object(script, "check_for_update") as check:
            self.assertEqual(compose.run(dry_run=True, ui=self.ui((compose.POST, "Hello world"))), 0)
        real.assert_not_called()
        check.assert_not_called()

    def test_the_dry_run_setting_reaches_the_composer(self):
        paths.save_config({"dry_run": True})
        args = mock.Mock(reply=None, image=None, text="Hello world", draft=False, dry_run=False)
        with mock.patch.object(compose, "_wait"), mock.patch.object(compose, "run", return_value=0) as run:
            self.assertEqual(compose.main(args), 0)
        self.assertTrue(run.call_args.args[4])

    def test_no_terminal_is_reported(self):
        args = mock.Mock(reply=None, image=None, text="", draft=False, dry_run=False)
        with mock.patch.object(compose, "_wait") as wait, mock.patch("sys.stdin") as stdin:
            stdin.isatty.return_value = False
            self.assertEqual(compose.main(args), 1)
        wait.assert_called_once()

    def test_end_of_input_at_the_update_prompt_posts_nothing(self):
        def eof(prompt=""):
            raise EOFError

        with mock.patch.object(script, "fetch_remote", return_value=b"#!/bin/sh\n# changed\n"):
            with mock.patch.object(script, "commit") as real:
                with self.assertRaises(EOFError):
                    compose.post("Hello world", None, [], None, False, ask=eof)
        real.assert_not_called()

    def test_interrupted_post_may_have_been_sent(self):
        draft = self.draft()
        with mock.patch.object(script, "check_for_update"), mock.patch.object(script, "commit", side_effect=KeyboardInterrupt):
            with mock.patch("builtins.print") as printed:
                self.assertEqual(compose.post("Hello world", None, [], draft, False), 130)
        self.assertIn("may have been sent", printed.call_args.args[0])
        self.assertTrue(draft[0].exists())

    def test_pending_review_is_explained(self):
        url = "https://dev-null.sh/@me/commits/1"
        answer = f"Committed: {url} (pending review, only you can see it until it is approved)"
        with mock.patch.object(script, "check_for_update"), mock.patch.object(script, "commit", return_value=(True, url, answer)):
            with mock.patch("builtins.print") as printed:
                compose.post("Hello world", None, [], None, False)
        said = [call.args[0] for call in printed.call_args_list if call.args]
        self.assertIn(f"Posted: {url}", said)
        self.assertTrue(any("pending review" in line for line in said))

    def test_discarding_a_draft_removes_it(self):
        draft = self.draft()
        self.assertEqual(compose.run("Shipped", draft=draft, ui=self.ui((compose.DISCARD, "Shipped"))), 1)
        self.assertFalse(draft[0].exists())
        self.assertTrue(self.shown[0][2])

    def test_a_posted_draft_is_removed_a_failed_one_kept(self):
        draft = self.draft()
        with mock.patch.object(script, "check_for_update"):
            with mock.patch.object(script, "commit", return_value=(False, None, "Slow down")):
                compose.post("Shipped", None, [], draft, False)
            self.assertTrue(draft[0].exists())
            with mock.patch.object(script, "commit", return_value=(True, "https://dev-null.sh/@me/commits/1", "")):
                compose.post("Shipped", None, [], draft, False)
        self.assertFalse(draft[0].exists())

    def test_a_draft_that_grew_during_review_is_kept(self):
        draft = self.draft()
        paths.write_json(draft[0], {**draft[1], "subjects": ["A", "B"], "updated": 2})
        with mock.patch.object(script, "check_for_update"):
            with mock.patch.object(script, "commit", return_value=(True, "https://dev-null.sh/@me/commits/1", "")):
                compose.post("Shipped", None, [], draft, False)
        self.assertTrue(draft[0].exists())


if __name__ == "__main__":
    unittest.main()


class FakeScreen:
    def __init__(self, keys):
        self.keys = list(keys)

    def getmaxyx(self):
        return 26, 84

    def timeout(self, delay):
        pass

    def get_wch(self):
        import curses

        if not self.keys:
            raise curses.error("no input")
        return self.keys.pop(0)

    def nodelay(self, flag):
        pass


class KeyTest(unittest.TestCase):
    def keys(self, typed):
        import curses

        screen, got = FakeScreen(typed), []
        while screen.keys:
            got.append(compose._key(screen))
        return got, curses

    def test_arrows_in_both_modes_one_at_a_time(self):
        got, curses = self.keys("\x1b[D\x1b[Dx\x1bOAy")
        self.assertEqual(got, [curses.KEY_LEFT, curses.KEY_LEFT, "x", curses.KEY_UP, "y"])

    def test_lone_esc_and_unknown_sequences(self):
        got, curses = self.keys("\x1b")
        self.assertEqual(got, ["\x1b"])
        got, _ = self.keys("\x1b[15~a")
        self.assertEqual(got, [None, "a"])

    def test_bracketed_paste_is_text(self):
        got, _ = self.keys("\x1b[200~a\x13b\x1b[201~c")
        self.assertEqual(got, [("paste", "a\x13b"), "c"])


class HandleTest(unittest.TestCase):
    def setUp(self):
        from devnull_herdr.textbox import Box

        self.box, self.state = Box("Shipped it"), {"note": "", "top": 0}

    def press(self, key, queued=(), draft=True):
        with mock.patch.object(compose, "_queued", return_value=bool(queued)):
            return compose._handle(FakeScreen([]), self.box, key, draft, self.state)

    def test_ctrl_d_twice_discards(self):
        self.assertIsNone(self.press("\x04"))
        self.assertEqual(self.state["note"], compose.DISCARDING)
        self.assertEqual(self.press("\x04"), (compose.DISCARD, "Shipped it"))

    def test_another_key_between_resets_the_confirmation(self):
        self.press("\x04")
        self.press("!")
        self.assertIsNone(self.press("\x04"))

    def test_ctrl_d_does_nothing_without_a_draft(self):
        self.assertIsNone(self.press("\x04", draft=False))
        self.assertIsNone(self.press("\x04", draft=False))

    def test_ctrl_s_with_more_input_waiting_never_posts(self):
        self.assertIsNone(self.press("\x13", queued=True))
        self.assertIn("Ignored", self.state["note"])
        self.assertEqual(self.press("\x13"), (compose.POST, "Shipped it"))

    def test_a_paste_goes_in_as_text(self):
        self.assertIsNone(self.press(("paste", "!\x13\x04")))
        self.assertEqual(self.box.text, "Shipped it!")



class EmojiTest(HandleTest):
    def type(self, text):
        for char in text:
            self.press(char)

    def test_typing_a_shortcode_opens_the_list(self):
        self.type(" :st")
        start, matches = compose.suggestions(self.box, self.state)
        self.assertEqual(start, 11)
        self.assertEqual(matches[0], ("star", "⭐"))

    def test_enter_or_tab_puts_the_chosen_emoji_in(self):
        self.type(" :st")
        self.press(compose.curses.KEY_DOWN)
        self.press("\n")
        self.assertEqual(self.box.text, "Shipped it 🍲")
        self.type(" :rock")
        self.press("\t")
        self.assertEqual(self.box.text, "Shipped it 🍲 🪨")

    def test_up_wraps_around_to_the_last(self):
        self.type(" :st")
        self.press(compose.curses.KEY_UP)
        _, matches = compose.suggestions(self.box, self.state)
        self.assertEqual(self.state["pick"], len(matches) - 1)

    def test_the_closing_colon_puts_it_in(self):
        self.type(" :rocket:")
        self.assertEqual(self.box.text, "Shipped it 🚀")
        self.type(" :nope:")
        self.assertEqual(self.box.text, "Shipped it 🚀 :nope:")

    def test_esc_hides_the_list_and_closes_nothing(self):
        self.type(" :st")
        self.assertIsNone(self.press("\x1b"))
        self.assertEqual(self.state["note"], "")
        self.assertEqual(compose.suggestions(self.box, self.state), (None, []))
        self.press("\n")
        self.assertEqual(self.box.text, "Shipped it :st\n")
        self.type(":ro")
        self.assertTrue(compose.suggestions(self.box, self.state)[1])

    def test_a_paste_without_markers_keeps_its_new_lines(self):
        self.type(" :ok")
        self.press("\n", queued=True)
        self.press(":", queued=True)
        self.assertEqual(self.box.text, "Shipped it :ok\n:")

    def test_a_small_popup_shows_fewer_matches(self):
        self.state["room"] = 2
        self.type(" :st")
        self.assertEqual(len(compose.suggestions(self.box, self.state)[1]), 2)
        self.state["room"] = 0
        self.assertEqual(compose.suggestions(self.box, self.state), (11, []))

    def test_enter_without_a_list_is_a_new_line(self):
        self.press("\n")
        self.assertEqual(self.box.text, "Shipped it\n")


class RecoveryTest(Isolated):
    def crash_with(self, typed):
        def crash(loop, box, *rest):
            box.insert(typed)
            raise ValueError("boom")

        return mock.patch("curses.wrapper", side_effect=crash)

    def setUp(self):
        super().setUp()
        patcher = mock.patch("sys.stdin")
        patcher.start().isatty.return_value = True
        self.addCleanup(patcher.stop)

    def test_a_crash_keeps_the_text_and_what_it_replied_to(self):
        with self.crash_with(" and then some"), self.assertRaisesRegex(RuntimeError, "next time you open the composer"):
            compose.run("Half a post", reply="0be0130")
        saved = paths.read_json(compose.recovery_path(), {})
        self.assertEqual((saved["text"], saved["reply"]), ("Half a post and then some", "0be0130"))
        self.assertEqual(oct(compose.recovery_path().stat().st_mode & 0o777), "0o600")

    def test_a_crash_while_reviewing_a_draft_keeps_the_edit_in_the_draft(self):
        path = drafts.drafts_dir() / "x.json"
        paths.write_json(path, {"name": "x", "subjects": ["Add a thing"], "updated": 1})
        draft = (path, paths.read_json(path, {}))
        with self.crash_with(" (my words)"), self.assertRaisesRegex(RuntimeError, "review the draft again"):
            compose.run(drafts.render(draft[1]), draft=draft)
        self.assertFalse(compose.recovery_path().exists())
        self.assertEqual(drafts.render(paths.read_json(path, {})), "Just shipped a thing in x 🚀 (my words)")

    def args(self, text=None, reply=None):
        return mock.Mock(reply=reply, image=None, text=text, draft=False, dry_run=False)

    def test_the_next_composer_starts_from_it(self):
        compose.save_unsent("Saved post", "0be0130", [])
        with mock.patch.object(compose, "_wait"), mock.patch.object(compose, "run", return_value=1) as run:
            compose.main(self.args())
        self.assertEqual(run.call_args.args[:2], ("Saved post", "0be0130"))
        self.assertFalse(compose.recovery_path().exists())

    def test_a_composer_with_its_own_text_leaves_it_alone(self):
        compose.save_unsent("Saved post", None, [])
        with mock.patch.object(compose, "_wait"), mock.patch.object(compose, "run", return_value=0):
            compose.main(self.args(text="Something else"))
        self.assertTrue(compose.recovery_path().exists())
