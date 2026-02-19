"""
Notion Sync Module

Syncs podcast discoveries and pitches to Notion databases.
Includes Claude pre-scoring to filter noise before human review.

Databases:
  - Podcast Targets: discovered podcasts with relevance scores
  - Pitches: generated pitch emails for review/approval
"""

import os
import json
from datetime import datetime

import requests
import yaml
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
PODCAST_DB_ID = os.environ.get("NOTION_PODCAST_DB_ID", "")
PITCH_DB_ID = os.environ.get("NOTION_PITCH_DB_ID", "")

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


# ---------------------------------------------------------------------------
# Claude Pre-Scoring
# ---------------------------------------------------------------------------

SCORING_PROMPT = """You are evaluating whether podcasts are good fits for a guest to pitch.

## Guest Profile
{guest_profile}

## Podcasts to Score
{podcasts_json}

For each podcast, evaluate:
1. Is this actually a podcast (not an article, course, or unrelated page)?
2. Does it interview external guests?
3. Does the topic overlap with the guest's expertise?
4. Is the audience right (decision-makers, marketers, industry leaders — not students or hobbyists)?

Return a JSON array with one object per podcast. Each object:
- "name" (string): podcast name exactly as provided
- "relevance" (string): "High", "Medium", or "Low"
- "reason" (string): 1 sentence explaining the score
- "skip" (boolean): true if this isn't actually a podcast or is clearly irrelevant

Only return the JSON array, no other text."""


def score_podcasts(podcasts, profile_path, batch_size=20):
    """Score a list of podcast records for relevance using Claude."""
    with open(profile_path, "r") as f:
        content = f.read()
    # Extract body after frontmatter
    if content.startswith("---"):
        parts = content.split("---", 2)
        profile_body = parts[2].strip() if len(parts) >= 3 else content
    else:
        profile_body = content

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    all_scores = {}

    for i in range(0, len(podcasts), batch_size):
        batch = podcasts[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (len(podcasts) + batch_size - 1) // batch_size
        print(f"  Scoring batch {batch_num}/{total_batches} ({len(batch)} podcasts)...")

        # Prepare minimal podcast info for scoring
        podcast_summaries = []
        for p in batch:
            episodes = p.get("recent_episodes", [])
            ep_titles = [ep.get("title", "") for ep in episodes[:3]]
            podcast_summaries.append({
                "name": p.get("name", ""),
                "host": p.get("host", ""),
                "description": p.get("description", "")[:300],
                "episode_count": p.get("episode_count", 0),
                "recent_episode_titles": ep_titles,
                "categories": p.get("categories", []),
            })

        prompt = SCORING_PROMPT.format(
            guest_profile=profile_body,
            podcasts_json=json.dumps(podcast_summaries, indent=2),
        )

        response = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = response.content[0].text
        try:
            scores = json.loads(response_text)
        except json.JSONDecodeError:
            start = response_text.find("[")
            end = response_text.rfind("]") + 1
            if start >= 0 and end > start:
                scores = json.loads(response_text[start:end])
            else:
                print(f"    Warning: Could not parse scoring response")
                scores = []

        for score in scores:
            all_scores[score["name"]] = score

    return all_scores


# ---------------------------------------------------------------------------
# Notion: Podcast Targets Database
# ---------------------------------------------------------------------------

def get_existing_podcast_urls():
    """Get all website URLs already in the Podcast Targets database."""
    if not NOTION_TOKEN or not PODCAST_DB_ID:
        return set()

    existing = set()
    has_more = True
    start_cursor = None

    while has_more:
        body = {"page_size": 100}
        if start_cursor:
            body["start_cursor"] = start_cursor

        resp = requests.post(
            f"https://api.notion.com/v1/databases/{PODCAST_DB_ID}/query",
            headers=NOTION_HEADERS,
            json=body,
        )

        if resp.status_code != 200:
            print(f"  Warning: Notion query failed: {resp.status_code}")
            return existing

        data = resp.json()
        for page in data.get("results", []):
            url = page.get("properties", {}).get("Website", {}).get("url")
            if url:
                existing.add(url)
            rss = page.get("properties", {}).get("RSS Feed", {}).get("url")
            if rss:
                existing.add(rss)

        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")

    return existing


def push_podcast_to_notion(podcast, score_info=None):
    """Create a page in the Podcast Targets database."""
    if not NOTION_TOKEN or not PODCAST_DB_ID:
        return None

    relevance = "Medium"
    score_summary = ""
    if score_info:
        relevance = score_info.get("relevance", "Medium")
        score_summary = score_info.get("reason", "")

    # Build date property
    last_ep = podcast.get("last_episode_date", "")
    date_found = datetime.now().strftime("%Y-%m-%d")

    # Build categories/topics string
    categories = podcast.get("categories", [])
    topics_str = ", ".join(categories[:5]) if categories else ""

    properties = {
        "Podcast": {
            "title": [{"text": {"content": podcast.get("name", "")[:100]}}]
        },
        "Host": {
            "rich_text": [{"text": {"content": podcast.get("host", "")[:200]}}]
        },
        "Status": {
            "select": {"name": "Discovered"}
        },
        "Relevance": {
            "select": {"name": relevance}
        },
        "Source": {
            "select": {"name": "Podcast Index" if podcast.get("source") == "podcast_index" else "Exa"}
        },
        "Date Found": {
            "date": {"start": date_found}
        },
    }

    # Optional fields
    if podcast.get("episode_count"):
        properties["Episode Count"] = {"number": podcast["episode_count"]}
    if podcast.get("website"):
        properties["Website"] = {"url": podcast["website"]}
    if podcast.get("rss_url"):
        properties["RSS Feed"] = {"url": podcast["rss_url"]}
    if last_ep and len(last_ep) == 10:
        properties["Last Episode"] = {"date": {"start": last_ep}}
    if topics_str:
        properties["Topics"] = {"rich_text": [{"text": {"content": topics_str[:200]}}]}
    if score_summary:
        properties["Score Summary"] = {"rich_text": [{"text": {"content": score_summary[:2000]}}]}

    # Page body: description + recent episodes
    children = []
    desc = podcast.get("description", "")
    if desc:
        children.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": desc[:2000]}}]
            }
        })

    episodes = podcast.get("recent_episodes", [])
    if episodes:
        children.append({
            "object": "block",
            "type": "heading_3",
            "heading_3": {
                "rich_text": [{"type": "text", "text": {"content": "Recent Episodes"}}]
            }
        })
        for ep in episodes[:3]:
            ep_text = f"{ep.get('title', '')} ({ep.get('date', '')})"
            children.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{"type": "text", "text": {"content": ep_text[:200]}}]
                }
            })

    body = {
        "parent": {"database_id": PODCAST_DB_ID},
        "properties": properties,
    }
    if children:
        body["children"] = children

    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=NOTION_HEADERS,
        json=body,
    )

    if resp.status_code == 200:
        return resp.json().get("id")
    else:
        print(f"  Warning: Failed to create page for '{podcast.get('name', '')}': {resp.status_code} {resp.text[:200]}")
        return None


def sync_discoveries_to_notion(podcasts, scores, existing_urls=None):
    """Push scored podcast discoveries to Notion, skipping duplicates and low-relevance."""
    if existing_urls is None:
        existing_urls = get_existing_podcast_urls()

    created = 0
    skipped_dup = 0
    skipped_low = 0

    for podcast in podcasts:
        name = podcast.get("name", "")

        # Skip if already in Notion
        website = podcast.get("website", "")
        rss = podcast.get("rss_url", "")
        if website in existing_urls or (rss and rss in existing_urls):
            skipped_dup += 1
            continue

        # Get score
        score_info = scores.get(name, {})

        # Skip if Claude said to skip
        if score_info.get("skip"):
            skipped_low += 1
            continue

        # Skip Low relevance
        if score_info.get("relevance") == "Low":
            skipped_low += 1
            continue

        page_id = push_podcast_to_notion(podcast, score_info)
        if page_id:
            created += 1
            if website:
                existing_urls.add(website)
            if rss:
                existing_urls.add(rss)

    return created, skipped_dup, skipped_low


# ---------------------------------------------------------------------------
# Notion: Pitches Database
# ---------------------------------------------------------------------------

def push_pitch_to_notion(pitch_data):
    """Create a page in the Pitches database."""
    if not NOTION_TOKEN or not PITCH_DB_ID:
        return None

    fm = pitch_data.get("frontmatter", {})

    properties = {
        "Podcast": {
            "title": [{"text": {"content": fm.get("podcast", "")[:100]}}]
        },
        "Host": {
            "rich_text": [{"text": {"content": fm.get("host", "")[:200]}}]
        },
        "Status": {
            "select": {"name": "Draft"}
        },
        "Date Created": {
            "date": {"start": datetime.now().strftime("%Y-%m-%d")}
        },
    }

    if fm.get("contact_email"):
        properties["Contact Email"] = {"email": fm["contact_email"]}

    subject_lines = pitch_data.get("subject_lines", [])
    if subject_lines:
        properties["Subject Line"] = {
            "rich_text": [{"text": {"content": subject_lines[0][:200]}}]
        }

    # Pitch body as page content
    children = []
    pitch_email = pitch_data.get("pitch_email", "")
    if pitch_email:
        children.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": "Pitch Email"}}]
            }
        })
        # Split into paragraphs
        for para in pitch_email.split("\n\n"):
            if para.strip():
                children.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": para.strip()[:2000]}}]
                    }
                })

    if subject_lines:
        children.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": "Subject Line Options"}}]
            }
        })
        for sl in subject_lines:
            children.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{"type": "text", "text": {"content": sl[:200]}}]
                }
            })

    # Hook / analysis summary
    hook = pitch_data.get("hook", "")
    if hook:
        properties["Hook"] = {"rich_text": [{"text": {"content": hook[:2000]}}]}

    body = {
        "parent": {"database_id": PITCH_DB_ID},
        "properties": properties,
    }
    if children:
        body["children"] = children

    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=NOTION_HEADERS,
        json=body,
    )

    if resp.status_code == 200:
        return resp.json().get("id")
    else:
        print(f"  Warning: Failed to create pitch for '{fm.get('podcast', '')}': {resp.status_code} {resp.text[:200]}")
        return None


if __name__ == "__main__":
    print("Use via pitcher.py commands — this module provides sync functions.")
