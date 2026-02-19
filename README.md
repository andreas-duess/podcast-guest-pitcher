# Podcast Guest Pitcher

A self-hosted pipeline for discovering relevant podcasts, analyzing episodes, generating personalized guest pitches, and managing outreach. Built to replace expensive SaaS tools like PodPitch.

**This is a work in progress.** We're still iterating on the discovery sources, transcript quality, pitch generation, and outreach workflow. Expect rough edges.

## What it does

1. **Discovers** podcasts via Podcast Index API and Exa semantic search
2. **Fetches transcripts** from YouTube (free) or RSS descriptions (fallback)
3. **Analyzes episodes** with Claude to find personalized hooks
4. **Generates pitch emails** tailored to each show's style and topics
5. **Manages contacts** via RSS parsing, web scraping, and manual enrichment
6. **Sends outreach** through Gmail or Lemlist
7. **Syncs to Notion** for tracking pipeline status

## Setup

### 1. Clone and install

```bash
git clone https://github.com/andreas-duess/podcast-guest-pitcher.git
cd podcast-guest-pitcher
pip install -r requirements.txt
```

### 2. Get API keys

| Key | Where to get it | Required? |
|-----|----------------|-----------|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) | Yes |
| `PODCAST_INDEX_KEY` | [podcastindex.org](https://podcastindex.org) (free) | Yes |
| `PODCAST_INDEX_SECRET` | Same as above | Yes |
| `EXA_API_KEY` | [exa.ai](https://exa.ai) | Optional (semantic discovery) |
| `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com) | Optional (Whisper transcription) |
| `APOLLO_API_KEY` | [apollo.io](https://apollo.io) (free tier) | Optional (contact enrichment) |
| `NOTION_TOKEN` | [developers.notion.so](https://developers.notion.so) | Optional (pipeline tracking) |
| `NOTION_PODCAST_DB_ID` | Your Notion database ID | Optional |
| `NOTION_PITCH_DB_ID` | Your Notion database ID | Optional |
| `LEMLIST_API_KEY` | [lemlist.com](https://lemlist.com) | Optional (email outreach) |

### 3. Create your .env file

```bash
cp .env.example .env  # Then fill in your keys
```

Or create `.env` manually:

```
ANTHROPIC_API_KEY=sk-ant-...
PODCAST_INDEX_KEY=...
PODCAST_INDEX_SECRET=...
```

### 4. Create your guest profile

Copy and edit the example profile:

```bash
cp profiles/andreas-duess.md profiles/your-name.md
```

See "Guest profile format" below for what to include.

## Usage

```bash
# Discover new podcasts
python pitcher.py discover --profile profiles/your-name.md

# Review discovered podcasts and mark targets
python pitcher.py review

# Fetch episode transcripts for marked targets
python pitcher.py transcribe

# Generate pitches for all targets with transcripts
python pitcher.py pitch

# Find host contact emails
python pitcher.py contacts

# Check pipeline status
python pitcher.py status

# Full pipeline (discover -> review -> transcribe -> pitch)
python pitcher.py run --profile profiles/your-name.md
```

## Workflow

The pipeline has human review gates built in. Nothing gets sent without your approval.

```
Discover podcasts
       |
       v
  Review & mark targets  <-- you decide which podcasts to pursue
       |
       v
  Fetch transcripts       (automatic)
       |
       v
  Generate pitches        (automatic)
       |
       v
  Review pitches          <-- you edit/approve before sending
       |
       v
  Send outreach           (Gmail drafts or Lemlist)
```

Each podcast moves through statuses: `discovered` -> `pursuing` -> `pitched` -> `replied` / `booked` / `declined`

## Guest profile format

Profiles live in `profiles/` as markdown files with YAML frontmatter. The profile tells the analyzer what topics you speak about, your credentials, and what kinds of podcasts to target.

```yaml
---
name: Your Name
title: Your Title
company: Your Company
website: https://yoursite.com

topics:
  - topic you speak about
  - another topic
  - a third topic

credibility:
  - your key credential
  - another credential

current_promotion:
  - what you'd plug on the show

search_queries:
  - specific podcast search terms
  - another search term

ideal_podcast_types:
  - type of show you want to be on
  - another type

anti_targets:
  - shows to avoid
  - another filter
---

# Your Name — Podcast Guest Profile

## The Story in 30 Seconds
A short bio the analyzer uses to understand your angle.

## What Makes You Different
Your unique positioning.

## Best Episode Topics
1. **Topic name** — one-line description
2. **Another topic** — one-line description
```

The `search_queries` field drives podcast discovery. The `topics` and `credibility` fields help Claude find relevant hooks. The `anti_targets` field filters out bad matches.

## Project structure

```
podcast-guest-pitcher/
├── pitcher.py          # CLI orchestrator — ties all modules together
├── discovery.py        # Podcast search via Podcast Index + Exa
├── transcripts.py      # Episode transcript fetching (YouTube -> Whisper -> RSS)
├── analyzer.py         # Claude analysis + pitch generation
├── contacts.py         # Host email finding (RSS -> website -> Apollo)
├── outreach.py         # Lemlist campaign creation + lead injection
├── notion_sync.py      # Notion database sync for pipeline tracking
├── profiles/           # Guest profiles (one per person)
├── data/
│   ├── podcasts/       # Discovered podcast records (JSON, one per show)
│   ├── transcripts/    # Cached episode transcripts (gitignored)
│   └── pitches/        # Generated pitch emails (markdown)
├── requirements.txt
└── .env                # API keys (gitignored)
```

### What each module does

| File | Purpose |
|------|---------|
| `pitcher.py` | CLI entry point. Subcommands: discover, review, transcribe, pitch, contacts, send, status, run |
| `discovery.py` | Searches Podcast Index by keyword, expands topics into queries, saves JSON records to `data/podcasts/` |
| `transcripts.py` | Three-strategy waterfall: YouTube transcript (free) -> Whisper audio transcription (paid) -> RSS description fallback (free). Caches to `data/transcripts/` |
| `analyzer.py` | Two-pass Claude Sonnet analysis. Pass 1: identify themes, audience, and overlap with guest. Pass 2: write a personalized pitch email referencing specific episodes |
| `contacts.py` | Finds host emails by parsing RSS `<itunes:owner>` tags, scraping podcast websites, and optionally querying Apollo |
| `outreach.py` | Loads approved pitches and creates Lemlist campaigns with custom variables |
| `notion_sync.py` | Pushes podcast targets and pitches to Notion databases for visual pipeline tracking |

### Data files

- **`data/podcasts/{slug}.json`** — one file per discovered podcast. Contains metadata, episodes, status, contact info, relevance score. Edit `"pursue": true` to mark a target.
- **`data/transcripts/{slug}/`** — cached transcripts (gitignored, regenerate with `pitcher.py transcribe`)
- **`data/pitches/{slug}.md`** — generated pitch with YAML frontmatter and sections: Subject Lines, Pitch Email, Analysis Brief, Podcast Info

## Cost

About $0.05-0.10 per pitch using RSS descriptions or YouTube transcripts. If using Whisper audio transcription (fallback), add ~$0.14/episode (~$0.42 per podcast for 3 episodes). Discovery is free.

## Current limitations

- **YouTube rate limiting** — YouTube blocks IPs after ~30-50 transcript requests. The tool detects this and falls back to RSS descriptions, but transcript quality drops significantly.
- **RSS descriptions are shallow** — when YouTube is blocked and Whisper isn't configured, pitches are based on ~500 characters of episode summary per episode instead of full transcripts. Hooks are less specific.
- **No Whisper fallback without OpenAI key** — set `OPENAI_API_KEY` in `.env` to enable audio transcription. Costs ~$0.14/episode.
- **Contact finding is imperfect** — RSS feeds often don't include host emails. Website scraping catches some, but many podcasts require manual research.
- **Discovery noise** — Podcast Index keyword search returns some irrelevant results (non-English shows, dead feeds, tangentially related topics). The review step filters these out.
- **No follow-up sequences yet** — currently single-touch outreach only

## Roadmap

- [ ] Add Listen Notes and Podchaser as discovery sources
- [ ] Whisper transcription with smarter audio chunking (first 30 min only for long episodes)
- [ ] Follow-up email sequences (Lemlist or Gmail)
- [ ] Better contact discovery (LinkedIn scraping, podcast booking pages)
- [ ] Multi-client support for agency use (multiple guest profiles, separate pipelines)
- [ ] Notion two-way sync (update status in Notion, reflect back to local JSON)
- [ ] GitHub Actions for scheduled weekly discovery runs
- [ ] Response tracking (connect Gmail/Lemlist replies back to pipeline status)

## Stack

- Python 3
- Claude Sonnet (analysis + pitch writing)
- Podcast Index API (discovery)
- YouTube Transcript API (free transcripts)
- OpenAI Whisper (paid audio transcription, optional)
- Notion API (pipeline tracking, optional)
- Gmail / Lemlist (outreach)
- Apollo (contact enrichment, optional)
- Exa (semantic search, optional)
