"""How an item's text is split into the pieces that get embedded.

The version is stored with the vectors: changing how text is chunked means every
item is chunked and embedded again, the same as changing the model.
"""

CHUNKING_VERSION = "whole-v1"


def chunk_texts(title: str, body: str) -> list[str]:
    return [f"{title}\n{body}"]
