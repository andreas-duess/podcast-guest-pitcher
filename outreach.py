"""
Outreach Module

Creates Lemlist campaigns and injects approved pitches as leads.
Campaigns are created PAUSED — activate in Lemlist dashboard after review.

Uses Lemlist API with Basic Auth (empty username, API key as password).
"""

import os
import re
import json
import base64
import time

import yaml
import requests
from dotenv import load_dotenv

load_dotenv()

LEMLIST_API_KEY = os.environ.get("LEMLIST_API_KEY", "")
LEMLIST_BASE_URL = "https://api.lemlist.com/api"

PITCHES_DIR = os.path.join(os.path.dirname(__file__), "data", "pitches")
PODCASTS_DIR = os.path.join(os.path.dirname(__file__), "data", "podcasts")


def _auth_headers():
    """Generate Lemlist auth headers. Empty username, API key as password."""
    auth_string = base64.b64encode(f":{LEMLIST_API_KEY}".encode()).decode()
    return {
        "Content-Type": "application/json",
        "Authorization": f"Basic {auth_string}",
    }


def _lemlist_request(method, path, json_data=None):
    """Make an authenticated request to Lemlist API."""
    url = f"{LEMLIST_BASE_URL}{path}"
    resp = requests.request(
        method, url,
        headers=_auth_headers(),
        json=json_data,
        timeout=30,
    )
    return resp


# ---------------------------------------------------------------------------
# Load Approved Pitches
# ---------------------------------------------------------------------------

def load_pitch(pitch_path):
    """Load a pitch markdown file and extract frontmatter + content."""
    with open(pitch_path, "r") as f:
        content = f.read()

    frontmatter = {}
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            frontmatter = yaml.safe_load(parts[1]) or {}
            body = parts[2].strip()

    # Extract pitch email section
    pitch_email = ""
    in_pitch = False
    for line in body.split("\n"):
        if "## Pitch Email" in line:
            in_pitch = True
            continue
        if in_pitch and line.startswith("## "):
            break
        if in_pitch:
            pitch_email += line + "\n"

    # Extract subject lines
    subject_lines = []
    in_subjects = False
    for line in body.split("\n"):
        if "## Subject Line" in line:
            in_subjects = True
            continue
        if in_subjects and line.startswith("## "):
            break
        if in_subjects:
            match = re.match(r'\d+\.\s*(.+)', line.strip())
            if match:
                subject_lines.append(match.group(1).strip())

    return {
        "frontmatter": frontmatter,
        "pitch_email": pitch_email.strip(),
        "subject_lines": subject_lines,
        "full_body": body,
    }


def get_approved_pitches():
    """Load all pitch files with status 'approved'."""
    approved = []
    for fname in sorted(os.listdir(PITCHES_DIR)):
        if not fname.endswith(".md"):
            continue
        path = os.path.join(PITCHES_DIR, fname)
        pitch = load_pitch(path)
        if pitch["frontmatter"].get("status") == "approved":
            pitch["filepath"] = path
            pitch["slug"] = fname.replace(".md", "")
            approved.append(pitch)
    return approved


# ---------------------------------------------------------------------------
# Campaign Management
# ---------------------------------------------------------------------------

def create_campaign(name):
    """Create a new Lemlist campaign (paused by default)."""
    resp = _lemlist_request("POST", "/campaigns", {
        "name": name,
        "timezone": "America/Toronto",
    })

    if resp.status_code != 200:
        print(f"  Error creating campaign: {resp.status_code} {resp.text[:200]}")
        return None, None

    data = resp.json()
    return data.get("_id"), data.get("sequenceId")


def add_email_step(sequence_id, subject, message_html, index=1, delay=0):
    """Add an email step to a campaign sequence."""
    resp = _lemlist_request("POST", f"/sequences/{sequence_id}/steps", {
        "type": "email",
        "index": index,
        "delay": delay,
        "subject": subject,
        "message": message_html,
    })

    if resp.status_code != 200:
        print(f"  Error adding email step: {resp.status_code} {resp.text[:200]}")
        return None

    return resp.json().get("_id")


def add_lead(campaign_id, email, lead_data):
    """Add a lead/contact to a campaign."""
    resp = _lemlist_request(
        "POST",
        f"/campaigns/{campaign_id}/leads/{email}",
        lead_data,
    )

    if resp.status_code != 200:
        print(f"  Error adding lead {email}: {resp.status_code} {resp.text[:200]}")
        return None

    return resp.json().get("_id")


# ---------------------------------------------------------------------------
# Pitch → Lemlist Conversion
# ---------------------------------------------------------------------------

def pitch_to_html(pitch_text):
    """Convert plain text pitch to simple HTML for Lemlist."""
    paragraphs = pitch_text.strip().split("\n\n")
    html_parts = []
    for p in paragraphs:
        # Handle line breaks within paragraphs
        p = p.replace("\n", "<br>")
        html_parts.append(f"<p>{p}</p>")
    return "\n".join(html_parts)


def send_approved_pitches(dry_run=False):
    """Create Lemlist campaign and inject all approved pitches."""
    if not LEMLIST_API_KEY:
        print("Error: LEMLIST_API_KEY not configured")
        return

    approved = get_approved_pitches()
    if not approved:
        print("No approved pitches found. Mark pitches as 'approved' in frontmatter first.")
        return

    print(f"Found {len(approved)} approved pitch(es)")

    if dry_run:
        for pitch in approved:
            fm = pitch["frontmatter"]
            print(f"\n  [{fm.get('podcast', '')}]")
            print(f"  Host: {fm.get('host', '')}")
            print(f"  Email: {fm.get('contact_email', 'MISSING')}")
            print(f"  Subject: {pitch['subject_lines'][0] if pitch['subject_lines'] else 'NONE'}")
            print(f"  Body preview: {pitch['pitch_email'][:100]}...")
        return

    # Create campaign
    campaign_name = f"Podcast Guest Pitch — {time.strftime('%Y-%m-%d')}"
    print(f"\nCreating campaign: {campaign_name}")
    campaign_id, sequence_id = create_campaign(campaign_name)

    if not campaign_id:
        print("Failed to create campaign. Aborting.")
        return

    print(f"  Campaign ID: {campaign_id}")
    print(f"  Sequence ID: {sequence_id}")

    # Add email template with variables
    subject = "{{subject}}"
    message = "{{pitchBody}}"

    step_id = add_email_step(sequence_id, subject, message, index=1, delay=0)
    if not step_id:
        print("Failed to add email step. Aborting.")
        return

    # Add follow-up (day 7)
    followup_message = (
        "<p>Hi {{firstName}},</p>"
        "<p>Just circling back on my note about coming on {{podcastName}}. "
        "Happy to work around your schedule if the timing's better in a few weeks.</p>"
        "<p>{{guestName}}</p>"
    )
    add_email_step(sequence_id, "Re: {{subject}}", followup_message, index=2, delay=7)

    # Add leads
    added = 0
    for pitch in approved:
        fm = pitch["frontmatter"]
        email = fm.get("contact_email", "")

        if not email:
            print(f"  Skipping {fm.get('podcast', '')} — no contact email")
            continue

        subject_line = pitch["subject_lines"][0] if pitch["subject_lines"] else f"Guest idea for {fm.get('podcast', '')}"
        pitch_html = pitch_to_html(pitch["pitch_email"])

        # Get host name parts
        host_name = fm.get("host", "")
        first_name = host_name.split()[0] if host_name else ""

        lead_data = {
            "email": email,
            "firstName": first_name,
            "lastName": " ".join(host_name.split()[1:]) if len(host_name.split()) > 1 else "",
            "companyName": fm.get("podcast", ""),
            # Custom variables
            "subject": subject_line,
            "podcastName": fm.get("podcast", ""),
            "pitchBody": pitch_html,
            "guestName": fm.get("guest", "Andreas"),
        }

        lead_id = add_lead(campaign_id, email, lead_data)
        if lead_id:
            added += 1
            print(f"  Added: {email} ({fm.get('podcast', '')})")

        time.sleep(0.3)

    print(f"\nDone. Added {added}/{len(approved)} leads to campaign.")
    print(f"Campaign created as PAUSED — activate in Lemlist dashboard.")
    print(f"Campaign: {campaign_name}")


if __name__ == "__main__":
    import sys
    dry_run = "--dry-run" in sys.argv
    send_approved_pitches(dry_run=dry_run)
