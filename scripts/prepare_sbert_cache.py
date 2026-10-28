"""Prime a pinned, hash-checked model for the network-disabled regression suite."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
WEIGHT_SHA256 = "eaa086f0ffee582aeb45b36e34cdd1fe2d6de2bef61f8a559a1bbc9bd955917b"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    cache = args.cache.resolve()
    os.environ["HF_HOME"] = str(cache)
    hub = cache / "hub"
    if args.offline:
        snapshot = hub / ("models--" + MODEL.replace("/", "--")) / "snapshots" / REVISION
    else:
        from huggingface_hub import snapshot_download
        snapshot = Path(snapshot_download(
            repo_id=MODEL, revision=REVISION, cache_dir=str(hub),
            allow_patterns=["*.json", "*.txt", "*.safetensors"],
        ))
    digest = hashlib.sha256()
    with (snapshot / "model.safetensors").open("rb") as weights:
        for block in iter(lambda: weights.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != WEIGHT_SHA256:
        raise RuntimeError("SBert weight hash mismatch")
    # Production loads by model name. Pin its offline main alias to the same
    # snapshot; never resolve a different model revision during regression.
    refs = snapshot.parent.parent / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "main").write_text(REVISION, encoding="utf-8")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(MODEL, local_files_only=True)
    vectors = model.encode(["固定モデルの実推論確認"], show_progress_bar=False)
    if vectors.shape != (1, 384):
        raise RuntimeError("Unexpected SBert embedding shape")
    print(f"Verified {MODEL}@{REVISION}; real embedding shape={vectors.shape}")


if __name__ == "__main__":
    main()
