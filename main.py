"""
CLI entrypoint for the RA Outreach Tool.

Usage:
  python main.py fetch --city berlin --genre techno --limit 20
  python main.py list
  python main.py draft --event-id 1 --tone casual
  python main.py drafts
  python main.py sent --draft-id 1
  python main.py outreach
  python main.py similar --event-id 1 --top 5
  python main.py run --city berlin --genre techno --limit 5
"""

import argparse
import json
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

from src import storage

PROFILE_PATH = Path(__file__).parent / "profile.json"


def _cooldown_days() -> int:
    try:
        return json.loads(PROFILE_PATH.read_text()).get("outreach_cooldown_days", 90)
    except Exception:
        return 90


def cmd_fetch(args):
    from src.scraper import RAScraper
    cooldown = _cooldown_days()
    scraper = RAScraper()
    events = scraper.fetch_events(city=args.city, genre=args.genre, limit=args.limit)
    saved, skipped = 0, 0
    for event in events:
        row_id = storage.save_event(event)
        if row_id:
            contact = event.contact_email or event.contact_website or event.contact_instagram or "no contact"
            # Warn if this promoter is on cooldown
            on_cooldown = storage.get_cooldown_status(event.ra_promoter_url, event.promoter or "", cooldown)
            cooldown_note = f"  ⚠ contacted {int(on_cooldown['days_ago'])}d ago" if on_cooldown else ""
            print(f"  [{row_id}] {event.title} @ {event.venue}  |  {event.promoter or 'no promoter'}  |  {contact}{cooldown_note}")
            saved += 1
        else:
            print(f"  Skipped (duplicate): {event.title}")
            skipped += 1
    print(f"\n{saved} saved, {skipped} duplicates skipped.")


def cmd_draft(args):
    from src.drafter import draft_email, refine_email
    event = storage.get_event(args.event_id)
    if not event:
        print(f"No event with id {args.event_id}")
        sys.exit(1)

    # Warn if promoter is on cooldown before spending tokens
    cooldown = _cooldown_days()
    on_cooldown = storage.get_cooldown_status(event["ra_promoter_url"], event["promoter"] or "", cooldown)
    if on_cooldown:
        days = int(on_cooldown["days_ago"])
        print(f"⚠  You contacted {event['promoter']} {days} day(s) ago (cooldown: {cooldown} days).")
        proceed = input("Draft anyway? [y/N] ").strip().lower()
        if proceed != "y":
            print("Aborted.")
            return

    event_dict = dict(event)
    print(f"Drafting email for: {event['title']} @ {event['venue']}")
    if event["contact_email"]:
        print(f"Contact: {event['contact_email']}")
    elif event["contact_website"]:
        print(f"Website: {event['contact_website']}")
    print()

    current_draft, _ = draft_email(event_dict, tone=args.tone)
    draft_id = None

    while True:
        print("-" * 60)
        print(current_draft)
        print("-" * 60)
        print("\n[s] save  [r] refine  [q] quit without saving")
        choice = input("> ").strip().lower()

        if choice == "s":
            draft_id = storage.save_draft(args.event_id, current_draft, args.tone)
            print(f"Draft saved (id: {draft_id}). When you send it, run:")
            print(f"  python main.py sent --draft-id {draft_id}")
            break
        elif choice == "r":
            feedback = input("What to change? ").strip()
            if feedback:
                current_draft, _ = refine_email(current_draft, feedback, event_dict, tone=args.tone)
        elif choice == "q":
            print("Discarded.")
            break


def cmd_sent(args):
    """Mark a draft as sent — logs the promoter on cooldown."""
    drafts = storage.list_drafts()
    match = next((d for d in drafts if d["id"] == args.draft_id), None)
    if not match:
        print(f"No draft with id {args.draft_id}")
        sys.exit(1)

    event = storage.get_event(match["event_id"])
    promoter = event["promoter"] or match["venue"]
    storage.log_outreach(
        promoter_name=promoter,
        ra_promoter_url=event["ra_promoter_url"],
        event_id=match["event_id"],
        draft_id=args.draft_id,
    )
    cooldown = _cooldown_days()
    print(f"Logged. {promoter} is on cooldown for {cooldown} days.")


def cmd_outreach(args):
    """Show full outreach history."""
    log = storage.list_outreach_log()
    if not log:
        print("No outreach logged yet. After sending a draft, run: python main.py sent --draft-id <id>")
        return
    for entry in log:
        days = int(entry["days_ago"])
        print(f"  [log {entry['id']}] {entry['promoter_name']} — {entry['title']} @ {entry['venue']}  |  sent {days}d ago ({entry['sent_at'][:10]})")


def cmd_list(args):
    cooldown = _cooldown_days()
    events = storage.list_events()
    if not events:
        print("No events saved yet. Run: python main.py fetch --city <city> --genre <genre>")
        return
    for e in events:
        on_cooldown = storage.get_cooldown_status(e["ra_promoter_url"], e["promoter"] or "", cooldown)
        cooldown_note = f"  ⚠ contacted {int(on_cooldown['days_ago'])}d ago" if on_cooldown else ""
        print(f"  [{e['id']}] {e['title']} — {e['venue']}, {e['city']} ({e['genre']}){cooldown_note}")


def cmd_drafts(args):
    drafts = storage.list_drafts()
    if not drafts:
        print("No drafts saved yet. Run: python main.py draft --event-id <id>")
        return
    for d in drafts:
        print(f"\n[draft {d['id']}] {d['title']} @ {d['venue']}  ({d['tone']}, {d['created_at']})")
        print("-" * 60)
        print(d["body"])
        print("-" * 60)


def cmd_run(args):
    """
    Fetch events then batch-draft emails using the Claude agent loop.

    For each eligible event (not on promoter cooldown), Claude is given a set of
    tools it can call — get_event_details, check_outreach_history, find_similar_events —
    and decides what to look up before writing the email. This is the agentic pattern:
    Claude → tool call → result → Claude → ... → final draft.
    """
    from src.scraper import RAScraper
    from src.agent import run_agent

    cooldown = _cooldown_days()

    print(f"Fetching {args.limit} {args.genre} events in {args.city}...\n")
    scraper = RAScraper()
    events = scraper.fetch_events(city=args.city, genre=args.genre, limit=args.limit)

    eligible_ids: list[int] = []
    for event in events:
        row_id = storage.save_event(event)
        if row_id:
            event_id = row_id
            status = "new"
        else:
            existing = storage.get_event_by_url(event.ra_url)
            event_id = existing["id"] if existing else None
            status = "existing"

        if not event_id:
            continue

        on_cooldown = storage.get_cooldown_status(event.ra_promoter_url, event.promoter or "", cooldown)
        if on_cooldown:
            days = int(on_cooldown["days_ago"])
            print(f"  ⚠ skip (cooldown {days}d): {event.title} @ {event.venue}")
        else:
            eligible_ids.append(event_id)
            print(f"  [{status}] [{event_id}] {event.title} @ {event.venue}")

    if not eligible_ids:
        print("\nNo eligible events — all promoters are on cooldown.")
        return

    print(f"\n{len(eligible_ids)} events eligible. Starting agent drafting loop...\n")

    for event_id in eligible_ids:
        event = storage.get_event(event_id)
        print(f"\n{'='*60}")
        print(f"[{event_id}] {event['title']} @ {event['venue']}")
        print("Running agent...\n")

        draft_text, _ = run_agent(event_id)

        print("\n" + "-" * 60)
        print(draft_text)
        print("-" * 60)
        print("\n[s] save + log outreach  [k] skip  [q] quit")
        choice = input("> ").strip().lower()

        if choice == "s":
            draft_id = storage.save_draft(event_id, draft_text, "casual")
            promoter = event["promoter"] or event["venue"]
            storage.log_outreach(
                promoter_name=promoter,
                ra_promoter_url=event["ra_promoter_url"],
                event_id=event_id,
                draft_id=draft_id,
            )
            print(f"  Saved (draft {draft_id}). {promoter} on cooldown for {cooldown} days.")
        elif choice == "q":
            print("Stopped.")
            break
        else:
            print("  Skipped.")


def cmd_similar(args):
    from src.recommender import find_similar
    target = storage.get_event(args.event_id)
    if not target:
        print(f"No event with id {args.event_id}")
        sys.exit(1)
    all_events = storage.list_events(limit=500)
    results = find_similar(dict(target), [dict(e) for e in all_events], top_n=args.top)
    for event, score in results:
        print(f"  [{score:.2f}] {event['title']} @ {event['venue']}")


def main():
    storage.init_db()

    parser = argparse.ArgumentParser(description="RA Outreach Tool")
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch")
    p_fetch.add_argument("--city", required=True)
    p_fetch.add_argument("--genre", required=True)
    p_fetch.add_argument("--limit", type=int, default=20)

    p_run = sub.add_parser("run", help="Fetch events + batch-draft emails via agent loop")
    p_run.add_argument("--city", required=True)
    p_run.add_argument("--genre", required=True)
    p_run.add_argument("--limit", type=int, default=5)

    p_draft = sub.add_parser("draft")
    p_draft.add_argument("--event-id", type=int, required=True)
    p_draft.add_argument("--tone", default="casual", choices=["casual", "formal"])

    p_sent = sub.add_parser("sent")
    p_sent.add_argument("--draft-id", type=int, required=True)

    p_similar = sub.add_parser("similar")
    p_similar.add_argument("--event-id", type=int, required=True)
    p_similar.add_argument("--top", type=int, default=5)

    sub.add_parser("list")
    sub.add_parser("drafts")
    sub.add_parser("outreach")

    args = parser.parse_args()
    {
        "fetch":    cmd_fetch,
        "run":      cmd_run,
        "draft":    cmd_draft,
        "sent":     cmd_sent,
        "similar":  cmd_similar,
        "list":     cmd_list,
        "drafts":   cmd_drafts,
        "outreach": cmd_outreach,
    }[args.command](args)


if __name__ == "__main__":
    main()
