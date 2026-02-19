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


def cmd_discover(args):
    """Run podcast discovery."""
    profile = _get_profile(args)
    use_exa = "--exa" in args
    discover(profile, use_exa=use_exa)


def cmd_review(args):
    """Interactive review of discovered podcasts."""
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

    # Step 1: Discover
    print("\n--- STEP 1: DISCOVERY ---\n")
    use_exa = "--exa" in args
    discover(profile, use_exa=use_exa)

    # Step 2: Review (interactive)
    print("\n--- STEP 2: REVIEW ---\n")
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
    print("Pipeline complete. Review pitches in data/pitches/")
    print("To approve a pitch: set 'status: approved' in the pitch frontmatter")
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

    contacts = sum(1 for p in podcasts if p.get("contact_email"))
    print(f"\nContacts found: {contacts}")
    print(f"Transcripts cached: {transcript_count}")
    print(f"Pitches generated: {pitch_count}")
    print(f"Pitches approved: {approved_count}")


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
