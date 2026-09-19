"""How an item's text is split into the pieces that get embedded.

The model reads at most 256 tokens and silently ignores the rest, and a long
commit message or event description runs well past that -- code-heavy text is
about two tokens a word. So a long body is cut into overlapping windows counted in
the model's own tokens, each with the title in front, since the title is what
says what a piece is about. Anything that fits stays whole, exactly as before.

The version is stored with the vectors: changing how text is chunked means every
item is chunked and embedded again, the same as changing the model.
"""

from src.embeddings.provider import MAX_TOKENS, get_embedder

CHUNKING_VERSION = "tokens-v1"

# room for the [CLS] and [SEP] the model adds, and the newline after the title
RESERVED_TOKENS = 3
# the most body a window carries even when the title is short, and how much of
# the previous window each one repeats, so a sentence cut at the edge is still
# whole in one of them
WINDOW_TOKENS = 200
OVERLAP_TOKENS = 40


def chunk_texts(title: str, body: str, tokenizer=None) -> list[str]:
    whole = f"{title}\n{body}"
    tokenizer = tokenizer or get_embedder().counting_tokenizer

    title_tokens = len(tokenizer.encode(title, add_special_tokens=False).ids)
    body_encoding = tokenizer.encode(body, add_special_tokens=False)
    budget = MAX_TOKENS - RESERVED_TOKENS - title_tokens

    if len(body_encoding.ids) <= budget:
        return [whole]

    # a title too long to leave room is rare; give the body a sensible minimum
    # and let the model truncate the title instead
    window = max(min(WINDOW_TOKENS, budget), 64)
    step = window - OVERLAP_TOKENS
    offsets = body_encoding.offsets

    chunks = []
    for start in range(0, len(offsets), step):
        end = min(start + window, len(offsets))
        piece = body[offsets[start][0]:offsets[end - 1][1]]
        chunks.append(f"{title}\n{piece}")
        if end == len(offsets):
            break
    return chunks
