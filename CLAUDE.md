# RA Outreach Tool

A CLI tool for DJs to research Resident Advisor events, save contact info, and draft personalized outreach emails using Claude.

## What this project does

1. **Scrapes** RA event listings by genre and city via the RA GraphQL API
2. **Stores** event data (venue, promoter, description, contacts) in SQLite
3. **Drafts** personalized outreach emails via Claude API (streaming output)
4. **Batch-drafts** via an agentic loop (`run`) or the Batches API (`batch-run`)
5. **Tracks** outreach history with a configurable cooldown window
6. **Recommends** similar events using Voyage AI embedding-based similarity

## Project structure

```
src/
  scraper.py      — RA GraphQL API client
  storage.py      — SQLite operations (events, drafts, outreach log, embeddings, batches)
  drafter.py      — Claude API email drafting with prompt caching + streaming
  agent.py        — Tool definitions + agent loop for the run command
  batcher.py      — Anthropic Batches API submit/poll/collect
  recommender.py  — Voyage AI embeddings + cosine similarity
main.py           — CLI entrypoint
profile.json      — DJ profile (name, links, cooldown settings)
data/events.db    — SQLite database (gitignored)
```

## Running the project

```bash
# Install dependencies
pip install -r requirements.txt

# Fetch events (Berlin, techno)
python main.py fetch --city berlin --genre techno --limit 20

# Draft an outreach email for a saved event (streaming, interactive refine loop)
python main.py draft --event-id 1

# Fetch + agent-draft in one flow (Claude uses tools to fetch context)
python main.py run --city berlin --genre techno --limit 5

# Fetch + submit async batch (up to 50% cheaper, fire and forget)
python main.py batch-run --city berlin --genre techno --limit 10
python main.py batch-collect --batch-id <id>

# Find events similar to a saved event
python main.py similar --event-id 1
```

## Environment variables

- `ANTHROPIC_API_KEY` — required for email drafting and embeddings
- `VOYAGE_API_KEY` — required for event similarity (`similar` command)

## Key design decisions

- Scraper is behind a `BaseScraper` interface so the data source can be swapped without touching downstream code
- Email drafts use `cache_control: ephemeral` on the system prompt to reduce token costs on repeated calls
- All Claude responses stream token-by-token via `client.messages.stream()`
- `run` uses tool use (agent loop) — Claude fetches only the context it needs rather than pre-packaging everything
- `batch-run` uses the Batches API — all requests submitted at once, results collected async at ~50% cost
- SQLite keeps things portable — no external DB needed for local use

## What NOT to do

- Don't commit `data/events.db` (it's gitignored)
- Don't hardcode API keys — always use env vars / `.env`
- Don't send emails automatically — drafts are always reviewed first
