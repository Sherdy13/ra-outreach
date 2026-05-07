"""
Batch email drafting using the Anthropic Message Batches API.

Instead of drafting one email at a time (blocking on each API call), this
module submits all requests in a single async batch. Benefits:
  - Up to 50% cost reduction on input tokens
  - No rate limit pressure — one API call regardless of event count
  - Fire and forget — submit now, collect results later

Two-phase flow:
  batch-run:     fetch events → build requests → submit → save batch_id to DB
  batch-collect: poll until done → display each draft → save/skip interactively

Prompt quality is identical to drafter.py — same system prompt, same event
context format, same model. Cache_control on the system prompt is supported
in batch mode.
"""

import time

from src import storage
from src.drafter import _build_system_prompt, _format_event_context, get_client, load_profile


def build_batch_requests(event_ids: list[int], tone: str = "casual") -> tuple[list[dict], dict[str, int]]:
    """
    Build Anthropic batch request dicts for each event_id.
    Returns (requests_list, custom_id -> event_id mapping).
    """
    system_prompt = _build_system_prompt(load_profile())
    requests = []
    id_map: dict[str, int] = {}

    for event_id in event_ids:
        event = storage.get_event(event_id)
        if not event:
            continue
        custom_id = f"event_{event_id}"
        id_map[custom_id] = event_id
        event_context = _format_event_context(dict(event), tone)
        requests.append({
            "custom_id": custom_id,
            "params": {
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 500,
                "system": [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
                "messages": [
                    {
                        "role": "user",
                        "content": f"Draft an outreach email for this event:\n\n{event_context}",
                    }
                ],
            },
        })

    return requests, id_map


def submit_batch(requests: list[dict]) -> str:
    """Submit requests to the Batches API. Returns the batch_id."""
    batch = get_client().beta.messages.batches.create(requests=requests)
    return batch.id


def poll_until_done(batch_id: str, poll_interval: int = 10) -> None:
    """Block until processing_status == 'ended', printing a live progress line."""
    client = get_client()
    while True:
        batch = client.beta.messages.batches.retrieve(batch_id)
        counts = batch.request_counts
        print(
            f"\r  processing: {counts.processing}  "
            f"succeeded: {counts.succeeded}  "
            f"errored: {counts.errored}   ",
            end="",
            flush=True,
        )
        if batch.processing_status == "ended":
            print()
            return
        time.sleep(poll_interval)


def get_results(batch_id: str) -> list:
    """Return all result objects for a completed batch."""
    return list(get_client().beta.messages.batches.results(batch_id))
