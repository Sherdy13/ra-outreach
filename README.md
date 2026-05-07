# RA Outreach Tool

A CLI tool for DJs to find events on Resident Advisor, save promoter contact info, and draft personalized outreach emails using AI — with similarity-based event recommendations and built-in spam prevention.

## What it does

1. **Fetches** RA event listings by city and genre via Resident Advisor's GraphQL API
2. **Stores** event data (venue, promoter, description, contacts) in a local SQLite database
3. **Drafts** personalized outreach emails using Claude AI, with a refine loop before saving
4. **Batch-drafts** via an agentic loop — Claude calls tools to fetch event details and outreach history before writing each email
5. **Tracks** who you've contacted with a configurable cooldown window (default 90 days)
6. **Recommends** similar events using Voyage AI embeddings + cosine similarity

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. API keys

Create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=sk-ant-...
VOYAGE_API_KEY=pa-...
```

- **Anthropic API key** — get one at [console.anthropic.com](https://console.anthropic.com)
- **Voyage AI key** — get one at [dashboard.voyageai.com](https://dashboard.voyageai.com)

### 3. Set up your profile

Edit `profile.json` with your details:

```json
{
  "name": "Your Name",
  "artist_name": "YOUR DJ NAME",
  "location": "Berlin",
  "genres": ["techno", "ambient", "experimental"],
  "soundcloud": "https://soundcloud.com/...",
  "instagram": "@...",
  "ra_profile": "https://ra.co/dj/...",
  "booking_email": "you@email.com",
  "outreach_cooldown_days": 90
}
```

Only filled-in fields appear in email signatures.

## Usage

```bash
# Fetch events
python main.py fetch --city berlin --genre techno --limit 20

# List saved events (⚠ flags promoters on cooldown)
python main.py list

# Draft an email for a saved event (interactive: save / refine / quit)
python main.py draft --event-id 3

# View all saved drafts
python main.py drafts

# Mark a draft as sent — puts the promoter on cooldown
python main.py sent --draft-id 1

# View full outreach history
python main.py outreach

# Find events similar to a saved event
python main.py similar --event-id 3 --top 5

# Fetch + batch-draft emails in one go (agent loop)
python main.py run --city berlin --genre techno --limit 5
```

## How it works

### Email drafting
Two modes are available:

- **`draft`** — pre-packages event context and sends it to Claude in one call. Good for single events.
- **`run`** — agentic mode. Claude is given a set of tools (`get_event_details`, `check_outreach_history`, `find_similar_events`) and decides what to look up before writing. More efficient at scale since Claude only fetches what it needs.

Both use the Haiku model. The system prompt is marked with `cache_control: ephemeral`, caching it server-side for ~5 minutes — making repeat calls in a batch ~10x cheaper.

### Event similarity
Each event's title, genre, venue, and description are embedded into a 1024-dimensional vector using Voyage AI's `voyage-3` model. Similarity is measured with cosine similarity. Embeddings are stored in the database after first use — so the API is only called once per event ever.

### Cooldown tracking
When you mark a draft as sent, the promoter is logged with a timestamp. The `list`, `fetch`, and `draft` commands all check against this log and warn you if you've contacted that promoter within the cooldown window. Cooldown period is configurable in `profile.json`.

## Project structure

```
main.py                    — CLI entrypoint (all commands)
profile.json               — Your DJ profile and links
src/
  scraper.py               — RA GraphQL API client
  storage.py               — SQLite: events, drafts, outreach log, embeddings
  drafter.py               — Claude API email drafting + refinement
  recommender.py           — Voyage AI embeddings + cosine similarity
  agent.py                 — Tool definitions + agent loop for batch run command
.claude/
  settings.json            — Claude Code hooks and permissions
  commands/                — Custom Claude Code slash commands
    fetch-events.md        — /fetch-events
    draft-email.md         — /draft-email
    find-similar.md        — /find-similar
data/                      — SQLite database (gitignored)
```

## Claude Code integration

This project is set up for [Claude Code](https://claude.ai/code). `CLAUDE.md` gives Claude context about the project on every session. Custom slash commands in `.claude/commands/` let you trigger common workflows directly from the Claude Code prompt.
