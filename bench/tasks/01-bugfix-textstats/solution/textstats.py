"""Text statistics used by the reading-report feature."""
import math
import re

WORD_RE = re.compile(r"[^\W_]+(?:['-][^\W_]+)*")
SENTENCE_END_RE = re.compile(r"[.!?]+")


def words(text):
    return WORD_RE.findall(text)


def word_count(text):
    return len(words(text))


def top_words(text, n=3):
    if n <= 0:
        return []
    counts = {}
    for w in words(text):
        w = w.lower()
        counts[w] = counts.get(w, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def sentence_count(text):
    return sum(1 for part in SENTENCE_END_RE.split(text) if part.strip())


def reading_time_seconds(text, wpm=200):
    if wpm <= 0:
        raise ValueError("wpm must be positive")
    return math.ceil(word_count(text) / wpm * 60)
