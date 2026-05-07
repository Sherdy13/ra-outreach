"""
Agentic email drafting using Claude tool use.

Instead of pre-packaging all event context into the prompt, Claude is given
tools it can call to fetch exactly the information it needs:
  get_event_details       — full event row from the DB
  check_outreach_history  — days since last contact with this promoter
  find_similar_events     — similar events by embedding cosine similarity

Claude decides which tools to call and when. For a well-known venue it may
skip find_similar_events. For a new promoter it might check history first.
This is the agent loop pattern: Claude → tool call → result → Claude → ...

Token efficiency vs. pre-packaging approach (drafter.py):
- drafter.py: we assemble all context in Python, send it in one shot
- agent.py: Claude fetches only what it needs — more flexible, lower context
             overhead when Claude decides some tools aren't necessary
- Both use cache_control=ephemeral on the system prompt for cache hits
"""

import json

from src import storage
from src.drafter import _build_system_prompt, load_profile, get_client


_TOOLS = [
    {
        "name": "get_event_details",
        "description": (
            "Fetch full details for a saved event from the database, including "
            "title, venue, promoter, genre, description, and contact info."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "integer",
                    "description": "The ID of the event to look up.",
                }
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "check_outreach_history",
        "description": (
            "Check whether this promoter has been contacted recently. "
            "Returns days_ago (float) if contacted within cooldown window, otherwise null."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "promoter_name": {
                    "type": "string",
                    "description": "The promoter's name.",
                },
                "ra_promoter_url": {
                    "type": "string",
                    "description": (
                        "The promoter's RA URL, if available. "
                        "More reliable than name for deduplication."
                    ),
                },
            },
            "required": ["promoter_name"],
        },
    },
    {
        "name": "find_similar_events",
        "description": (
            "Find events in the database that are similar to the given event, "
            "based on embedding cosine similarity. Useful for referencing shared "
            "context or past events in the email."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "integer",
                    "description": "The event to find similar events for.",
                },
                "top_n": {
                    "type": "integer",
                    "description": "Number of similar events to return (default 3).",
                },
            },
            "required": ["event_id"],
        },
    },
]


def _handle_tool_call(name: str, inputs: dict) -> str:
    if name == "get_event_details":
        event = storage.get_event(inputs["event_id"])
        if not event:
            return json.dumps({"error": f"No event with id {inputs['event_id']}"})
        row = dict(event)
        row.pop("embedding", None)  # don't send binary blob to Claude
        return json.dumps(row)

    if name == "check_outreach_history":
        cooldown = 90
        row = storage.get_cooldown_status(
            inputs.get("ra_promoter_url"),
            inputs["promoter_name"],
            cooldown,
        )
        if row:
            return json.dumps({"contacted": True, "days_ago": round(row["days_ago"], 1)})
        return json.dumps({"contacted": False, "days_ago": None})

    if name == "find_similar_events":
        from src.recommender import find_similar
        event = storage.get_event(inputs["event_id"])
        if not event:
            return json.dumps({"error": f"No event with id {inputs['event_id']}"})
        all_events = storage.list_events(limit=500)
        top_n = inputs.get("top_n", 3)
        results = find_similar(dict(event), [dict(e) for e in all_events], top_n=top_n)
        return json.dumps([
            {"title": e["title"], "venue": e["venue"], "similarity": round(score, 3)}
            for e, score in results
        ])

    return json.dumps({"error": f"Unknown tool: {name}"})


def run_agent(event_id: int) -> tuple[str, dict]:
    """
    Run the agentic drafting loop for a single event.
    Returns (draft_text, usage_stats).

    Claude is given the event_id and a set of tools. It decides what to look up
    before writing the email. The loop continues until Claude produces a final
    text response (stop_reason == "end_turn").
    """
    client = get_client()
    system_prompt = _build_system_prompt(load_profile())

    messages = [
        {
            "role": "user",
            "content": (
                f"Draft an outreach email for event ID {event_id}. "
                "Use the available tools to look up event details, check outreach "
                "history, and optionally find similar events for context. "
                "Then write the email."
            ),
        }
    ]

    total_usage = {"input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_created": 0}
    tool_calls_made = 0

    while True:
        with client.messages.stream(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            tools=_TOOLS,
            messages=messages,
        ) as stream:
            # Stream text tokens live — only fires on the final end_turn call.
            # Tool-use rounds produce no text so this is silent during tool calls.
            for token in stream.text_stream:
                print(token, end="", flush=True)
            response = stream.get_final_message()

        total_usage["input_tokens"]  += response.usage.input_tokens
        total_usage["output_tokens"] += response.usage.output_tokens
        total_usage["cache_read"]    += getattr(response.usage, "cache_read_input_tokens", 0) or 0
        total_usage["cache_created"] += getattr(response.usage, "cache_creation_input_tokens", 0) or 0

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            print()  # newline after streamed email
            text = next(
                (block.text for block in response.content if hasattr(block, "text")),
                "",
            ).strip()
            _report_usage(total_usage, tool_calls_made)
            return text, total_usage

        # stop_reason == "tool_use" — dispatch each tool call, feed results back
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                tool_calls_made += 1
                print(f"  → {block.name}({json.dumps(block.input)})")
                result = _handle_tool_call(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
        messages.append({"role": "user", "content": tool_results})


def _report_usage(usage: dict, tool_calls: int) -> None:
    cached = usage.get("cache_read", 0) or 0
    cache_note = f" ({cached} from cache ✓)" if cached else ""
    print(
        f"  Tokens — input: {usage['input_tokens']}{cache_note}, "
        f"output: {usage['output_tokens']}, tool calls: {tool_calls}"
    )
