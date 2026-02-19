"""
Transcript Module

Fetches episode transcripts using two strategies:
1. YouTube (free) — search for the episode on YouTube, pull transcript
2. Whisper fallback — download audio from RSS, transcribe with OpenAI

Caches transcripts to data/transcripts/{podcast-slug}/
"""

import os
import re
import json
import time
import random
import tempfile

import requests
import feedparser
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
TRANSCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "data", "transcripts")
os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)

# Max audio duration to transcribe (30 min = save cost on long episodes)
MAX_AUDIO_SECONDS = 1800

# Rate limiting for YouTube to avoid IP bans
_youtube_blocked = False
_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
]


def _transcript_path(podcast_slug, episode_title):
    """Get the file path for a cached transcript."""
    ep_slug = re.sub(r'[^\w\s-]', '', episode_title.lower())
    ep_slug = re.sub(r'[\s_]+', '-', ep_slug)[:60]
    podcast_dir = os.path.join(TRANSCRIPTS_DIR, podcast_slug)
    os.makedirs(podcast_dir, exist_ok=True)
    return os.path.join(podcast_dir, f"{ep_slug}.md")


def _is_cached(podcast_slug, episode_title):
    """Check if a transcript is already cached."""
    path = _transcript_path(podcast_slug, episode_title)
    return os.path.exists(path) and os.path.getsize(path) > 100


def _save_transcript(podcast_slug, episode_title, text, source="unknown"):
    """Save transcript as markdown."""
    path = _transcript_path(podcast_slug, episode_title)
    content = f"---\nepisode: {episode_title}\nsource: {source}\n---\n\n{text}"
    with open(path, "w") as f:
        f.write(content)
    return path


# ---------------------------------------------------------------------------
# Strategy 1: YouTube Transcript (free)
# ---------------------------------------------------------------------------

def search_youtube(query, max_results=3):
    """Search YouTube for a video matching the query. Uses scraping approach."""
    global _youtube_blocked
    if _youtube_blocked:
        return []

    try:
        time.sleep(random.uniform(3, 6))  # Rate limit between searches
        search_url = "https://www.youtube.com/results"
        resp = requests.get(
            search_url,
            params={"search_query": query},
            headers={"User-Agent": random.choice(_USER_AGENTS)},
            timeout=15,
        )
        resp.raise_for_status()

        # Extract video IDs from the page
        video_ids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', resp.text)
        # Deduplicate while preserving order
        seen = set()
        unique_ids = []
        for vid in video_ids:
            if vid not in seen:
                seen.add(vid)
                unique_ids.append(vid)
            if len(unique_ids) >= max_results:
                break

        return unique_ids
    except Exception as e:
        print(f"    YouTube search failed: {e}")
        return []


def get_youtube_transcript(video_id):
    """Fetch transcript from YouTube using youtube-transcript-api."""
    global _youtube_blocked
    if _youtube_blocked:
        return None

    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        time.sleep(random.uniform(2, 4))  # Rate limit between transcript fetches
        ytt = YouTubeTranscriptApi()
        transcript = ytt.fetch(video_id, languages=['en'])
        lines = [entry.text for entry in transcript]
        return " ".join(lines)

    except Exception as e:
        err_str = str(e)
        if "blocking" in err_str.lower() or "IP" in err_str or "RequestBlocked" in err_str:
            print(f"    YouTube IP blocked — switching to Whisper-only mode")
            _youtube_blocked = True
        else:
            print(f"    YouTube transcript failed for {video_id}: {e}")
        return None


def fetch_transcript_youtube(podcast_name, episode_title):
    """Try to find and fetch a YouTube transcript for an episode."""
    if _youtube_blocked:
        return None, None

    query = f"{podcast_name} {episode_title}"
    video_ids = search_youtube(query)

    for vid in video_ids:
        text = get_youtube_transcript(vid)
        if text and len(text) > 200:
            return text, vid

    return None, None


# ---------------------------------------------------------------------------
# Strategy 2: Whisper Fallback
# ---------------------------------------------------------------------------

def download_audio(audio_url, max_bytes=50_000_000):
    """Download audio file to a temp file. Limit to ~50MB."""
    try:
        resp = requests.get(
            audio_url,
            stream=True,
            timeout=30,
            headers={"User-Agent": "PodcastGuestPitcher/1.0"},
        )
        resp.raise_for_status()

        suffix = ".mp3"
        if "audio/mp4" in resp.headers.get("content-type", ""):
            suffix = ".m4a"

        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        downloaded = 0
        for chunk in resp.iter_content(chunk_size=8192):
            tmp.write(chunk)
            downloaded += len(chunk)
            if downloaded > max_bytes:
                break
        tmp.close()
        return tmp.name
    except Exception as e:
        print(f"    Audio download failed: {e}")
        return None


def transcribe_with_whisper(audio_path):
    """Transcribe audio using OpenAI Whisper API."""
    if not OPENAI_API_KEY:
        print("    Whisper: no OPENAI_API_KEY configured")
        return None

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)

        with open(audio_path, "rb") as f:
            response = client.audio.transcriptions.create(
                model="gpt-4o-mini-transcribe",
                file=f,
            )
        return response.text
    except Exception as e:
        print(f"    Whisper transcription failed: {e}")
        return None
    finally:
        # Clean up temp file
        try:
            os.unlink(audio_path)
        except OSError:
            pass


def fetch_transcript_whisper(audio_url):
    """Download audio and transcribe with Whisper."""
    audio_path = download_audio(audio_url)
    if not audio_path:
        return None

    text = transcribe_with_whisper(audio_path)
    return text


# ---------------------------------------------------------------------------
# Get Episodes from RSS
# ---------------------------------------------------------------------------

def _get_episode_description(entry):
    """Extract the best available description from an RSS entry."""
    # Try content:encoded first (usually the richest), then summary
    if entry.get("content"):
        desc = entry["content"][0].get("value", "")
        if desc:
            # Strip HTML tags for cleaner text
            desc = re.sub(r'<[^>]+>', ' ', desc)
            desc = re.sub(r'\s+', ' ', desc).strip()
            return desc[:2000]
    summary = entry.get("summary", "") or ""
    summary = re.sub(r'<[^>]+>', ' ', summary)
    summary = re.sub(r'\s+', ' ', summary).strip()
    return summary[:2000]


def get_episodes_from_rss(rss_url, max_episodes=3):
    """Parse RSS feed and return recent episodes with audio URLs."""
    try:
        feed = feedparser.parse(rss_url)
        episodes = []

        for entry in feed.entries[:max_episodes]:
            audio_url = ""
            for link in entry.get("links", []):
                if link.get("type", "").startswith("audio/") or link.get("href", "").endswith((".mp3", ".m4a")):
                    audio_url = link["href"]
                    break
            if not audio_url:
                for enc in entry.get("enclosures", []):
                    if enc.get("type", "").startswith("audio/"):
                        audio_url = enc.get("href", "")
                        break

            episodes.append({
                "title": entry.get("title", "Untitled"),
                "date": entry.get("published", ""),
                "audio_url": audio_url,
                "description": _get_episode_description(entry),
            })

        return episodes
    except Exception as e:
        print(f"  RSS parse failed for {rss_url}: {e}")
        return []


# ---------------------------------------------------------------------------
# Main Transcript Flow
# ---------------------------------------------------------------------------

def fetch_transcripts(podcast_record, max_episodes=3):
    """Fetch transcripts for a podcast. Returns list of transcript file paths."""
    slug = podcast_record.get("slug", "unknown")
    name = podcast_record.get("name", "")
    rss_url = podcast_record.get("rss_url", "")

    print(f"\n  Fetching transcripts for: {name}")

    # Get episodes — prefer from record, fall back to RSS
    episodes = podcast_record.get("recent_episodes", [])
    if not episodes and rss_url:
        episodes = get_episodes_from_rss(rss_url, max_episodes)

    if not episodes:
        print(f"    No episodes found")
        return []

    paths = []
    for ep in episodes[:max_episodes]:
        title = ep.get("title", "Untitled")

        # Check cache
        if _is_cached(slug, title):
            print(f"    Cached: {title[:50]}")
            paths.append(_transcript_path(slug, title))
            continue

        print(f"    Fetching: {title[:50]}...")

        # Strategy 1: YouTube
        text, vid = fetch_transcript_youtube(name, title)
        if text:
            path = _save_transcript(slug, title, text, source=f"youtube:{vid}")
            print(f"      -> YouTube transcript ({len(text)} chars)")
            paths.append(path)
            continue

        # Strategy 2: Whisper
        audio_url = ep.get("audio_url", "")
        if audio_url and OPENAI_API_KEY:
            text = fetch_transcript_whisper(audio_url)
            if text:
                path = _save_transcript(slug, title, text, source="whisper")
                print(f"      -> Whisper transcript ({len(text)} chars)")
                paths.append(path)
                continue

        # Strategy 3: Use episode description from RSS as minimal context
        description = ep.get("description", "")
        if description and len(description) > 50:
            text = f"[Episode description — no full transcript available]\n\nTitle: {title}\n\n{description}"
            path = _save_transcript(slug, title, text, source="rss-description")
            print(f"      -> RSS description fallback ({len(description)} chars)")
            paths.append(path)
            continue

        print(f"      -> No transcript available")
        time.sleep(0.5)

    return paths


def transcribe_all_pursued(podcasts):
    """Fetch transcripts for all pursued podcasts."""
    total_paths = []
    for record in podcasts:
        if not record.get("pursue"):
            continue
        paths = fetch_transcripts(record)
        total_paths.extend(paths)
    return total_paths


if __name__ == "__main__":
    from discovery import load_pursued_podcasts
    pursued = load_pursued_podcasts()
    print(f"Transcribing {len(pursued)} pursued podcasts...")
    transcribe_all_pursued(pursued)
