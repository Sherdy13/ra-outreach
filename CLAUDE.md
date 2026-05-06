# RA Outreach Tool

A CLI tool for DJs to research Resident Advisor events, save contact info, and draft personalized outreach emails using Claude.

## What this project does

1. **Scrapes** RA event listings by genre and city
2. **Stores** event data (venue, promoter, description, contacts) in SQLite
3. **Drafts** personalized outreach emails via Claude API
4. **Recommends** similar events using embedding-based similarity

## Project structure

```
src/
  scraper.py      — Fetches and parses RA event pages
  storage.py      — SQLite operations (events, contacts, drafts)
  drafter.py      — Claude API email drafting with prompt caching
  recommender.py  — Embedding-based event similarity
main.py           — CLI entrypoint
data/events.db    — SQLite database (gitignored)
```

## Running the project

```bash
# Install dependencies
pip install -r requirements.txt

# Fetch events (Berlin, techno)
python main.py fetch --city berlin --genre techno --limit 20

# Draft an outreach email for a saved event
python main.py draft --event-id 1

# Find events similar to a saved event
python main.py similar --event-id 1
```

## Environment variables

- `ANTHROPIC_API_KEY` — required for email drafting and embeddings

## Key design decisions

- Scraper is behind a `BaseScraper` interface so the data source can be swapped without touching downstream code
- Email drafts use prompt caching on the system prompt to reduce token costs on repeated calls
- SQLite keeps things portable — no external DB needed for local use

## What NOT to do

- Don't commit `data/events.db` (it's gitignored)
- Don't hardcode the Anthropic API key — always use the env var
- Don't send emails automatically — drafts are always reviewed first
