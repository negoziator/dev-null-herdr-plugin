import os
import unittest

from devnull_herdr import cli, script
from tests.helpers import Isolated


class UpdateCheckTest(Isolated):
    def check(self, remote, *keys):
        keys, said, shown = iter(keys), [], []
        script.check_for_update(
            ask=lambda prompt="": next(keys), fetch=lambda: remote, show=shown.append, out=said.append
        )
        return said, shown

    def test_same_version_asks_nothing(self):
        said, _ = self.check(script.VENDORED.read_bytes())
        self.assertEqual(said, [])
        self.assertEqual(script.current(), script.VENDORED)

    def test_offline_keeps_the_current_one(self):
        said, _ = self.check(None)
        self.assertIn("Could not check", said[0])

    def test_see_diff_then_update(self):
        new = script.VENDORED.read_bytes() + b"# new\n"
        said, shown = self.check(new, "1", "2")
        self.assertIn("New version of /dev/null available. 1) See diff 2) Update 3) Discard", said)
        self.assertIn("+# new", shown[0])
        self.assertEqual(script.current(), script.installed())
        self.assertEqual(script.current().read_bytes(), new)
        self.assertEqual(os.stat(script.installed()).st_mode & 0o777, 0o600)

    def test_discard_keeps_the_vendored_copy(self):
        said, _ = self.check(b"something else", "9", "3")
        self.assertIn("Pick 1, 2 or 3.", said)
        self.assertEqual(script.current(), script.VENDORED)

    def test_vendored_copy_is_the_reviewed_one(self):
        self.assertEqual(
            script.sha256(script.VENDORED.read_bytes()),
            "e016dd6ac2227060deec72ea3fca1fc98e88a82ac0b21719b342b72e1aedbbb0",
        )


class InstalledTest(Isolated):
    def test_a_newer_vendored_copy_wins(self):
        script.install(b"#!/bin/sh\n# accepted update\n")
        self.assertEqual(script.current(), script.installed())
        # The plugin is upgraded: the update was accepted over another vendored copy.
        script._installed_for().write_text("0" * 64 + "\n")
        self.assertEqual(script.current(), script.VENDORED)
        self.assertFalse(script.installed().exists())

    def test_an_update_without_its_record_is_dropped(self):
        script.installed().write_text("#!/bin/sh\n")
        self.assertEqual(script.current(), script.VENDORED)

    def test_reset(self):
        script.install(b"#!/bin/sh\n")
        script.reset()
        self.assertEqual(script.current(), script.VENDORED)

    def test_only_scripts_are_offered(self):
        self.assertTrue(script.looks_like_script(b"#!/usr/bin/env bash\necho\n"))
        self.assertFalse(script.looks_like_script(b"<html>Sign in to the Wi-Fi</html>"))
        self.assertFalse(script.looks_like_script(b"#!/bin/sh\n\0binary"))
        self.assertFalse(script.looks_like_script(b"#!" + b"x" * script.MAX_SIZE))

    def test_the_diff_shows_escape_sequences(self):
        shown = script.diff(b"#!/bin/sh\n", b"#!/bin/sh\necho \x1b[8mhidden\n")
        self.assertIn("\\x1b[8mhidden", shown)
        self.assertNotIn("\x1b", shown)


class CommitTest(Isolated):
    def fake_script(self, body):
        script.install(body.encode())

    def test_command(self):
        self.assertEqual(
            script.command("--reply is text", "0be0130", ["a.png"])[2:],
            ["--reply", "0be0130", "--image", "a.png", "--", "--reply is text"],
        )

    def test_reads_the_commit_link(self):
        self.fake_script('printf "Committed: https://dev-null.sh/@me/commits/01a1 (pending review)\\n"\n')
        ok, url, output = script.commit("hello")
        self.assertTrue(ok)
        self.assertEqual(url, "https://dev-null.sh/@me/commits/01a1")

    def test_a_refusal(self):
        self.fake_script('echo "devnull: Slow down" >&2; exit 1\n')
        self.assertEqual(script.commit("hello"), (False, None, ""))

    def test_the_message_is_one_argument(self):
        self.fake_script('printf "%s|" "$@"\n')
        _, _, output = script.commit("it's $(not) run; `ever`")
        self.assertEqual(output, "--|it's $(not) run; `ever`|")


class CliTest(Isolated):
    def test_post_dry_run(self):
        self.assertEqual(cli.main(["post", "--dry-run", "Hello world"]), 0)

    def test_post_refuses_too_short(self):
        self.assertEqual(cli.main(["post", "hi"]), 1)

    def test_drafts_toggle(self):
        cli.main(["drafts", "off"])
        from devnull_herdr import paths

        self.assertFalse(paths.config()["drafts"])
        cli.main(["drafts", "toggle"])
        self.assertTrue(paths.config()["drafts"])


if __name__ == "__main__":
    unittest.main()
