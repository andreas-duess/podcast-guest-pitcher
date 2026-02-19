#!/usr/bin/env python3
"""
Podcast Guest Pitcher — CLI Orchestrator

Usage:
  python pitcher.py discover --profile profiles/andreas-duess.md [--exa]
  python pitcher.py review
  python pitcher.py transcribe
  python pitcher.py pitch --profile profiles/andreas-duess.md
  python pitcher.py contacts [--apollo]
  python pitcher.py send [--dry-run]
  python pitcher.py run --profile profiles/andreas-duess.md
  python pitcher.py status
"""

import sys
import os
import json

# Ensure we're working from the script's directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from discovery import discover, load_all_podcasts, load_pursued_podcasts, PODCASTS_DIR
from transcripts import fetch_transcripts, transcribe_all_pursued
from analyzer import generate_pitch, generate_all_pitches
from contacts import find_all_contacts
from outreach import send_approved_pitches
from notion_sync import (
    score_podcasts,
    sync_discoveries_to_notion,
    get_existing_podcast_urls,
    push_pitch_to_notion,
    NOTION_TOKEN,
    PODCAST_DB_ID,
    PITCH_DB_ID,
)


def cmd_discover(args):
    """Run podcast discovery, score with Claude, push to Notion."""
    profile = _get_profile(args)
    use_exa = "--exa" in args

    # Discover
    results = discover(profile, use_exa=use_exa)

    if not results:
        return

    # Score with Claude
    print("\nScoring podcasts for relevance...")
    scores = score_podcasts(results, profile)

    high = sum(1 for s in scores.values() if s.get("relevance") == "High")
    med = sum(1 for s in scores.values() if s.get("relevance") == "Medium")
    low = sum(1 for s in scores.values() if s.get("relevance") == "Low")
    skipped = sum(1 for s in scores.values() if s.get("skip"))
    print(f"  Scores: {high} High, {med} Medium, {low} Low, {skipped} not-a-podcast")

    # Sync to Notion
    if NOTION_TOKEN and PODCAST_DB_ID:
        print("\nSyncing to Notion...")
        existing = get_existing_podcast_urls()
        created, dup, dropped = sync_discoveries_to_notion(results, scores, existing)
        print(f"  Created {created} Notion pages ({dup} duplicates skipped, {dropped} low-relevance dropped)")
    else:
        print("\nNotion not configured — results saved to local JSON only.")

    # Also update local JSON with scores
    for record in results:
        name = record.get("name", "")
        if name in scores:
            score = scores[name]
            record["relevance"] = score.get("relevance", "")
            record["score_reason"] = score.get("reason", "")
            if score.get("skip"):
                record["status"] = "skipped"

            slug = record.get("slug", "unknown")
            filepath = os.path.join(PODCASTS_DIR, f"{slug}.json")
            if os.path.exists(filepath):
                with open(filepath, "r") as f:
                    existing_record = json.load(f)
                existing_record["relevance"] = record["relevance"]
                existing_record["score_reason"] = record["score_reason"]
                if score.get("skip"):
                    existing_record["status"] = "skipped"
                with open(filepath, "w") as f:
                    json.dump(existing_record, f, indent=2)


def cmd_review(args):
    """Interactive review of discovered podcasts (or review in Notion)."""
    if NOTION_TOKEN and PODCAST_DB_ID:
        print("Podcasts are in Notion — review and set status there.")
        print("Mark podcasts as 'Pursuing' in the Status column to include them in the pipeline.")
        print("\nTo use CLI review instead, unset NOTION_TOKEN in .env\n")

    podcasts = load_all_podcasts()
    unreviewed = [p for p in podcasts if p.get("status") == "discovered"]

    if not unreviewed:
        print("No unreviewed podcasts. Run 'discover' first.")
        return

    print(f"\n{len(unreviewed)} podcasts to review ({len(podcasts)} total)\n")
    print("For each podcast, enter:")
    print("  y = pursue (will fetch transcripts + generate pitch)")
    print("  n = skip")
    print("  q = quit review\n")

    reviewed = 0
    pursued = 0

    for i, podcast in enumerate(unreviewed, 1):
        print(f"--- [{i}/{len(unreviewed)}] ---")
        print(f"  Name: {podcast.get('name', 'Unknown')}")
        print(f"  Host: {podcast.get('host', 'Unknown')}")
        print(f"  Relevance: {podcast.get('relevance', '?')}")
        if podcast.get("score_reason"):
            print(f"  Why: {podcast['score_reason']}")
        print(f"  Source: {podcast.get('source', '')}")
        print(f"  Website: {podcast.get('website', '')}")
        print(f"  Episodes: {podcast.get('episode_count', '?')}")
        print(f"  Last episode: {podcast.get('last_episode_date', '?')}")

        desc = podcast.get("description", "")
        if desc:
            print(f"  Description: {desc[:200]}...")

        episodes = podcast.get("recent_episodes", [])
        if episodes:
            print(f"  Recent episodes:")
            for ep in episodes[:3]:
                print(f"    - {ep.get('title', '')[:60]} ({ep.get('date', '')})")

        print()
        try:
            choice = input("  Pursue? [y/n/q]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n\nReview ended.")
            break

        if choice == "q":
            break

        slug = podcast.get("slug", "unknown")
        filepath = os.path.join(PODCASTS_DIR, f"{slug}.json")

        if choice == "y":
            podcast["pursue"] = True
            podcast["status"] = "pursuing"
            pursued += 1
        else:
            podcast["pursue"] = False
            podcast["status"] = "skipped"

        with open(filepath, "w") as f:
            json.dump(podcast, f, indent=2)

        reviewed += 1

    print(f"\nReviewed {reviewed} podcasts. {pursued} marked for pursuit.")


def cmd_transcribe(args):
    """Fetch transcripts for all pursued podcasts."""
    pursued = load_pursued_podcasts()
    if not pursued:
        print("No pursued podcasts. Run 'review' first.")
        return

    print(f"Transcribing {len(pursued)} pursued podcasts...\n")
    paths = transcribe_all_pursued(pursued)
    print(f"\nFetched {len(paths)} transcripts total.")


def cmd_pitch(args):
    """Generate pitches for all pursued podcasts."""
    profile = _get_profile(args)
    pursued = load_pursued_podcasts()
    if not pursued:
        print("No pursued podcasts. Run 'review' first.")
        return

    print(f"Generating pitches for {len(pursued)} pursued podcasts...\n")
    paths = generate_all_pitches(pursued, profile)
    print(f"\nGenerated {len(paths)} pitches. Review in data/pitches/")

    # Sync pitches to Notion
    if NOTION_TOKEN and PITCH_DB_ID and paths:
        print("\nSyncing pitches to Notion...")
        from analyzer import load_profile as load_analyzer_profile
        import yaml

        synced = 0
        for path in paths:
            from outreach import load_pitch
            pitch_data = load_pitch(path)
            page_id = push_pitch_to_notion(pitch_data)
            if page_id:
                synced += 1
        print(f"  Created {synced} pitch pages in Notion")


def cmd_contacts(args):
    """Find contact info for pursued podcasts."""
    use_apollo = "--apollo" in args
    pursued = load_pursued_podcasts()
    if not pursued:
        print("No pursued podcasts. Run 'review' first.")
        return

    print(f"Finding contacts for {len(pursued)} pursued podcasts...")
    if use_apollo:
        print("(Apollo enrichment enabled — will use credits)\n")
    else:
        print("(Apollo disabled — use --apollo to enable)\n")

    found = find_all_contacts(pursued, use_apollo=use_apollo)
    print(f"\nFound contacts for {found}/{len(pursued)} podcasts.")


def cmd_send(args):
    """Send approved pitches via Lemlist."""
    dry_run = "--dry-run" in args
    if dry_run:
        print("DRY RUN — will not create Lemlist campaign\n")
    send_approved_pitches(dry_run=dry_run)


def cmd_run(args):
    """Run full pipeline: discover → review → transcribe → pitch."""
    profile = _get_profile(args)

    print("=" * 60)
    print("PODCAST GUEST PITCHER — Full Pipeline")
    print("=" * 60)

    # Step 1: Discover + Score + Notion sync
    print("\n--- STEP 1: DISCOVERY ---\n")
    cmd_discover(args)

    # Step 2: Review
    print("\n--- STEP 2: REVIEW ---\n")
    if NOTION_TOKEN and PODCAST_DB_ID:
        print("Review podcasts in Notion, then re-run with 'transcribe' and 'pitch' commands.")
        print("Pipeline paused for Notion review.")
        return
    else:
        cmd_review(args)

    pursued = load_pursued_podcasts()
    if not pursued:
        print("\nNo podcasts pursued. Pipeline complete.")
        return

    # Step 3: Transcribe
    print("\n--- STEP 3: TRANSCRIPTS ---\n")
    transcribe_all_pursued(pursued)

    # Step 4: Find contacts
    print("\n--- STEP 4: CONTACTS ---\n")
    find_all_contacts(pursued)

    # Step 5: Generate pitches
    print("\n--- STEP 5: PITCH GENERATION ---\n")
    generate_all_pitches(pursued, profile)

    print("\n" + "=" * 60)
    print("Pipeline complete. Review pitches in data/pitches/ or Notion.")
    print("To approve: set status to 'Approved' in Notion or frontmatter")
    print("To send: python pitcher.py send")
    print("=" * 60)


def cmd_status(args):
    """Show current pipeline status."""
    podcasts = load_all_podcasts()
    pitches_dir = os.path.join(os.path.dirname(__file__), "data", "pitches")
    transcripts_dir = os.path.join(os.path.dirname(__file__), "data", "transcripts")

    # Count by status
    status_counts = {}
    for p in podcasts:
        s = p.get("status", "discovered")
        status_counts[s] = status_counts.get(s, 0) + 1

    # Count by relevance
    relevance_counts = {}
    for p in podcasts:
        r = p.get("relevance", "unscored")
        relevance_counts[r] = relevance_counts.get(r, 0) + 1

    # Count transcripts
    transcript_count = 0
    if os.path.isdir(transcripts_dir):
        for d in os.listdir(transcripts_dir):
            subdir = os.path.join(transcripts_dir, d)
            if os.path.isdir(subdir):
                transcript_count += len([f for f in os.listdir(subdir) if f.endswith(".md")])

    # Count pitches
    pitch_count = 0
    approved_count = 0
    if os.path.isdir(pitches_dir):
        for f in os.listdir(pitches_dir):
            if f.endswith(".md"):
                pitch_count += 1
                path = os.path.join(pitches_dir, f)
                with open(path, "r") as fh:
                    if "status: approved" in fh.read():
                        approved_count += 1

    print("Podcast Guest Pitcher — Status")
    print("=" * 40)
    print(f"\nPodcasts: {len(podcasts)} total")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")

    print(f"\nRelevance:")
    for rel, count in sorted(relevance_counts.items()):
        print(f"  {rel}: {count}")

    contacts = sum(1 for p in podcasts if p.get("contact_email"))
    print(f"\nContacts found: {contacts}")
    print(f"Transcripts cached: {transcript_count}")
    print(f"Pitches generated: {pitch_count}")
    print(f"Pitches approved: {approved_count}")

    if NOTION_TOKEN and PODCAST_DB_ID:
        print(f"\nNotion: connected")
    else:
        print(f"\nNotion: not configured")


def _get_profile(args):
    """Extract --profile argument or use default."""
    for i, arg in enumerate(args):
        if arg == "--profile" and i + 1 < len(args):
            return args[i + 1]
    return "profiles/andreas-duess.md"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

COMMANDS = {
    "discover": cmd_discover,
    "review": cmd_review,
    "transcribe": cmd_transcribe,
    "pitch": cmd_pitch,
    "contacts": cmd_contacts,
    "send": cmd_send,
    "run": cmd_run,
    "status": cmd_status,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        print("Commands:")
        for name in COMMANDS:
            print(f"  {name}")
        sys.exit(0)

    command = sys.argv[1]
    if command not in COMMANDS:
        print(f"Unknown command: {command}")
        print(f"Available: {', '.join(COMMANDS.keys())}")
        sys.exit(1)

    COMMANDS[command](sys.argv[2:])


if __name__ == "__main__":
    main()
