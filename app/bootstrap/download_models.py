"""Bake the model weights into the image (§B4.5): no downloads at Space startup.

Embeddings and re-ranker (Phase 3/5), and the guardrail models (Phase 8): LLM Guard's prompt-injection,
toxicity and zero-shot topic classifiers, at the revisions LLM Guard pins. spaCy's `en_core_web_lg` (Presidio)
is a pip package, installed by the Dockerfile.
"""
from huggingface_hub import snapshot_download
from sentence_transformers import CrossEncoder, SentenceTransformer

from app.config import get_settings

# (repo, revision) as pinned by llm-guard 0.3.16 (its prompt_injection, toxicity and ban_topics scanners)
GUARD_MODELS = [
    ("protectai/deberta-v3-base-prompt-injection-v2", "89b085cd330414d3e7d9dd787870f315957e1e9f"),
    ("unitary/unbiased-toxic-roberta", "36295dd80b422dc49f40052021430dae76241adc"),
    ("MoritzLaurer/roberta-base-zeroshot-v2.0-c", "d825e740e0c59881cf0b0b1481ccf726b6d65341"),
]


def main() -> None:
    s = get_settings()
    SentenceTransformer(s.embed_model, device="cpu")
    CrossEncoder(s.rerank_model, device="cpu")  # used from Phase 5
    for repo, revision in GUARD_MODELS:  # torch weights only (no ONNX / TF / Flax copies)
        snapshot_download(repo, revision=revision, ignore_patterns=["*.onnx", "onnx/*", "*.h5", "*.msgpack",
                                                                    "*.ot", "*.ckpt", "*.tflite"])
    print(f"cached {s.embed_model}, {s.rerank_model} and {len(GUARD_MODELS)} guardrail models")


if __name__ == "__main__":
    main()
