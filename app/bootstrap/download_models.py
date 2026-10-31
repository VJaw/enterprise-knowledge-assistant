"""Bake the embedding and re-ranker weights into the image (§B4.5): no downloads at Space startup."""
from sentence_transformers import CrossEncoder, SentenceTransformer

from app.config import get_settings


def main() -> None:
    s = get_settings()
    SentenceTransformer(s.embed_model, device="cpu")
    CrossEncoder(s.rerank_model, device="cpu")  # used from Phase 5
    print(f"cached {s.embed_model} and {s.rerank_model}")


if __name__ == "__main__":
    main()
