from devnull_herdr import api, watch
from tests.helpers import Isolated


class Client:
    def __init__(self, counts, signed_in=True):
        self.counts, self.signed_in = list(counts), signed_in

    def account(self):
        return {"username": "me"} if self.signed_in else None

    def me(self):
        count = self.counts.pop(0)
        if isinstance(count, Exception):
            raise count
        return {"unread_notifications_count": count}


class WatchTest(Isolated):
    def run_loop(self, client, turns):
        notes, sleeps = [], []
        left = [turns]

        def running():
            left[0] -= 1
            return left[0] >= 0

        watch.loop(sleep=sleeps.append, running=running, notify=lambda *a, **k: notes.append(a[1]), client=client)
        return notes, sleeps

    def test_notifies_when_the_count_grows(self):
        notes, sleeps = self.run_loop(Client([2, 2, 5, 1, 2]), 5)
        self.assertEqual(notes, ["3 new notifications. Open the feed (4) to see them.",
                                 "1 new notification. Open the feed (4) to see them."])
        self.assertEqual(sleeps, [60] * 5)

    def test_not_signed_in_waits_longer(self):
        _, sleeps = self.run_loop(Client([], signed_in=False), 2)
        self.assertEqual(sleeps, [300, 300])

    def test_a_revoked_device_is_said_once(self):
        revoked = api.ApiError(401, "device_not_approved", "This device was signed out")
        notes, sleeps = self.run_loop(Client([revoked, revoked]), 2)
        self.assertEqual(notes, ["This device was signed out"])
        self.assertEqual(sleeps, [300, 300])

    def test_rate_limit_is_respected(self):
        _, sleeps = self.run_loop(Client([api.ApiError(429, "rate_limited", "slow", retry_after=120)]), 1)
        self.assertEqual(sleeps, [120])

    def test_odd_answers_do_not_end_the_watcher(self):
        class Odd(Client):
            def me(self):
                self.counts.pop(0)
                return {"unread_notifications_count": "many"}

        _, sleeps = self.run_loop(Odd([0, 0]), 2)
        self.assertEqual(sleeps, [60, 60])

    def test_no_herdr_no_watcher(self):
        import os

        os.environ.pop("HERDR_SOCKET_PATH", None)
        self.assertFalse(watch.herdr_running())
        os.environ["HERDR_SOCKET_PATH"] = str(self.tmp / "gone.sock")
        (self.tmp / "gone.sock").write_text("")  # left behind by a killed herdr
        self.assertFalse(watch.herdr_running())

    def test_one_watcher_at_a_time(self):
        held = watch._lock()
        self.assertIsNotNone(held)
        self.assertIsNone(watch._lock())
        notes, sleeps = self.run_loop(Client([1]), 1)
        self.assertEqual(sleeps, [])
