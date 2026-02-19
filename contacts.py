"""
Contact Finding Module

Layered approach to finding podcast host contact info:
1. RSS feed parsing — <managingEditor>, <itunes:owner>
2. Website scraping — contact/booking page
3. Apollo enrichment — host name → email (use sparingly, 75 free credits/month)
"""

import os
import re
import json

import requests
import feedparser
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

APOLLO_API_KEY = os.environ.get("APOLLO_API_KEY", "")
EXA_API_KEY = os.environ.get("EXA_API_KEY", "")

PODCASTS_DIR = os.path.join(os.path.dirname(__file__), "data", "podcasts")


# ---------------------------------------------------------------------------
# Strategy 1: RSS Feed Parsing
# ---------------------------------------------------------------------------

def find_email_in_rss(rss_url):
    """Extract email from RSS feed metadata."""
    if not rss_url:
        return None, None

    try:
        feed = feedparser.parse(rss_url)
    except Exception:
        return None, None

    # Check <managingEditor>
    editor = feed.feed.get("managingeditor", "")
    if editor and "@" in editor:
        # Format is often "email (name)" or just "email"
        match = re.search(r'[\w.+-]+@[\w-]+\.[\w.]+', editor)
        if match:
            email = match.group()
            name = re.sub(r'[\w.+-]+@[\w-]+\.[\w.]+', '', editor).strip(" ()")
            return email, name or None

    # Check <itunes:owner>
    itunes_email = feed.feed.get("publisher_detail", {}).get("email", "")
    itunes_name = feed.feed.get("publisher_detail", {}).get("name", "")
    if not itunes_email:
        # feedparser sometimes puts it here
        itunes_email = feed.feed.get("author_detail", {}).get("email", "")
        itunes_name = feed.feed.get("author_detail", {}).get("name", "")

    if itunes_email and "@" in itunes_email:
        return itunes_email, itunes_name or None

    # Scan all feed-level fields for any email
    for key, value in feed.feed.items():
        if isinstance(value, str) and "@" in value:
            match = re.search(r'[\w.+-]+@[\w-]+\.[\w.]+', value)
            if match:
                return match.group(), None

    return None, None


# ---------------------------------------------------------------------------
# Strategy 2: Website Scraping
# ---------------------------------------------------------------------------

def find_contact_on_website(website_url):
    """Scrape podcast website for contact/booking page and email."""
    if not website_url:
        return None, None

    try:
        resp = requests.get(
            website_url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception:
        return None, None

    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text()

    # Look for emails in page text
    emails = re.findall(r'[\w.+-]+@[\w-]+\.[\w.]+', text)
    # Filter out common non-contact emails
    filtered = [e for e in emails if not any(x in e.lower() for x in [
        "noreply", "no-reply", "support@", "info@wordpress", "example.com",
        "wixpress", "squarespace",
    ])]

    if filtered:
        return filtered[0], None

    # Look for contact/booking page links
    contact_links = []
    for link in soup.find_all("a", href=True):
        href = link["href"].lower()
        link_text = link.get_text().lower()
        if any(word in href or word in link_text for word in [
            "contact", "book", "guest", "pitch", "appear", "apply",
        ]):
            full_url = href if href.startswith("http") else website_url.rstrip("/") + "/" + href.lstrip("/")
            contact_links.append(full_url)

    # Follow first contact link
    for contact_url in contact_links[:2]:
        try:
            resp = requests.get(
                contact_url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )
            resp.raise_for_status()
            page_text = BeautifulSoup(resp.text, "html.parser").get_text()
            emails = re.findall(r'[\w.+-]+@[\w-]+\.[\w.]+', page_text)
            filtered = [e for e in emails if "noreply" not in e.lower() and "example" not in e.lower()]
            if filtered:
                return filtered[0], None
        except Exception:
            continue

    return None, None


# ---------------------------------------------------------------------------
# Strategy 3: Apollo Enrichment (use sparingly)
# ---------------------------------------------------------------------------

def find_email_via_apollo(person_name, company_name=""):
    """Use Apollo API to find email for a person. Costs 1 credit per lookup."""
    if not APOLLO_API_KEY or not person_name:
        return None

    try:
        # Split name
        parts = person_name.strip().split()
        first_name = parts[0] if parts else ""
        last_name = parts[-1] if len(parts) > 1 else ""

        resp = requests.post(
            "https://api.apollo.io/api/v1/people/match",
            headers={
                "X-Api-Key": APOLLO_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "first_name": first_name,
                "last_name": last_name,
                "organization_name": company_name,
            },
            timeout=15,
        )

        if resp.status_code != 200:
            return None

        data = resp.json()
        person = data.get("person", {})
        return person.get("email")

    except Exception as e:
        print(f"    Apollo lookup failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Main Contact Flow
# ---------------------------------------------------------------------------

def find_contact(podcast_record, use_apollo=False):
    """Find contact info for a podcast host. Returns (email, name, source)."""
    name = podcast_record.get("name", "")
    host = podcast_record.get("host", "")
    rss_url = podcast_record.get("rss_url", "")
    website = podcast_record.get("website", "")

    print(f"  Finding contact for: {name}")

    # Already have contact?
    if podcast_record.get("contact_email"):
        print(f"    Already have: {podcast_record['contact_email']}")
        return podcast_record["contact_email"], podcast_record.get("contact_name"), "existing"

    # Strategy 1: RSS
    email, contact_name = find_email_in_rss(rss_url)
    if email:
        print(f"    RSS: {email}")
        return email, contact_name or host, "rss"

    # Strategy 2: Website
    email, _ = find_contact_on_website(website)
    if email:
        print(f"    Website: {email}")
        return email, host, "website"

    # Strategy 3: Apollo (only if explicitly enabled)
    if use_apollo and host:
        email = find_email_via_apollo(host, name)
        if email:
            print(f"    Apollo: {email}")
            return email, host, "apollo"

    print(f"    No contact found")
    return None, host, None


def find_all_contacts(podcasts, use_apollo=False):
    """Find contacts for all pursued podcasts and update their records."""
    found = 0
    for record in podcasts:
        if not record.get("pursue"):
            continue

        email, name, source = find_contact(record, use_apollo=use_apollo)
        if email:
            record["contact_email"] = email
            record["contact_name"] = name or ""
            record["contact_source"] = source

            # Save updated record
            slug = record.get("slug", "unknown")
            filepath = os.path.join(PODCASTS_DIR, f"{slug}.json")
            with open(filepath, "w") as f:
                json.dump(record, f, indent=2)

            found += 1

    return found


if __name__ == "__main__":
    from discovery import load_pursued_podcasts
    pursued = load_pursued_podcasts()
    print(f"Finding contacts for {len(pursued)} pursued podcasts...")
    found = find_all_contacts(pursued)
    print(f"\nFound {found} contacts")
