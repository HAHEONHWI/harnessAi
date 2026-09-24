"""Text statistics used by the reading-report feature."""
import re

WORD_RE = re.compile(r"[A-Za-z]+")


def words(text):
    return WORD_RE.findall(text)


def word_count(text):
    return len(text.split(" "))


def top_words(text, n=3):
    counts = {}
    for w in words(text):
        counts[w] = counts.get(w, 0) + 1
    return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:n]


def sentence_count(text):
    return text.count(".")


def reading_time_seconds(text, wpm=200):
    return word_count(text) / wpm * 60
