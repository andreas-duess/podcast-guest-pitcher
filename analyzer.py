"""
Analyzer Module

Two-pass Claude analysis:
1. Analysis pass — identify themes, host interests, overlap with guest expertise
2. Pitch pass — write a personalized pitch email

Outputs markdown files to data/pitches/{slug}.md
"""

import os
import json
from datetime import datetime

import yaml
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
PITCHES_DIR = os.path.join(os.path.dirname(__file__), "data", "pitches")
os.makedirs(PITCHES_DIR, exist_ok=True)

TRANSCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "data", "transcripts")


def load_profile(profile_path):
    """Load guest profile frontmatter and body."""
    with open(profile_path, "r") as f:
        content = f.read()
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return yaml.safe_load(parts[1]), parts[2].strip()
    raise ValueError(f"Could not parse {profile_path}")


def load_transcripts(podcast_slug, max_chars=15000):
    """Load cached transcripts for a podcast, truncated to stay within context."""
    transcript_dir = os.path.join(TRANSCRIPTS_DIR, podcast_slug)
    if not os.path.isdir(transcript_dir):
        return []

    transcripts = []
    total_chars = 0
    for fname in sorted(os.listdir(transcript_dir)):
        if not fname.endswith(".md"):
            continue
        path = os.path.join(transcript_dir, fname)
        with open(path, "r") as f:
            text = f.read()

        # Truncate individual transcript if needed
        remaining = max_chars - total_chars
        if remaining <= 0:
            break
        if len(text) > remaining:
            text = text[:remaining] + "\n\n[...truncated...]"

        transcripts.append({"filename": fname, "text": text})
        total_chars += len(text)

    return transcripts


# ---------------------------------------------------------------------------
# Pass 1: Analysis
# ---------------------------------------------------------------------------

ANALYSIS_PROMPT = """You are a podcast guest booking strategist. Analyze this podcast to find the best angle for pitching the guest below.

## Guest Profile
{guest_profile}

## Podcast Information
Name: {podcast_name}
Host: {host}
Description: {description}

## Recent Episode Transcripts
{transcripts}

## Your Task

Analyze the podcast and identify:

1. **Recurring themes** across the episodes (3-5 key themes)
2. **Host's interests and style** — What does the host care about? Are they conversational or interview-driven? Do they like provocative takes or practical advice?
3. **Audience profile** — Who listens to this show? What level of expertise?
4. **Overlap with guest** — Where does the guest's expertise directly map to what this podcast covers?
5. **Best hook** — The single strongest angle for pitching this guest to this host. Be specific — reference an episode topic or theme the host clearly cares about.
6. **Episode the guest should reference** — Which specific episode (by title) would make the best reference point in the pitch?

Return your analysis as a structured brief. Be specific and actionable — generic observations are useless."""


def analyze_podcast(podcast_record, profile_path):
    """Run analysis pass on a podcast."""
    profile_data, profile_body = load_profile(profile_path)
    slug = podcast_record.get("slug", "unknown")

    transcripts = load_transcripts(slug)
    transcript_text = "\n\n---\n\n".join(
        f"### {t['filename']}\n{t['text']}" for t in transcripts
    ) if transcripts else "(No transcripts available — use podcast description and episode titles only)"

    # Build episode context from record
    episodes_context = ""
    for ep in podcast_record.get("recent_episodes", []):
        episodes_context += f"- {ep.get('title', '')} ({ep.get('date', '')})\n"

    description = podcast_record.get("description", "")
    if episodes_context:
        description += f"\n\nRecent episodes:\n{episodes_context}"

    prompt = ANALYSIS_PROMPT.format(
        guest_profile=profile_body,
        podcast_name=podcast_record.get("name", ""),
        host=podcast_record.get("host", "Unknown"),
        description=description,
        transcripts=transcript_text,
    )

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    return response.content[0].text


# ---------------------------------------------------------------------------
# Pass 2: Pitch Writing
# ---------------------------------------------------------------------------

PITCH_PROMPT = """You are writing a podcast guest pitch email. The email is FROM the guest TO the podcast host.

## Guest Profile
{guest_profile}

## Podcast Analysis
{analysis}

## Podcast
Name: {podcast_name}
Host: {host}

## Rules
- Keep it under 150 words
- Reference a specific recent episode or topic the host covered
- Connect the guest's expertise to what the host's audience cares about
- Tone: warm, specific, not salesy — like one professional reaching out to another
- Don't use "I'd love to" or "I'd be honored" — be direct about the value
- Include a concrete topic or angle the guest could discuss
- End with a simple ask (15-min call or direct booking link)
- Sign off as the guest (first name only)
- Do NOT include a subject line — that will be added separately

Write ONLY the email body. No preamble or explanation."""


SUBJECT_PROMPT = """Write 3 email subject line options for a podcast guest pitch.

Guest: {guest_name} ({guest_title}, {guest_company})
Podcast: {podcast_name}
Hook: {hook}

Rules:
- Under 50 characters each
- Personal, not promotional
- Reference the podcast or a specific episode topic
- No spam words (free, guaranteed, exclusive)

Return ONLY the 3 subject lines, one per line, numbered 1-3."""


def write_pitch(podcast_record, profile_path, analysis):
    """Write a pitch email based on the analysis."""
    profile_data, profile_body = load_profile(profile_path)

    prompt = PITCH_PROMPT.format(
        guest_profile=profile_body,
        analysis=analysis,
        podcast_name=podcast_record.get("name", ""),
        host=podcast_record.get("host", "Unknown"),
    )

    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    # Generate pitch body
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    pitch_body = response.content[0].text

    # Generate subject lines
    subject_prompt = SUBJECT_PROMPT.format(
        guest_name=profile_data.get("name", ""),
        guest_title=profile_data.get("title", ""),
        guest_company=profile_data.get("company", ""),
        podcast_name=podcast_record.get("name", ""),
        hook=analysis[:300],
    )

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=200,
        messages=[{"role": "user", "content": subject_prompt}],
    )
    subject_lines = response.content[0].text

    return pitch_body, subject_lines


# ---------------------------------------------------------------------------
# Save Pitch
# ---------------------------------------------------------------------------

def save_pitch(podcast_record, analysis, pitch_body, subject_lines, profile_data):
    """Save pitch as a markdown file."""
    slug = podcast_record.get("slug", "unknown")
    filepath = os.path.join(PITCHES_DIR, f"{slug}.md")

    content = f"""---
podcast: {podcast_record.get('name', '')}
host: {podcast_record.get('host', '')}
guest: {profile_data.get('name', '')}
status: draft
date: {datetime.now().strftime('%Y-%m-%d')}
contact_email: {podcast_record.get('contact_email', '')}
---

# Pitch: {podcast_record.get('name', '')}

## Subject Line Options
{subject_lines}

## Pitch Email
{pitch_body}

## Analysis Brief
{analysis}

## Podcast Info
- **Website:** {podcast_record.get('website', '')}
- **RSS:** {podcast_record.get('rss_url', '')}
- **Host:** {podcast_record.get('host', '')}
- **Last Episode:** {podcast_record.get('last_episode_date', '')}
"""

    with open(filepath, "w") as f:
        f.write(content)

    return filepath


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate_pitch(podcast_record, profile_path):
    """Full two-pass pitch generation for a single podcast."""
    profile_data, _ = load_profile(profile_path)
    name = podcast_record.get("name", "Unknown")
    print(f"\n  Analyzing: {name}...")

    # Pass 1: Analysis
    analysis = analyze_podcast(podcast_record, profile_path)
    print(f"    Analysis complete ({len(analysis)} chars)")

    # Pass 2: Pitch
    pitch_body, subject_lines = write_pitch(podcast_record, profile_path, analysis)
    print(f"    Pitch written ({len(pitch_body)} chars)")

    # Save
    path = save_pitch(podcast_record, analysis, pitch_body, subject_lines, profile_data)
    print(f"    Saved to: {path}")

    return path


def generate_all_pitches(podcasts, profile_path):
    """Generate pitches for all pursued podcasts."""
    paths = []
    for record in podcasts:
        if not record.get("pursue"):
            continue
        path = generate_pitch(record, profile_path)
        paths.append(path)
    return paths


if __name__ == "__main__":
    import sys
    from discovery import load_pursued_podcasts

    profile = sys.argv[1] if len(sys.argv) > 1 else "profiles/andreas-duess.md"
    pursued = load_pursued_podcasts()
    print(f"Generating pitches for {len(pursued)} pursued podcasts...")
    generate_all_pitches(pursued, profile)
