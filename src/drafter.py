"""
Drafts personalized outreach emails using the Claude API.

Token optimization strategy:
- System prompt uses cache_control=ephemeral — Anthropic caches it server-side
  for ~5 minutes. Cache hits cost ~10x less and process faster. This matters
  when drafting emails in a batch.
- claude-haiku used throughout — short email drafts don't need a bigger model.

Profile:
- Loaded once from profile.json at the project root.
- Injected into the system prompt so every email is signed with real links.
- Changing profile.json changes all future drafts — no code edits needed.
"""

import json
import os
from pathlib import Path

import anthropic

_client = None
_profile = None

PROFILE_PATH = Path(__file__).parent.parent / "profile.json"


def load_profile() -> dict:
    global _profile
    if _profile is None:
        if not PROFILE_PATH.exists():
            raise FileNotFoundError(
                f"profile.json not found at {PROFILE_PATH}\n"
                "Create it with your name, links, and contact info."
            )
        _profile = json.loads(PROFILE_PATH.read_text())
    return _profile


def _build_system_prompt(profile: dict) -> str:
    name = profile.get("name", "the DJ")
    artist_name = profile.get("artist_name", "").strip()
    location = profile.get("location", "")
    genres = ", ".join(profile.get("genres", ["electronic music"]))

    # Use artist name in the body if set, real name in the sign-off
    body_name = artist_name if artist_name else name
    sign_off = f"{artist_name} / {name}" if artist_name else name

    link_fields = {
        "SoundCloud": profile.get("soundcloud"),
        "Mixcloud":   profile.get("mixcloud"),
        "Instagram":  profile.get("instagram"),
        "RA profile": profile.get("ra_profile"),
        "Booking":    profile.get("booking_email"),
    }
    links = "\n".join(
        f"- {label}: {value}"
        for label, value in link_fields.items()
        if value and value.strip()
    )
    extra = profile.get("signature_extra", "").strip()
    signature_block = f"{sign_off}\n{links}" + (f"\n{extra}" if extra else "")

    return f"""You are helping a DJ/artist named {body_name} (real name: {name}) draft short, personalized outreach emails to event promoters and venues.

Profile:
- Artist name: {body_name}
- Based in {location}
- Plays {genres}
- Style: thoughtful, genuine, not pushy — reads the room
- Tone: casual but professional (peer reaching out, not a sales pitch)

Rules for every draft:
- Body under 150 words — promoters are busy
- Reference something specific from the event or venue description
- End the body with a low-pressure ask
- No generic openers ("I hope this finds you well", "My name is {body_name} and I am a DJ")
- Refer to yourself by artist name ({body_name}) in the body
- Address contact by name if known, otherwise address the venue/promoter

Always end every email with this exact signature block, after a blank line:

{signature_block}

Output only the email body + signature — no subject line, no explanation."""


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY is not set.\n"
                "Run: export ANTHROPIC_API_KEY=sk-ant-..."
            )
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


def _format_event_context(event: dict, tone: str) -> str:
    lines = [
        f"Event: {event['title']}",
        f"Date: {event.get('date', 'upcoming')}",
        f"Venue: {event['venue']}",
        f"Promoter/Contact: {event.get('promoter') or 'unknown'}",
        f"Genre: {event.get('genre', 'electronic')}",
        f"Tone: {tone}",
    ]
    if event.get("contact_email"):
        lines.append(f"Contact email: {event['contact_email']}")
    if event.get("contact_website"):
        lines.append(f"Website: {event['contact_website']}")
    if event.get("description"):
        desc = event["description"][:800] + "..." if len(event["description"]) > 800 else event["description"]
        lines.append(f"\nEvent description:\n{desc}")
    return "\n".join(lines)


def _report_usage(usage: dict, label: str = "") -> None:
    prefix = f"[{label}] " if label else ""
    cached = usage.get("cache_read", 0) or 0
    cache_created = usage.get("cache_created", 0) or 0

    cache_note = ""
    if cached:
        cache_note = f" ({cached} from cache ✓)"
    elif cache_created:
        cache_note = f" ({cache_created} cached for next call)"

    print(f"  {prefix}Tokens — input: {usage['input_tokens']}{cache_note}, output: {usage['output_tokens']}")


def _call(messages: list, system_prompt: str) -> tuple[str, dict]:
    client = get_client()
    with client.messages.stream(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
    ) as stream:
        for token in stream.text_stream:
            print(token, end="", flush=True)
        print()  # newline after stream ends
        final = stream.get_final_message()
    usage = {
        "input_tokens":  final.usage.input_tokens,
        "output_tokens": final.usage.output_tokens,
        "cache_read":    getattr(final.usage, "cache_read_input_tokens", 0) or 0,
        "cache_created": getattr(final.usage, "cache_creation_input_tokens", 0) or 0,
    }
    return final.content[0].text.strip(), usage


def draft_email(event: dict, tone: str = "casual") -> tuple[str, dict]:
    """
    Draft an outreach email for an event.
    Returns (email_text, usage_stats).
    First call caches the system prompt; subsequent calls within ~5 min get a cache hit.
    """
    system_prompt = _build_system_prompt(load_profile())
    event_context = _format_event_context(event, tone)
    text, usage = _call(
        messages=[{"role": "user", "content": f"Draft an outreach email for this event:\n\n{event_context}"}],
        system_prompt=system_prompt,
    )
    _report_usage(usage, "draft")
    return text, usage


def refine_email(original_draft: str, feedback: str, event: dict, tone: str = "casual") -> tuple[str, dict]:
    """
    Revise an existing draft based on user feedback.
    Reuses the same cached system prompt — cheaper than the first call.
    """
    system_prompt = _build_system_prompt(load_profile())
    event_context = _format_event_context(event, tone)
    text, usage = _call(
        messages=[
            {"role": "user",      "content": f"Draft an outreach email for this event:\n\n{event_context}"},
            {"role": "assistant", "content": original_draft},
            {"role": "user",      "content": f"Please revise the email. Feedback: {feedback}"},
        ],
        system_prompt=system_prompt,
    )
    _report_usage(usage, "refine")
    return text, usage
