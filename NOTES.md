# Project Notes

Running log of decisions, learnings, and what was done each session.

---

## Session 1 — 2026-05-06

### What we built
- Full project skeleton: directory structure, all source file stubs, CLI entrypoint
- `CLAUDE.md` — Claude Code reads this automatically at startup to understand the project context
- `.claude/settings.json` — project-level Claude Code config with:
  - Bash permissions (python, pip, sqlite3 pre-approved)
  - A `PostToolUse` hook that logs every file write to `data/claude_activity.log`
- `.claude/commands/` — three custom slash commands usable inside Claude Code:
  - `/fetch-events` — trigger a scrape
  - `/draft-email` — trigger email drafting for a saved event
  - `/find-similar` — trigger similarity search
- `src/scraper.py` — `Event` dataclass + `BaseScraper` abstract class + `RAScraper` stub
- `src/storage.py` — SQLite schema (events, drafts tables) + CRUD functions
- `src/drafter.py` — Claude API call with **prompt caching** on system prompt
- `src/recommender.py` — cosine similarity stub (needs embeddings implemented)
- `main.py` — argparse CLI wiring all commands together
- `requirements.txt`, `.gitignore`

### Key concepts introduced

**CLAUDE.md**
Claude Code automatically reads this file when starting a session in the project directory. It gives Claude context about what the project does, how to run it, and important constraints. Think of it as a README specifically written for your AI assistant.

**`.claude/settings.json`**
Project-level config for Claude Code. The two main things here:
- `permissions.allow` — pre-approve shell commands so Claude doesn't prompt you every time
- `hooks` — shell commands that run automatically on Claude Code events (PostToolUse, PreToolUse, etc.)

**Hooks**
A hook is a shell command Claude Code runs when something happens (e.g. after writing a file). They're defined in settings.json. Our hook logs all file writes to a log file. Later we could add hooks that, e.g., auto-run tests after every edit, or validate a draft before saving.

**Prompt caching**
When you call the Claude API repeatedly with the same system prompt, you can mark it with `cache_control: {type: "ephemeral"}`. Anthropic caches the processed prompt for ~5 minutes. Cached tokens are ~10x cheaper and faster. This matters a lot when you're drafting emails in a loop.

**`BaseScraper` interface**
`RAScraper` inherits from `BaseScraper`. This means we can write a `MockScraper` for testing, or a `FutureSiteScraper` later, without touching `storage.py`, `drafter.py`, or `main.py`. Classic dependency inversion — the business logic doesn't care where the data comes from.

### Next session
- Implement `RAScraper.fetch_events()` — HTTP fetch + BeautifulSoup parsing of RA search results
- Test with a real city + genre
- Decide on RA URL structure to target

---

## Session 2 — 2026-05-06

### What we built
- Fully implemented `RAScraper` in `src/scraper.py` — fetches real events from RA's internal GraphQL API
- Updated `src/storage.py` schema with new fields: `contact_website`, `contact_instagram`, `ra_promoter_url`
- Updated `main.py` fetch output to show promoter + contact info
- Tested live: 10 Berlin techno events fetched and stored successfully

### How the RA GraphQL API works

**Endpoint:** `https://ra.co/graphql` (POST, no auth needed)  
**Headers required:** `Content-Type: application/json`, `Referer: https://ra.co/events`, `User-Agent` (browser string)

**Key queries we discovered by introspecting the schema:**

| Query | What it does |
|---|---|
| `areas(searchTerm: "berlin")` | Looks up area id by city name |
| `area(areaUrlName: "berlin")` | Doesn't work (undocumented arg) |
| `eventListings(filters, pageSize, page)` | Main event fetch |
| `genres { id name }` | Full genre list |

**Genre filtering:** Use `genre: {eq: "techno"}` (lowercase string) in the filters. Using genre IDs or capitalized names returns 0 results.

**Promoter contact fields available:** `email`, `website`, `instagram`, `facebook`, `contentUrl`  
In practice, most promoters on RA don't have email set — but website is usually present.

**Pagination:** `pageSize` (max 20 per page), `page` (1-indexed). `totalResults` tells you total available.

**Why we used `areas(searchTerm:)` instead of hardcoded IDs:**  
Hardcoding area IDs would break for any city the user types. The search query lets users type "London" or "berlin" and get the right area ID dynamically.

**Rate limiting:** Added 0.5s sleep between pages. RA didn't block us during testing.

### Key design decisions
- `Event` dataclass extended with `contact_website`, `contact_instagram`, `ra_promoter_url`
- Contact selection: prefer promoter with email → fall back to first promoter → null
- Description comes from `content` field (not `blurb`) — includes full multi-paragraph text

### Next session
- Implement `drafter.py` — Claude API email drafting with prompt caching
- Test with a real saved event (event id 6, ://about blank, has rich description)
- Show token usage: input tokens vs cached tokens in output

---

## Session 3 — 2026-05-06

### What we built
- Fully implemented `src/drafter.py` with Claude API, prompt caching, and refinement loop
- `profile.json` — user config for name, artist name, links, cooldown settings
- `draft` command — interactive loop with save / refine / quit
- `drafts` command — view all saved drafts
- `sent` command — mark a draft as sent, logs promoter on cooldown
- `outreach` command — full outreach history
- `outreach_log` table in SQLite — tracks promoter, event, draft, sent_at
- Cooldown warnings appear in `list` and `fetch` output (⚠ contacted Xd ago)

### Key concepts

**Prompt caching**
System prompt marked with `cache_control: {type: "ephemeral"}`. First call creates the cache entry (shown as "X cached for next call"). Subsequent calls within ~5 min get a cache hit ("X from cache ✓"). Cached tokens cost ~10x less. Most visible when doing batch drafting.

**Profile-driven system prompt**
`_build_system_prompt(profile)` reads `profile.json` and builds the system prompt dynamically. Artist name used in email body, real name in sign-off (`DATA RYDER / Ryan`). Only filled-in links appear in the signature. Changing `profile.json` changes all future drafts — no code edits.

**`python-dotenv`**
Used `load_dotenv(..., override=True)` to load `.env`. The `override=True` is required — without it, dotenv silently skips vars that exist in the environment (even as empty strings).

**Cooldown tracking**
- `outreach_log` table stores sent_at as UTC via SQLite `datetime('now')`
- Cooldown check uses `julianday('now') - julianday(sent_at)` — pure SQL date math
- Matches on `ra_promoter_url` first, falls back to `promoter_name`
- Cooldown window configurable via `outreach_cooldown_days` in `profile.json` (default 90)

### Next session
- Implement `src/recommender.py` — embed event descriptions, find similar events by cosine similarity
- Use Voyage embeddings via Anthropic API
- Test `similar` command with real saved events

---

## Session 4 — 2026-05-06

### What we built
- Fully implemented `src/recommender.py` — Voyage AI embeddings + cosine similarity
- Embedding storage: serialized as binary BLOBs in the existing `embedding` column
- Lazy caching: embed once, store in DB, reuse forever — zero API calls on repeat runs
- Batching: all uncached events embedded in a single API call (critical for rate limits)
- `similar` command working end to end

### Key concepts

**Embeddings**
A vector (list of ~1024 floats) that represents the *meaning* of a piece of text. Similar texts produce similar vectors. Used here to find events with a similar vibe/genre/description without exact keyword matching.

**Cosine similarity**
Measures the angle between two vectors. Score of 1.0 = identical direction (same meaning), 0.0 = perpendicular (unrelated). Formula: dot(a,b) / (|a| * |b|). Implemented with numpy for efficiency.

**Lazy caching pattern**
Don't embed everything upfront. On first request, check DB → if missing, call API and store → return result. Subsequent calls hit DB only. Cost and latency paid once per event ever. This is the right pattern at any scale.

**Batching**
Instead of one API call per event (hits rate limits, slow), collect all uncached events and send them in one call. Voyage supports batching natively. At scale, you'd further split into chunks of e.g. 100 events per call.

**Scaling beyond this**
For thousands of events, cosine similarity over all pairs in Python becomes slow. The next step would be a vector database (pgvector, Pinecone, Chroma) which does approximate nearest-neighbour search in milliseconds. The embedding logic stays identical — only the search backend changes.

**Voyage free tier gotcha**
Requires a payment method on file to unlock normal rate limits (3 RPM → standard). The 200M free tokens still apply — no actual charge. Same pattern as Anthropic console.

### Next steps
- Project is functionally complete for interview demo
- Possible additions: batch draft review flow, CLAUDE.md polish, git init + README

---

## Session 5 — 2026-05-07

### What we built
- `README.md` — full setup and usage docs, pushed to GitHub
- Git initialized, initial commit, remote set to github.com/Sherdy13/ra-outreach
- `.env` confirmed never committed (keys safe)

### Next session — BUILD THIS
Add a `run` command that combines fetch + batch draft into one flow, using **Claude tool use** (function calling) for the drafting step.

**The command:**
```
python main.py run --city berlin --genre techno --limit 5
```

**What it does:**
1. Fetches events for city/genre, skips any promoter on cooldown automatically
2. For each event, runs Claude as an agent with tools it can call:
   - `get_event_details(event_id)` — fetch full event from DB
   - `check_outreach_history(promoter_name, ra_promoter_url)` — days since last contact
   - `find_similar_events(event_id, top_n)` — similar events for context
3. Claude decides what to look up rather than us pre-packaging everything
4. Shows each draft: [s]end / [e]dit / [s]kip
5. Marks sent drafts in outreach log automatically

**Why tool use matters (explain in interview):**
- Current approach: we build ALL context in Python, pass it to Claude → lots of tokens every call
- Tool use: Claude starts minimal, calls tools to fetch only what it needs → more efficient, more flexible
- This is the agent loop pattern: Claude → tool call → result → Claude → ... → final answer
- At scale, Claude might skip `find_similar_events` for obvious venues, saving tokens

**New file to create:** `src/agent.py`
- Define tools schema (JSON Schema format for Anthropic API)
- Implement agent loop: call Claude → handle tool calls → feed results back → repeat until done
- Import from `storage.py` and `recommender.py` for tool implementations

**Context window optimization notes (for interview):**
- Tool use is inherently more token-efficient than pre-packaging all context
- Prompt caching on system prompt (already doing this)
- Right-size model: Haiku for drafting, not Sonnet
- In production: summarize long descriptions before embedding in prompts

---

## Session 6 — 2026-05-07

### What we built
- `src/agent.py` — tool definitions + agent loop for agentic email drafting
- `src/storage.py` — added `get_event_by_url()` for resolving duplicate event IDs
- `main.py` — added `run` subcommand (fetch + batch-draft in one flow)
- PR: https://github.com/Sherdy13/ra-outreach/pull/1

### How the agent loop works

1. User runs `python main.py run --city berlin --genre techno --limit 5`
2. Events are fetched and saved; promoters on cooldown are skipped
3. For each eligible event, `run_agent(event_id)` starts the loop:
   - User message: "Draft an outreach email for event ID {id}. Use the tools."
   - Claude calls `get_event_details` → we return the DB row as JSON
   - Claude optionally calls `check_outreach_history` and/or `find_similar_events`
   - Claude produces the final draft (`stop_reason == "end_turn"`)
4. Draft is shown; user picks `[s]ave / [k]ip / [q]uit`
5. Saving a draft also calls `log_outreach` — starts the cooldown timer

### Tool definitions (Anthropic format)

Each tool is a dict with `name`, `description`, and `input_schema` (JSON Schema).
Tool calls come back in `response.content` as blocks with `type == "tool_use"`.
Results go back as `{"type": "tool_result", "tool_use_id": block.id, "content": <json string>}`.
The loop continues until `stop_reason == "end_turn"`.

### Key contrast: drafter.py vs agent.py

| | drafter.py | agent.py |
|---|---|---|
| Context assembly | Python pre-packages everything | Claude fetches only what it needs |
| Tool calls | None | 2–3 per draft typically |
| Token efficiency | Fixed cost per event | Variable — Claude skips unnecessary calls |
| Flexibility | Rigid | Claude can adapt based on what it finds |

### Next ideas
- ~~Streaming output while Claude writes the draft~~ ✓ done session 7
- ~~Message Batches API~~ ✓ done session 7

---

## Session 7 — 2026-05-07

### What we built
- Streaming output in `drafter.py` and `agent.py` — tokens print live as Claude writes
- `src/batcher.py` — Anthropic Batches API: submit, poll, collect
- `src/storage.py` — `batches` table + CRUD
- `main.py` — three new commands: `batch-run`, `batch-collect`, `batches`
- Updated CLAUDE.md, README.md, NOTES.md

### Streaming (drafter.py + agent.py)

**Change:** `client.messages.create()` → `client.messages.stream()` context manager.

```python
with client.messages.stream(...) as stream:
    for token in stream.text_stream:
        print(token, end="", flush=True)
    final = stream.get_final_message()  # usage stats + full content
```

`stream.text_stream` is a generator that yields text tokens only. In the agent loop, tool-use rounds produce no text so the generator is silent — tool calls print their own `→ tool_name(...)` line. The final `end_turn` call streams the email.

In `cmd_draft`, the separator lines now wrap the streaming call so text appears between them — no post-call reprint needed.

### Message Batches API (batcher.py)

**Submit:**
```python
batch = client.beta.messages.batches.create(requests=[
    {"custom_id": "event_3", "params": {model, max_tokens, system, messages}},
    ...
])
batch_id = batch.id
```

Each `params` dict is identical to a `messages.create()` call — same model, same system prompt with `cache_control`, same messages format.

**Poll:**
```python
batch = client.beta.messages.batches.retrieve(batch_id)
batch.processing_status  # "in_progress" | "ended"
batch.request_counts     # .processing, .succeeded, .errored
```

**Collect results:**
```python
for result in client.beta.messages.batches.results(batch_id):
    result.custom_id          # "event_3"
    result.result.type        # "succeeded" | "errored" | "expired"
    result.result.message     # full Message object (if succeeded)
```

**custom_id → event_id mapping** stored as JSON in the `batches` table so we can match results back to events after the fact.

### Drafting mode comparison (interview table)

| | `draft` | `run` | `batch-run` |
|---|---|---|---|
| API pattern | messages.stream | messages.stream + tool use | Batches API (async) |
| Context assembly | Python pre-packages | Claude fetches via tools | Python pre-packages |
| Output | Streams live | Streams live (final call) | No live output |
| Cost | Standard | Standard + tool overhead | ~50% cheaper |
| Best for | Single event | Small batch, want live output | Bulk, no rush |

