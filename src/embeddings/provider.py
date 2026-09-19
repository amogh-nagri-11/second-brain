"""Sentence embeddings, from all-MiniLM-L6-v2 run through ONNX Runtime.

The same model sentence-transformers runs, reproduced step for step -- tokenise
(truncated at 256 tokens), run the transformer, mean-pool over the real tokens,
normalise -- without torch, which was ~650 MB of install for this one model.
ONNX Runtime and the tokenizer are already here for faster-whisper.
"""

import threading

import numpy as np

MODEL_REPO = "sentence-transformers/all-MiniLM-L6-v2"
# pinned, so an upload to the model repo can't quietly change every vector
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
# what's stored alongside the vectors; a change means everything is re-embedded
MODEL_ID = f"{MODEL_REPO}@{MODEL_REVISION[:12]}/onnx"
# the model's own limit, which sentence-transformers applies too
MAX_TOKENS = 256
DIMENSIONS = 384
BATCH_SIZE = 64


def _fetch(filename: str) -> str:
    from huggingface_hub import hf_hub_download

    # the local copy first: no network round trip on every start, and it keeps
    # working offline once downloaded
    try:
        return hf_hub_download(MODEL_REPO, filename, revision=MODEL_REVISION, local_files_only=True)
    except Exception:
        return hf_hub_download(MODEL_REPO, filename, revision=MODEL_REVISION)


class EmbeddingProvider:
    def __init__(self):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.tokenizer = Tokenizer.from_file(_fetch("tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=MAX_TOKENS)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")

        options = ort.SessionOptions()
        options.log_severity_level = 3
        self.session = ort.InferenceSession(
            _fetch("onnx/model.onnx"), options, providers=["CPUExecutionProvider"]
        )

    def _encode(self, texts: list[str]) -> np.ndarray:
        encodings = self.tokenizer.encode_batch(texts)
        ids = np.array([e.ids for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        types = np.array([e.type_ids for e in encodings], dtype=np.int64)

        hidden = self.session.run(
            None, {"input_ids": ids, "attention_mask": mask, "token_type_ids": types}
        )[0]

        # mean over the real tokens only; padding would drag every short text
        # towards the same point
        weights = mask[:, :, None].astype(np.float32)
        pooled = (hidden * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        return (pooled / np.clip(norms, 1e-12, None)).astype(np.float32)

    def embed(self, text: str) -> list[float]:
        return self._encode([text])[0].tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        vectors = [self._encode(texts[i:i + BATCH_SIZE]) for i in range(0, len(texts), BATCH_SIZE)]
        if not vectors:
            return []
        return np.concatenate(vectors).tolist()


_embedder = None
_embedder_lock = threading.Lock()


def get_embedder() -> EmbeddingProvider:
    """Shared instance -- loading the model takes a moment, so both the sync and the
    query path reuse the same one."""
    global _embedder
    with _embedder_lock:
        if _embedder is None:
            _embedder = EmbeddingProvider()
        return _embedder
