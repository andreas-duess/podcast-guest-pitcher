"""
Podcast Discovery Module

Two-pronged search:
1. Podcast Index API (free) — keyword search across podcast metadata
2. Exa.ai semantic search — finds niche/relevant shows by meaning

Outputs JSON records to data/podcasts/{slug}.json
"""

import os
import json
import re
import time
import hashlib
import hmac
from datetime import datetime, timedelta

import requests
import yaml
from dotenv import load_dotenv

load_dotenv()

EXA_API_KEY = os.environ.get("EXA_API_KEY", "")
PODCAST_INDEX_KEY = os.environ.get("PODCAST_INDEX_KEY", "")
PODCAST_INDEX_SECRET = os.environ.get("PODCAST_INDEX_SECRET", "")

PODCASTS_DIR = os.path.join(os.path.dirname(__file__), "data", "podcasts")
os.makedirs(PODCASTS_DIR, exist_ok=True)


def slugify(text):
    """Convert text to a filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text)
    return text[:80]


def load_profile(profile_path):
    """Load a guest profile markdown file and extract frontmatter."""
    with open(profile_path, "r") as f:
        content = f.read()

    # Extract YAML frontmatter
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            frontmatter = yaml.safe_load(parts[1])
            body = parts[2].strip()
            return frontmatter, body

    raise ValueError(f"Could not parse frontmatter from {profile_path}")


# ---------------------------------------------------------------------------
# Podcast Index API
# ---------------------------------------------------------------------------

def _podcast_index_headers():
    """Generate auth headers for Podcast Index API."""
    epoch = int(time.time())
    data = PODCAST_INDEX_KEY + PODCAST_INDEX_SECRET + str(epoch)
    sha1_hash = hashlib.sha1(data.encode("utf-8")).hexdigest()

    return {
        "User-Agent": "PodcastGuestPitcher/1.0",
        "X-Auth-Key": PODCAST_INDEX_KEY,
        "X-Auth-Date": str(epoch),
        "Authorization": sha1_hash,
    }


def search_podcast_index(query, max_results=10):
    """Search Podcast Index for podcasts matching a keyword query."""
    if not PODCAST_INDEX_KEY or not PODCAST_INDEX_SECRET:
        return []

    try:
        resp = requests.get(
            "https://api.podcastindex.org/api/1.0/search/byterm",
            headers=_podcast_index_headers(),
            params={"q": query, "max": max_results, "clean": 1},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("feeds", [])
    except requests.exceptions.RequestException as e:
        print(f"  Warning: Podcast Index search failed: {e}")
        return []


def get_episodes_podcast_index(feed_id, max_results=5):
    """Get recent episodes for a podcast from Podcast Index."""
    if not PODCAST_INDEX_KEY or not PODCAST_INDEX_SECRET:
        return []

    try:
        resp = requests.get(
            "https://api.podcastindex.org/api/1.0/episodes/byfeedid",
            headers=_podcast_index_headers(),
            params={"id": feed_id, "max": max_results},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", [])
    except requests.exceptions.RequestException as e:
        print(f"  Warning: Podcast Index episode fetch failed: {e}")
        return []


def _expand_topics_to_keywords(topics):
    """Break long topic phrases into shorter keyword queries for Podcast Index.

    Podcast Index does keyword matching, not semantic — so 'agricultural marketing
    and commodity boards' returns nothing, but 'agricultural marketing' works.
    """
    stopwords = {"and", "in", "for", "with", "to", "a", "the", "of", "on", "is", "an"}
    keywords = set()
    for topic in topics:
        # Add the full topic
        keywords.add(topic)
        # Split on common conjunctions and prepositions
        for separator in [" and ", " in ", " for ", " with ", " to "]:
            if separator in topic:
                parts = topic.split(separator)
                for part in parts:
                    part = part.strip()
                    if len(part) > 8:
                        keywords.add(part)
        # Also try 2-word combinations, skipping stopword-only pairs
        words = topic.split()
        if len(words) >= 2:
            for i in range(len(words) - 1):
                w1, w2 = words[i].lower(), words[i+1].lower()
                if w1 in stopwords or w2 in stopwords:
                    continue
                bigram = f"{words[i]} {words[i+1]}"
                keywords.add(bigram)
    return sorted(keywords)


def discover_via_podcast_index(topics, max_per_topic=10):
    """Search Podcast Index for each topic keyword."""
    results = []
    seen_ids = set()

    keywords = _expand_topics_to_keywords(topics)

    for keyword in keywords:
        print(f"  Podcast Index: searching '{keyword}'...")
        feeds = search_podcast_index(keyword, max_results=max_per_topic)

        for feed in feeds:
            feed_id = str(feed.get("id", ""))
            if feed_id in seen_ids:
                continue
            seen_ids.add(feed_id)

            # Filter: must have recent activity
            last_update = feed.get("newestItemPublishTime", 0)
            if last_update:
                last_date = datetime.fromtimestamp(last_update)
                if datetime.now() - last_date > timedelta(days=90):
                    continue

            # Get recent episodes for context
            episodes = get_episodes_podcast_index(feed_id, max_results=3)
            episode_list = []
            for ep in episodes:
                episode_list.append({
                    "title": ep.get("title", ""),
                    "date": datetime.fromtimestamp(ep.get("datePublished", 0)).strftime("%Y-%m-%d") if ep.get("datePublished") else "",
                    "duration": ep.get("duration", 0),
                    "audio_url": ep.get("enclosureUrl", ""),
                    "description": (ep.get("description", "") or "")[:500],
                })

            results.append({
                "source": "podcast_index",
                "name": feed.get("title", ""),
                "host": feed.get("author", feed.get("ownerName", "")),
                "description": (feed.get("description", "") or "")[:500],
                "rss_url": feed.get("url", ""),
                "website": feed.get("link", ""),
                "language": feed.get("language", "en"),
                "categories": list((feed.get("categories") or {}).values()),
                "feed_id": feed_id,
                "last_episode_date": datetime.fromtimestamp(last_update).strftime("%Y-%m-%d") if last_update else "",
                "episode_count": feed.get("episodeCount", 0),
                "recent_episodes": episode_list,
            })

        time.sleep(0.5)

    return results


# ---------------------------------------------------------------------------
# Exa Semantic Search
# ---------------------------------------------------------------------------

def search_exa(query, num_results=10):
    """Run a semantic search on Exa for podcast-related pages."""
    if not EXA_API_KEY:
        return []

    try:
        resp = requests.post(
            "https://api.exa.ai/search",
            headers={
                "x-api-key": EXA_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "query": query,
                "type": "neural",
                "numResults": num_results,
                "contents": {"text": {"maxCharacters": 1000}},
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("results", [])
    except requests.exceptions.RequestException as e:
        print(f"  Warning: Exa search failed: {e}")
        return []


def discover_via_exa(topics, max_per_topic=10):
    """Use Exa semantic search to find podcasts by topic."""
    results = []
    seen_urls = set()

    queries = []
    for topic in topics:
        queries.append(f"podcast about {topic} that interviews guests")
        queries.append(f"best podcasts for {topic} industry leaders and experts")

    for query in queries:
        print(f"  Exa: searching '{query[:60]}...'")
        hits = search_exa(query, num_results=max_per_topic)

        for hit in hits:
            url = hit.get("url", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)

            title = hit.get("title", "")
            text = hit.get("text", "")

            results.append({
                "source": "exa",
                "name": title,
                "host": "",
                "description": text[:500],
                "rss_url": "",
                "website": url,
                "language": "en",
                "categories": [],
                "last_episode_date": "",
                "episode_count": 0,
                "recent_episodes": [],
            })

        time.sleep(0.5)

    return results


# ---------------------------------------------------------------------------
# Save / Load Podcast Records
# ---------------------------------------------------------------------------

def save_podcast(record):
    """Save a podcast record to data/podcasts/{slug}.json."""
    slug = slugify(record["name"])
    if not slug:
        slug = slugify(record.get("website", "unknown"))

    filepath = os.path.join(PODCASTS_DIR, f"{slug}.json")

    # If file exists, merge (don't overwrite status/notes)
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            existing = json.load(f)
        # Preserve user-set fields
        for key in ("status", "pursue", "notes", "contact_email", "contact_name"):
            if key in existing:
                record[key] = existing[key]

    # Set defaults for new records
    record.setdefault("status", "discovered")
    record.setdefault("pursue", False)
    record.setdefault("notes", "")
    record.setdefault("discovered_date", datetime.now().strftime("%Y-%m-%d"))
    record["slug"] = slug

    with open(filepath, "w") as f:
        json.dump(record, f, indent=2)

    return filepath


def load_all_podcasts():
    """Load all podcast records from data/podcasts/."""
    records = []
    for fname in sorted(os.listdir(PODCASTS_DIR)):
        if not fname.endswith(".json"):
            continue
        filepath = os.path.join(PODCASTS_DIR, fname)
        with open(filepath, "r") as f:
            records.append(json.load(f))
    return records


def load_pursued_podcasts():
    """Load only podcasts marked for pursuit."""
    return [r for r in load_all_podcasts() if r.get("pursue")]


# ---------------------------------------------------------------------------
# Main Discovery Flow
# ---------------------------------------------------------------------------

def discover(profile_path, use_exa=False):
    """Run full discovery for a guest profile. Exa is opt-in via --exa flag."""
    profile, _ = load_profile(profile_path)
    topics = profile.get("topics", [])
    print(f"Discovering podcasts for: {profile['name']}")
    print(f"Topics: {', '.join(topics)}\n")

    all_results = []

    # Podcast Index
    if PODCAST_INDEX_KEY:
        print("Source 1: Podcast Index API")
        pi_results = discover_via_podcast_index(topics)
        print(f"  Found {len(pi_results)} podcasts\n")
        all_results.extend(pi_results)
    else:
        print("Source 1: Podcast Index API — skipped (no API key)\n")

    # Exa (opt-in — noisier but finds niche shows)
    if use_exa and EXA_API_KEY:
        print("Source 2: Exa semantic search")
        exa_results = discover_via_exa(topics)
        print(f"  Found {len(exa_results)} results\n")
        all_results.extend(exa_results)
    else:
        print("Source 2: Exa — skipped\n")

    # Save
    saved = 0
    for record in all_results:
        save_podcast(record)
        saved += 1

    print(f"Saved {saved} podcast records to {PODCASTS_DIR}/")
    return all_results


if __name__ == "__main__":
    import sys
    profile = sys.argv[1] if len(sys.argv) > 1 else "profiles/andreas-duess.md"
    discover(profile)
