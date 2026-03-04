"""
Enrichment Module

Fetches full RSS episode descriptions (last 20 episodes) and uses Claude
to produce structured analysis per podcast — key themes, notable guests,
relevant episodes, coverage gaps, and suggested hooks — so pitch emails
are genuinely personalized.

Usage:
  python pitcher.py enrich --profile profiles/andreas-duess.md [--force]
"""

import os
import re
import json

import feedparser
from anthropic import Anthropic
from dotenv import load_dotenv

from discovery import load_all_podcasts, PODCASTS_DIR

load_dotenv()

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]


ENRICHMENT_PROMPT = """You are analyzing a podcast to help craft a highly personalized guest pitch.

## Guest Profile
{guest_profile}

## Podcast
Name: {podcast_name}
Host: {host}
Description: {description}

## Last {episode_count} Episode Descriptions
{episode_descriptions}

## Your Task

Based on the episode descriptions above, produce a structured analysis:

1. **Key Themes** — 3-5 recurring topics this podcast covers consistently
2. **Notable Guests** — Names of notable guests mentioned in episode descriptions (if any)
3. **Relevant Episode** — The single best episode match for this guest's expertise. Include the episode title and a 1-sentence explanation of why it's relevant.
4. **Coverage Gap** — One topic this podcast hasn't covered (or barely touched) that the guest could fill
5. **Suggested Hook** — A one-liner pitch angle connecting the guest's expertise to what this podcast's audience cares about

Return ONLY valid JSON with these exact keys:
{{
  "key_themes": ["theme1", "theme2", "theme3"],
  "notable_guests": ["name1", "name2"],
  "relevant_episode": "Episode Title — why it's relevant",
  "coverage_gap": "What they haven't covered that the guest could fill",
  "suggested_hook": "One-liner pitch angle"
}}"""


def _get_episode_description(entry):
    """Extract the best available description from an RSS entry."""
    if entry.get("content"):
        desc = entry["content"][0].get("value", "")
        if desc:
            desc = re.sub(r'<[^>]+>', ' ', desc)
            desc = re.sub(r'\s+', ' ', desc).strip()
            return desc[:2000]
    summary = entry.get("summary", "") or ""
    summary = re.sub(r'<[^>]+>', ' ', summary)
    summary = re.sub(r'\s+', ' ', summary).strip()
    return summary[:2000]


def fetch_episode_descriptions(rss_url, max_episodes=20):
    """Parse RSS feed and return recent episodes."""
    if not rss_url:
        return []
    try:
        feed = feedparser.parse(rss_url)
        episodes = []
        for entry in feed.entries[:max_episodes]:
            episodes.append({
                "title": entry.get("title", "Untitled"),
                "date": entry.get("published", ""),
                "description": _get_episode_description(entry),
            })
        return episodes
    except Exception as e:
        print(f"  RSS parse failed for {rss_url}: {e}")
        return []


def enrich_podcast(podcast_record, guest_profile_text):
    """Send episode descriptions + guest profile to Claude, return structured enrichment."""
    rss_url = podcast_record.get("rss_url", "")
    episodes = fetch_episode_descriptions(rss_url, max_episodes=20)

    if not episodes:
        print(f"    No episodes from RSS — skipping enrichment")
        return None

    # Build episode descriptions text
    ep_text_parts = []
    for i, ep in enumerate(episodes, 1):
        title = ep.get("title", "Untitled")
        date = ep.get("date", "")
        desc = ep.get("description", "")
        ep_text_parts.append(f"{i}. [{date}] {title}\n   {desc[:500]}")

    episode_descriptions = "\n\n".join(ep_text_parts)

    prompt = ENRICHMENT_PROMPT.format(
        guest_profile=guest_profile_text,
        podcast_name=podcast_record.get("name", ""),
        host=podcast_record.get("host", "Unknown"),
        description=podcast_record.get("description", ""),
        episode_count=len(episodes),
        episode_descriptions=episode_descriptions,
    )

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = response.content[0].text

    # Parse JSON from response
    try:
        enrichment = json.loads(response_text)
    except json.JSONDecodeError:
        # Try to extract JSON from response
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                enrichment = json.loads(response_text[start:end])
            except json.JSONDecodeError:
                print(f"    Warning: Could not parse enrichment response")
                return None
        else:
            print(f"    Warning: No JSON found in enrichment response")
            return None

    return enrichment


def save_enrichment(podcast_record, enrichment):
    """Write enrichment dict to local podcast JSON."""
    slug = podcast_record.get("slug", "unknown")
    filepath = os.path.join(PODCASTS_DIR, f"{slug}.json")

    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            data = json.load(f)
    else:
        data = podcast_record

    data["enrichment"] = enrichment
    data["enriched"] = True

    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)

    return filepath


def _load_guest_profile(profile_path):
    """Load guest profile text (body after frontmatter)."""
    with open(profile_path, "r") as f:
        content = f.read()
    if content.startswith("---"):
        parts = content.split("---", 2)
        return parts[2].strip() if len(parts) >= 3 else content
    return content


def enrich_all(profile_path, force=False):
    """Orchestrator: filter to unenriched High/Medium podcasts, run enrichment, save."""
    guest_profile = _load_guest_profile(profile_path)
    podcasts = load_all_podcasts()

    # Filter to High/Medium relevance, unenriched (unless force)
    targets = []
    for p in podcasts:
        relevance = p.get("relevance", "")
        if relevance not in ("High", "Medium"):
            continue
        if not force and p.get("enriched"):
            continue
        if not p.get("rss_url"):
            continue
        targets.append(p)

    if not targets:
        print("No podcasts to enrich (all already enriched or no High/Medium with RSS).")
        return []

    print(f"Enriching {len(targets)} podcasts...\n")

    enriched = []
    for i, podcast in enumerate(targets, 1):
        name = podcast.get("name", "Unknown")
        print(f"  [{i}/{len(targets)}] {name}")

        enrichment = enrich_podcast(podcast, guest_profile)
        if enrichment:
            save_enrichment(podcast, enrichment)
            enriched.append(podcast)
            print(f"    Enriched: {', '.join(enrichment.get('key_themes', [])[:3])}")
        else:
            print(f"    Skipped (no enrichment)")

    print(f"\nEnriched {len(enriched)}/{len(targets)} podcasts.")
    return enriched


def sync_enrichment_to_notion():
    """Look up Notion page IDs by URL, PATCH enrichment fields."""
    from notion_sync import (
        get_podcast_page_ids,
        update_podcast_enrichment,
        NOTION_TOKEN,
        PODCAST_DB_ID,
    )

    if not NOTION_TOKEN or not PODCAST_DB_ID:
        print("Notion not configured — skipping enrichment sync.")
        return 0

    # Get URL → page_id mapping
    page_map = get_podcast_page_ids()
    if not page_map:
        print("No podcast pages found in Notion.")
        return 0

    # Load all enriched podcasts
    podcasts = load_all_podcasts()
    enriched_podcasts = [p for p in podcasts if p.get("enriched") and p.get("enrichment")]

    if not enriched_podcasts:
        print("No enriched podcasts to sync.")
        return 0

    print(f"Syncing enrichment for {len(enriched_podcasts)} podcasts to Notion...")

    synced = 0
    for podcast in enriched_podcasts:
        # Match by website URL or RSS URL
        page_id = page_map.get(podcast.get("website"))
        if not page_id:
            continue

        success = update_podcast_enrichment(page_id, podcast["enrichment"])
        if success:
            synced += 1

    print(f"  Synced {synced} enrichment records to Notion.")
    return synced


if __name__ == "__main__":
    import sys
    profile = sys.argv[1] if len(sys.argv) > 1 else "profiles/andreas-duess.md"
    force = "--force" in sys.argv
    enrich_all(profile, force=force)
