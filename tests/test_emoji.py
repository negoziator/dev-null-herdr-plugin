import unittest

from devnull_herdr import emoji


class EmojiTest(unittest.TestCase):
    def test_github_shortcodes(self):
        self.assertEqual(emoji.shortcodes()["star"], "⭐")
        self.assertEqual(emoji.shortcodes()["+1"], "👍")
        self.assertGreater(len(emoji.shortcodes()), 1800)

    def test_names_that_start_with_it_come_first_shortest_first(self):
        self.assertEqual([name for name, _ in emoji.search("st")][:4], ["star", "stew", "star2", "stars"])
        self.assertEqual([name for name, _ in emoji.search("rocket")], ["rocket"])
        self.assertIn("thumbsup", [name for name, _ in emoji.search("bsup")])
        self.assertEqual(emoji.search("s"), [])

    def test_what_is_being_typed(self):
        self.assertEqual(emoji.typing("Shipped it :roc", 15), (11, "roc"))
        self.assertEqual(emoji.typing(":st", 3), (0, "st"))
        self.assertEqual(emoji.typing("line\n:st", 8), (5, "st"))
        self.assertEqual(emoji.typing("(:st", 4), (1, "st"))
        self.assertEqual(emoji.typing(":Rocket", 7), (0, "rocket"))
        long = "x:" + "a" * 39
        self.assertIsNone(emoji.typing(long, len(long)))

    def test_links_and_times_are_left_alone(self):
        for text in ("see https://x", "at 10:30", "a:b", "done :star: ", "nothing"):
            self.assertIsNone(emoji.typing(text, len(text)), text)
