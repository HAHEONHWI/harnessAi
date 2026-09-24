import unittest

import textstats


class TextStatsTests(unittest.TestCase):
    def test_word_count_simple(self):
        self.assertEqual(textstats.word_count("one two three"), 3)

    def test_top_words_simple(self):
        self.assertEqual(textstats.top_words("b a b", 1), [("b", 2)])

    def test_sentences_simple(self):
        self.assertEqual(textstats.sentence_count("One. Two."), 2)


if __name__ == "__main__":
    unittest.main()
