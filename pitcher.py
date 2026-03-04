#!/usr/bin/env python3
"""
Podcast Guest Pitcher — CLI Orchestrator

Discovers podcasts, scores them, enriches with deep RSS analysis,
and syncs to Notion for OpenClaw to handle outreach.

Usage:
  python pitcher.py discover --profile profiles/andreas-duess.md [--exa]
  python pitcher.py enrich --profile profiles/andreas-duess.md [--force]
  python pitcher.py run --profile profiles/andreas-duess.md [--exa]
  python pitcher.py status
"""

import sys
import os
import json

# Ensure we're working from the script's directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from discovery import discover, load_all_podcasts, PODCASTS_DIR
from enrich import enrich_all, sync_enrichment_to_notion
from notion_sync import (
    score_podcasts,
    sync_discoveries_to_notion,
    get_existing_podcast_urls,
    NOTION_TOKEN,
    PODCAST_DB_ID,
)


def cmd_discover(args):
    """Run podcast discovery, score with Claude, push to Notion."""
    profile = _get_profile(args)
    use_exa = "--exa" in args

    results = discover(profile, use_exa=use_exa)

    if not results:
        return

    # Score with Claude
    print("\nScoring podcasts for relevance...")
    scores = score_podcasts(results, profile)

    # Known targets always get High relevance
    for record in results:
        if record.get("known_target"):
            name = record.get("name", "")
            scores[name] = {"name": name, "relevance": "High", "reason": "Known target from guest profile", "skip": False}

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

    # Update local JSON with scores
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


def cmd_enrich(args):
    """Enrich High/Medium podcasts with full RSS analysis via Claude."""
    profile = _get_profile(args)
    force = "--force" in args

    enriched = enrich_all(profile, force=force)

    if enriched and NOTION_TOKEN and PODCAST_DB_ID:
        print("\nSyncing enrichment to Notion...")
        sync_enrichment_to_notion()


def cmd_run(args):
    """Run full pipeline: discover → enrich → Notion (OpenClaw takes over)."""
    print("=" * 60)
    print("PODCAST GUEST PITCHER — Full Pipeline")
    print("=" * 60)

    print("\n--- STEP 1: DISCOVERY ---\n")
    cmd_discover(args)

    print("\n--- STEP 2: ENRICHMENT ---\n")
    cmd_enrich(args)

    print("\n" + "=" * 60)
    print("Pipeline complete. Podcasts enriched and synced to Notion.")
    print("OpenClaw handles outreach from here.")
    print("=" * 60)


def cmd_status(args):
    """Show current pipeline status."""
    podcasts = load_all_podcasts()

    status_counts = {}
    for p in podcasts:
        s = p.get("status", "discovered")
        status_counts[s] = status_counts.get(s, 0) + 1

    relevance_counts = {}
    for p in podcasts:
        r = p.get("relevance", "unscored")
        relevance_counts[r] = relevance_counts.get(r, 0) + 1

    enriched = sum(1 for p in podcasts if p.get("enriched"))

    print("Podcast Guest Pitcher — Status")
    print("=" * 40)
    print(f"\nPodcasts: {len(podcasts)} total")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")

    print(f"\nRelevance:")
    for rel, count in sorted(relevance_counts.items()):
        print(f"  {rel}: {count}")

    print(f"\nEnriched: {enriched}")

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


COMMANDS = {
    "discover": cmd_discover,
    "enrich": cmd_enrich,
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
