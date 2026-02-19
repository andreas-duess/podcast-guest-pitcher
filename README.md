# Podcast Guest Pitcher

A self-hosted pipeline for discovering relevant podcasts, analyzing episodes, generating personalized guest pitches, and managing outreach. Built to replace expensive SaaS tools like PodPitch.

**This is a work in progress.** We're still iterating on the discovery sources, transcript quality, pitch generation, and outreach workflow. Expect rough edges.

## What it does

1. **Discovers** podcasts via Podcast Index API and Exa semantic search
2. **Fetches transcripts** from YouTube (free) or RSS descriptions (fallback)
3. **Analyzes episodes** with Claude to find personalized hooks
4. **Generates pitch emails** tailored to each show's style and topics
5. **Manages contacts** via RSS parsing, web scraping, and manual enrichment
6. **Sends outreach** through Gmail (drafts for review, then send)
7. **Syncs to Notion** for tracking pipeline status

## Usage

```bash
# Discover new podcasts
python pitcher.py discover --profile profiles/andreas-duess.md

# Review and mark targets
python pitcher.py review

# Fetch episode transcripts
python pitcher.py transcribe

# Generate pitches
python pitcher.py pitch

# Full pipeline
python pitcher.py run --profile profiles/andreas-duess.md
```

## Cost

About $0.05-0.10 per pitch using RSS descriptions or YouTube transcripts. If using Whisper audio transcription (fallback), add ~$0.14/episode (~$0.42 per podcast for 3 episodes). Discovery is free.

## Stack

- Python 3
- Claude Sonnet (analysis + pitch writing)
- Podcast Index API (discovery)
- YouTube Transcript API (free transcripts)
- Notion API (pipeline tracking)
- Gmail API (outreach)
