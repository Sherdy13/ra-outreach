"""
Finds similar events using Voyage AI embeddings + cosine similarity.

How it works:
1. Each event's text (title + genre + venue + description) is embedded into
   a vector of 1024 floats using voyage-3. A vector captures the *meaning*
   of the text — similar-sounding events end up with similar vectors.

2. Similarity between two events = cosine similarity of their vectors.
   Score of 1.0 = identical, 0.0 = completely unrelated.

3. Embeddings are stored in the DB after first use (lazy caching).
   Re-embedding only happens if an event has no stored vector.
   This means at scale, you pay the API cost once per event ever.

Scalability note:
   For hundreds of events, computing all pairwise similarities in Python
   is fine. For tens of thousands, you'd move to a vector database
   (e.g. pgvector, Pinecone, Chroma) which does approximate nearest-neighbour
   search much faster. The embedding + cosine logic here stays the same.
"""

import os
import numpy as np
import voyageai

from src import storage

_client = None


def get_client() -> voyageai.Client:
    global _client
    if _client is None:
        api_key = os.environ.get("VOYAGE_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "VOYAGE_API_KEY is not set.\n"
                "Add it to your .env file: VOYAGE_API_KEY=pa-..."
            )
        _client = voyageai.Client(api_key=api_key)
    return _client


def _event_to_text(event: dict) -> str:
    """
    Combine the most meaningful fields into a single string for embedding.
    Title + genre + venue gives the event's identity.
    Description gives the vibe. Together they capture what makes it distinctive.
    """
    parts = [
        event.get("title", ""),
        event.get("genre", ""),
        event.get("venue", ""),
        event.get("description", "") or "",
    ]
    return " | ".join(p for p in parts if p.strip())


def get_or_create_embedding(event: dict) -> list[float]:
    """
    Return the embedding for an event, creating and caching it if needed.
    This is the lazy caching pattern — embed once, reuse forever.
    """
    event_id = event["id"]

    # Cache hit — return stored vector
    cached = storage.load_embedding(event_id)
    if cached:
        return cached

    # Cache miss — call Voyage API and store result
    text = _event_to_text(event)
    result = get_client().embed([text], model="voyage-3", input_type="document")
    vector = result.embeddings[0]
    storage.save_embedding(event_id, vector)
    return vector


def cosine_similarity(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a), np.array(b)
    return float(np.dot(va, vb) / (np.linalg.norm(va) * np.linalg.norm(vb)))


def _batch_embed(events: list[dict]) -> dict[int, list[float]]:
    """
    Embed all events that don't have a cached vector in a single API call.
    Returns a mapping of event_id -> vector for all events (cached + fresh).

    Batching is critical for staying within rate limits — one API call for
    N events instead of N calls. At scale this also reduces latency significantly.
    """
    # Split into cached and uncached
    result: dict[int, list[float]] = {}
    to_embed: list[dict] = []

    for event in events:
        cached = storage.load_embedding(event["id"])
        if cached:
            result[event["id"]] = cached
        else:
            to_embed.append(event)

    # Single API call for all uncached events
    if to_embed:
        texts = [_event_to_text(e) for e in to_embed]
        print(f"  Embedding {len(to_embed)} events via Voyage AI...")
        response = get_client().embed(texts, model="voyage-3", input_type="document")
        for event, vector in zip(to_embed, response.embeddings):
            storage.save_embedding(event["id"], vector)
            result[event["id"]] = vector

    return result


def find_similar(target_event: dict, all_events: list[dict], top_n: int = 5) -> list[tuple[dict, float]]:
    """
    Returns the top_n most similar events to target_event,
    as a list of (event_dict, similarity_score) tuples, highest first.

    All embeddings are fetched/created in a single batched API call.
    On subsequent runs, everything is served from the DB cache.
    """
    all_events_incl_target = [target_event] + [e for e in all_events if e["id"] != target_event["id"]]
    vectors = _batch_embed(all_events_incl_target)

    target_vec = vectors[target_event["id"]]
    scores = [
        (event, cosine_similarity(target_vec, vectors[event["id"]]))
        for event in all_events
        if event["id"] != target_event["id"] and event["id"] in vectors
    ]

    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:top_n]
