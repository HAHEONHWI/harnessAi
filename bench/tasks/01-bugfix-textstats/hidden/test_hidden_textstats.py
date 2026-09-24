import unittest

import textstats as ts


class Words(unittest.TestCase):
    def test_unicode_words(self):
        self.assertEqual(ts.words("café 안녕하세요 naïve"), ["café", "안녕하세요", "naïve"])

    def test_apostrophe_and_hyphen_join(self):
        self.assertEqual(ts.words("don't state-of-the-art 3-2"), ["don't", "state-of-the-art", "3-2"])

    def test_dangling_joiners_are_not_words(self):
        self.assertEqual(ts.words("'quoted' -dash- a--b"), ["quoted", "dash", "a", "b"])

    def test_underscore_splits(self):
        self.assertEqual(ts.words("snake_case"), ["snake", "case"])


class WordCount(unittest.TestCase):
    def test_whitespace_runs(self):
        self.assertEqual(ts.word_count("one  two\tthree\n\nfour "), 4)

    def test_empty(self):
        self.assertEqual(ts.word_count(""), 0)
        self.assertEqual(ts.word_count("   \n\t"), 0)

    def test_matches_words(self):
        text = "Hello, world! It's a state-of-the-art 테스트."
        self.assertEqual(ts.word_count(text), len(ts.words(text)))


class TopWords(unittest.TestCase):
    def test_case_insensitive_lowercase(self):
        self.assertEqual(ts.top_words("The the THE cat", 1), [("the", 3)])

    def test_ties_alphabetical(self):
        self.assertEqual(ts.top_words("pear apple pear apple fig", 3), [("apple", 2), ("pear", 2), ("fig", 1)])

    def test_non_positive_n(self):
        self.assertEqual(ts.top_words("a b c", 0), [])
        self.assertEqual(ts.top_words("a b c", -2), [])

    def test_returns_tuples(self):
        self.assertEqual(ts.top_words("x"), [("x", 1)])


class Sentences(unittest.TestCase):
    def test_mixed_punctuation_runs(self):
        self.assertEqual(ts.sentence_count("Wait... what?! Really. Yes!"), 4)

    def test_trailing_text(self):
        self.assertEqual(ts.sentence_count("First one. second without end"), 2)

    def test_empty(self):
        self.assertEqual(ts.sentence_count(""), 0)
        self.assertEqual(ts.sentence_count("  ...  "), 0)


class ReadingTime(unittest.TestCase):
    def test_rounds_up_to_int(self):
        text = " ".join(["w"] * 201)
        value = ts.reading_time_seconds(text, wpm=200)
        self.assertIsInstance(value, int)
        self.assertEqual(value, 61)

    def test_zero_and_invalid(self):
        self.assertEqual(ts.reading_time_seconds(""), 0)
        with self.assertRaises(ValueError):
            ts.reading_time_seconds("a b", wpm=0)


if __name__ == "__main__":
    unittest.main()
