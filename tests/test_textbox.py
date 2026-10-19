import unittest

from devnull_herdr.textbox import Box, typable, width


class EditTest(unittest.TestCase):
    def test_typing_and_backspace(self):
        box = Box()
        box.insert("helo")
        box.left()
        box.insert("l")
        self.assertEqual((box.text, box.cursor), ("hello", 4))
        box.backspace()
        box.backspace()
        self.assertEqual((box.text, box.cursor), ("heo", 2))
        box.delete()
        self.assertEqual(box.text, "he")

    def test_starts_at_the_end_of_a_draft(self):
        self.assertEqual(Box("Just shipped").cursor, 12)

    def test_control_characters_cannot_be_typed_or_pasted(self):
        box = Box()
        box.insert("a\x1b[8mb‮c\x07\r\nd 👨‍💻")
        self.assertEqual(box.text, "a[8mbc\nd 👨‍💻")

    def test_typable(self):
        self.assertTrue(typable("é"))
        self.assertTrue(typable("\n"))
        self.assertFalse(typable("\t"))
        self.assertFalse(typable("​"))


class LayoutTest(unittest.TestCase):
    def test_wraps_at_the_width(self):
        box = Box("abcdefgh")
        self.assertEqual(box.rows(3), [(0, 3), (3, 6), (6, 8)])

    def test_newlines_start_rows(self):
        self.assertEqual(Box("ab\n\ncd").rows(10), [(0, 2), (3, 3), (4, 6)])

    def test_wide_characters_take_two_columns(self):
        self.assertEqual(width("🚀"), 2)
        self.assertEqual(width("a"), 1)
        self.assertEqual(width("́"), 0)
        self.assertEqual(Box("🚀🚀a").rows(4), [(0, 2), (2, 3)])

    def test_cursor_position(self):
        box = Box("abcdef")
        self.assertEqual(box.position(3), (1, 3))
        box.cursor = 3
        self.assertEqual(box.position(3), (1, 0))
        box = Box("ab\n")
        self.assertEqual(box.position(10), (1, 0))
        box.cursor = 2
        self.assertEqual(box.position(10), (0, 2))

    def test_up_and_down_keep_the_column(self):
        box = Box("hello\nhi\nworld")
        box.cursor = 4  # hell|o
        box.down(20)
        self.assertEqual(box.cursor, 8)  # hi|, as far as that row goes
        box.down(20)
        self.assertEqual(box.cursor, 11)  # wo|rld
        box.up(20)
        box.up(20)
        self.assertEqual(box.cursor, 2)
        box.up(20)
        self.assertEqual(box.cursor, 0)
        box.down(20)
        box.down(20)
        box.down(20)
        self.assertEqual(box.cursor, len(box.text))

    def test_end_and_up_stay_on_a_wrapped_row(self):
        box = Box("abcdefghijklmn")
        box.cursor = 3
        box.end(10)
        self.assertEqual(box.position(10), (0, 9))
        box = Box("a" * 20)
        box.up(10)
        self.assertEqual(box.position(10)[0], 0)

    def test_end_on_a_wrapped_row_never_splits_an_emoji(self):
        box = Box("abcdefgh👨\u200d💻xyz")
        box.cursor = 0
        box.end(12)
        self.assertEqual(box.text[box.cursor], "👨")

    def test_starting_text_is_filtered(self):
        self.assertEqual(Box("a\tb\x1b[0m").text, "a    b[0m")

    def test_emoji_survive(self):
        flag = "\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F"
        for emoji in (flag, "👨\u200d💻", "❤\ufe0f"):
            self.assertEqual(Box(emoji).text, emoji)

    def test_home_and_end(self):
        box = Box("one\ntwo")
        box.cursor = 5
        box.home(20)
        self.assertEqual(box.cursor, 4)
        box.end(20)
        self.assertEqual(box.cursor, 7)


if __name__ == "__main__":
    unittest.main()
