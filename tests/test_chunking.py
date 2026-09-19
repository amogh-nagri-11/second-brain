import re
import unittest
from types import SimpleNamespace

from src.embeddings.chunking import OVERLAP_TOKENS, chunk_texts
from src.embeddings.provider import MAX_TOKENS


class WordTokenizer:
    """One token per word, with character offsets like the real tokenizer's."""

    def encode(self, text, add_special_tokens=True):
        spans = [m.span() for m in re.finditer(r"\S+", text)]
        return SimpleNamespace(ids=list(range(len(spans))), offsets=spans)


def words(n, start=0):
    return " ".join(f"w{i}" for i in range(start, start + n))


class ChunkingTests(unittest.TestCase):
    tok = WordTokenizer()

    def test_short_text_stays_whole(self):
        self.assertEqual(chunk_texts("Title", "a short body", self.tok), ["Title\na short body"])

    def test_long_text_is_split_within_the_limit(self):
        chunks = chunk_texts("Title here", words(900), self.tok)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertTrue(chunk.startswith("Title here\n"))
            self.assertLessEqual(len(chunk.split()), MAX_TOKENS - 3)

    def test_every_word_is_in_some_chunk(self):
        body = words(900)
        seen = set(" ".join(chunk_texts("T", body, self.tok)).split())
        self.assertTrue(set(body.split()) <= seen)

    def test_windows_overlap(self):
        first, second = chunk_texts("T", words(500), self.tok)[:2]
        shared = set(first.split()[1:]) & set(second.split()[1:])
        self.assertEqual(len(shared), OVERLAP_TOKENS)


if __name__ == "__main__":
    unittest.main()
