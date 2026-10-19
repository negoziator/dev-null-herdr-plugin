import datetime
import unittest
from unittest import mock

from devnull_herdr import compose, feed
from tests.helpers import Isolated

UUID = "01a155b8-e765-719c-b506-12744ccd0236"


def commit(number, **fields):
    return {"id": f"{UUID[:-2]}{number:02d}", "short_hash": f"abc{number:04d}", "body": f"Post {number}",
            "created_at": "2026-10-19T19:52:23Z", "stars_count": 1, "replies_count": 0, "starred": False,
            "html_url": f"https://dev-null.sh/@x/commits/{number}", "author": {"username": f"user{number}", "name": "Some One"},
            **fields}


class FakeClient:
    def __init__(self):
        self.calls = []

    def log(self, name, cursor=None, since=None):
        self.calls.append(("log", name, cursor, since))
        if since:
            return {"data": [], "new_commits_count": 3}
        start = 20 if cursor else 0
        return {"data": [commit(start + i) for i in range(20)], "meta": {"next_cursor": None if cursor else "page2"}}

    def notifications(self, cursor=None):
        self.calls.append(("notifications", cursor))
        return {"data": [{"type": "replied", "actor": {"username": "nuno"}, "actors_count": 2,
                          "created_at": "2026-10-19T19:52:23Z", "commit": commit(99)}], "meta": {}}

    def star(self, commit_id, on=True):
        self.calls.append(("star", commit_id, on))
        return {"data": {"starred": on, "stars_count": 2 if on else 1}}


class FeedTest(Isolated):
    def setUp(self):
        super().setUp()
        self.client = FakeClient()
        self.feed = feed.Feed(self.client)
        self.feed.load()

    def test_loads_following_first(self):
        self.assertEqual(self.client.calls[0], ("log", "following", None, None))
        self.assertEqual(len(self.feed.items["following"]), 20)

    def test_moving_near_the_end_loads_more(self):
        self.feed.move(17)
        self.assertEqual(len(self.feed.items["following"]), 40)
        self.feed.move(100)
        self.assertEqual(self.feed.selected["following"], 39)

    def test_tabs_load_once(self):
        self.feed.switch(1)
        self.feed.switch(0)
        self.feed.switch(1)
        self.assertEqual([call[1] for call in self.client.calls if call[0] == "log"], ["following", "latest"])

    def test_notifications_load_only_when_opened(self):
        self.assertNotIn("notifications", [call[0] for call in self.client.calls])
        self.feed.switch(3)
        self.assertEqual(self.client.calls[-1][0], "notifications")
        self.assertEqual(self.feed.target()["id"], commit(99)["id"])

    def test_counts_new_posts(self):
        self.feed.check_new()
        self.assertEqual(self.client.calls[-1][3], commit(0)["id"])
        self.assertEqual(self.feed.new, 3)
        self.feed.switch(2)  # trending is ranked: nothing to count
        calls = len(self.client.calls)
        self.feed.check_new()
        self.assertEqual(len(self.client.calls), calls)

    def test_star_toggles(self):
        self.feed.toggle_star()
        self.assertTrue(self.feed.current()["starred"])
        self.feed.toggle_star()
        self.assertEqual(self.client.calls[-1][2], False)


class RenderTest(unittest.TestCase):
    def test_a_post(self):
        lines = feed.post_lines(commit(1, body="hello\x1b[2J world", images=[{}], parent_id="x"), 40)
        text = "\n".join(line for line, _ in lines)
        self.assertIn("Some One @user1", text)
        self.assertIn("abc0001", lines[0][0])
        self.assertIn("hello[2J world", text)
        self.assertIn("↳ a reply", text)
        self.assertIn("[1 image]", text)
        self.assertNotIn("\x1b", text)

    def test_wide_names_keep_the_hash_in_place(self):
        head = feed.post_lines(commit(1, author={"username": "SuzumiyaAoba", "name": "涼宮 青葉"}), 40)[0][0]
        self.assertTrue(head.endswith("abc0001"))
        self.assertEqual(feed.columns(head), 40)

    def test_wraps_at_spaces(self):
        self.assertEqual(feed.wrap("thanks /dev/null for the fun", 12), ["thanks", "/dev/null", "for the fun"])
        self.assertEqual(feed.wrap("averyveryverylongword", 8), ["averyver", "yverylon", "gword"])
        self.assertEqual(feed.wrap("涼宮青葉涼宮", 4), ["涼宮", "青葉", "涼宮"])

    def test_a_long_post_is_cut(self):
        lines = feed.post_lines(commit(1, body="word " * 400), 40)
        self.assertIn("… o opens all of it", [line for line, _ in lines])

    def test_a_notification(self):
        item = {"type": "starred", "actor": {"username": "nuno"}, "actors_count": 3, "created_at": "2026-10-19T19:52:23Z"}
        self.assertIn("@nuno and 2 others starred", feed.notification_lines(item, 60)[0][0])

    def test_age(self):
        now = datetime.datetime(2026, 10, 19, 20, 52, 23, tzinfo=datetime.timezone.utc).timestamp()
        self.assertEqual(feed.age("2026-10-19T19:52:23Z", now), "1h")
        self.assertEqual(feed.age("nonsense"), "")


class OpenTest(unittest.TestCase):
    def test_only_dev_null_links_open(self):
        with mock.patch("subprocess.Popen") as popen, mock.patch("shutil.which", return_value="/usr/bin/xdg-open"):
            for url in ("file:///etc/passwd", "vscode://x", "devnull://auth/callback?code=x", "https://dev-null.sh.evil.example/x",
                        "--help", None):
                feed.open_in_browser(url)
            popen.assert_not_called()
            feed.open_in_browser("https://dev-null.sh/@me/commits/1")
            popen.assert_called_once()

    def test_odd_numbers_do_not_crash(self):
        lines = feed.post_lines(commit(1, stars_count="lots", replies_count=None), 40)
        self.assertIn("☆ 0   ↩ 0", [line for line, _ in lines])
        item = {"type": "starred", "actor": "nope", "actors_count": "x"}
        self.assertIn("@someone starred", feed.notification_lines(item, 60)[0][0])


class ReplyTest(Isolated):
    def test_reply_reaches_the_composer(self):
        with mock.patch.object(feed.herdr, "open_pane", return_value=(True, "")) as opened:
            self.assertIsNone(feed.start_reply(commit(5, body="What a post")))
        opened.assert_called_once_with("reply")
        args = mock.Mock(reply=None, image=None, text=None, draft=False, dry_run=False, pending_reply=True)
        with mock.patch.object(compose, "_wait"), mock.patch.object(compose, "run", return_value=0) as run:
            compose.main(args)
        self.assertEqual(run.call_args.args[1], commit(5)["id"])
        self.assertEqual(run.call_args.kwargs["reply_label"], "@user5: What a post")
        self.assertFalse(feed.reply_path().exists())


