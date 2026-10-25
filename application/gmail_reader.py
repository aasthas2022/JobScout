
"""Gmail reader for Greenhouse application verification emails.

Uses Google's Gmail API with read-only OAuth access.

Fixes:
- Find messages that arrived before polling began
- Accept equivalent employer names
- Parse Greenhouse emails strictly
- Prefer the newest matching email
- Show useful lookup diagnostics
- Never persist verification codes

Run:
    python3 -m application.gmail_reader --company Cresta

    python3 -m application.gmail_reader \
        --company Cresta --minutes 15 --wait 120
"""

import argparse
import base64
import html
import os
import re
import time

from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from pathlib import Path


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly"
]

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIR = ROOT / "application_private"

CREDENTIALS_FILE = PRIVATE_DIR / "gmail_credentials.json"
TOKEN_FILE = PRIVATE_DIR / "gmail_token.json"

ALLOWED_SENDERS = {
    "no-reply@us.greenhouse-mail.io",
}

SUBJECT_PREFIX = "security code for your application to "


def normalize(value):
    return re.sub(
        r"\s+",
        " ",
        str(value or "").casefold()
    ).strip()


def normalized_company(value):
    """Normalize common company suffixes for subject matching."""
    value = normalize(value)
    value = re.sub(r"[^\w\s]", " ", value)

    suffixes = (
        "corporation",
        "incorporated",
        "inc",
        "limited",
        "ltd",
        "llc",
        "company",
        "corp",
    )

    words = value.split()

    while words and words[-1] in suffixes:
        words.pop()

    return " ".join(words)


# ==========================================================
# OAuth authentication
# ==========================================================

def get_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    credentials = None

    if TOKEN_FILE.exists():
        try:
            credentials = Credentials.from_authorized_user_file(
                str(TOKEN_FILE),
                SCOPES,
            )
        except (OSError, ValueError):
            credentials = None

    if not credentials or not credentials.valid:

        if (
            credentials
            and credentials.expired
            and credentials.refresh_token
        ):
            try:
                credentials.refresh(Request())
            except Exception:
                credentials = None

        if not credentials or not credentials.valid:
            if not CREDENTIALS_FILE.is_file():
                raise FileNotFoundError(
                    f"Missing Gmail OAuth credentials: "
                    f"{CREDENTIALS_FILE}"
                )

            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE),
                SCOPES,
            )

            credentials = flow.run_local_server(
                host="localhost",
                port=0,
                open_browser=True,
            )

        PRIVATE_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        TOKEN_FILE.write_text(
            credentials.to_json(),
            encoding="utf-8",
        )

        try:
            os.chmod(TOKEN_FILE, 0o600)
        except OSError:
            pass

    return build(
        "gmail",
        "v1",
        credentials=credentials,
        cache_discovery=False,
    )


# ==========================================================
# Message decoding
# ==========================================================

def decode_data(encoded):
    if not encoded:
        return ""

    try:
        padded = encoded + "=" * (-len(encoded) % 4)

        return base64.urlsafe_b64decode(
            padded
        ).decode("utf-8", errors="replace")

    except (ValueError, UnicodeError):
        return ""


def html_to_text(value):
    value = re.sub(
        r"(?is)<(script|style)\b.*?</\1>",
        " ",
        value,
    )

    value = re.sub(
        r"(?i)</?(?:br|p|div|td|tr|li)\b[^>]*>",
        " ",
        value,
    )

    value = re.sub(r"<[^>]+>", " ", value)

    return html.unescape(value)


def extract_body(payload):
    plain_parts = []
    html_parts = []

    def visit(part):
        mime_type = part.get("mimeType", "")
        body = part.get("body") or {}

        content = decode_data(body.get("data"))

        if content:
            if mime_type == "text/plain":
                plain_parts.append(content)

            elif mime_type == "text/html":
                html_parts.append(
                    html_to_text(content)
                )

        for child in part.get("parts", []):
            visit(child)

    visit(payload)

    return "\n".join(plain_parts or html_parts)


def headers_for(payload):
    return {
        item.get("name", "").lower():
            item.get("value", "")
        for item in payload.get("headers", [])
    }


# ==========================================================
# Strict code extraction
# ==========================================================

def extract_code(body):
    """Extract exactly one code after the Greenhouse prompt.

    Never match arbitrary eight-character words such as
    'security' or 'requested'.
    """
    if not body:
        return None

    body = re.sub(
        r"\s+",
        " ",
        html.unescape(body),
    ).strip()

    pattern = (
        r"copy\s+and\s+paste\s+this\s+code\s+"
        r"into\s+the\s+security\s+code\s+field\s+"
        r"on\s+your\s+application\s*:\s*"
        r"([A-Za-z0-9]{8})(?![A-Za-z0-9])"
    )

    matches = re.findall(
        pattern,
        body,
        flags=re.IGNORECASE,
    )

    if not matches or len(set(matches)) != 1:
        return None

    return matches[0]


def employer_matches(subject, company):
    actual = normalize(subject)

    if not actual.startswith(SUBJECT_PREFIX):
        return False

    employer = actual[len(SUBJECT_PREFIX):].strip()

    return (
        normalized_company(employer)
        == normalized_company(company)
    )


def matching_message(message, company, earliest):
    payload = message.get("payload") or {}
    headers = headers_for(payload)

    sender = parseaddr(
        headers.get("from", "")
    )[1].lower()

    if sender not in ALLOWED_SENDERS:
        return None

    if not employer_matches(
        headers.get("subject", ""),
        company,
    ):
        return None

    received = datetime.fromtimestamp(
        int(message.get("internalDate", 0)) / 1000,
        tz=timezone.utc,
    )

    if received < earliest:
        return None

    return extract_code(
        extract_body(payload)
    )


# ==========================================================
# Find a recent matching email
# ==========================================================

def find_code(
    company,
    minutes=15,
    since=None,
    service=None,
    verbose=False,
):
    """Find newest matching code within a recent time window.

    `since`, when supplied, is the earliest accepted email
    arrival time. It does NOT need to be the submit-click time.
    """

    if not company or not company.strip():
        raise ValueError("Company name is required")

    if minutes <= 0:
        raise ValueError("minutes must be positive")

    earliest = since or (
        datetime.now(timezone.utc)
        - timedelta(minutes=minutes)
    )

    if earliest.tzinfo is None:
        raise ValueError(
            "'since' must be timezone-aware"
        )

    if service is None:
        service = get_service()

    # Search Gmail slightly before the cutoff, then
    # apply the precise cutoff to message.internalDate.
    search_after = int(
        earliest.timestamp()
    ) - 60

    query = (
        "from:no-reply@us.greenhouse-mail.io "
        '"Security code for your application" '
        f"after:{search_after}"
    )

    candidates = []
    total_examined = 0
    page_token = None

    for _ in range(3):
        arguments = {
            "userId": "me",
            "q": query,
            "maxResults": 50,
        }

        if page_token:
            arguments["pageToken"] = page_token

        response = (
            service.users()
            .messages()
            .list(**arguments)
            .execute()
        )

        for item in response.get("messages", []):
            total_examined += 1

            message = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=item["id"],
                    format="full",
                )
                .execute()
            )

            code = matching_message(
                message,
                company,
                earliest,
            )

            if code:
                candidates.append((
                    int(message["internalDate"]),
                    code,
                ))

        page_token = response.get("nextPageToken")

        if not page_token:
            break

    if verbose:
        print(
            f"Gmail check: examined {total_examined} "
            f"recent Greenhouse messages; "
            f"valid matches for {company}: "
            f"{len(candidates)}"
        )

    if not candidates:
        return None

    # The newest matching email wins.
    return max(
        candidates,
        key=lambda item: item[0],
    )[1]


# ==========================================================
# Polling
# ==========================================================

def wait_for_code(
    company,
    minutes=15,
    wait_seconds=120,
    poll_interval=5,
    since=None,
    verbose=True,
):
    """Poll Gmail, including emails received before polling starts.

    The lookback window remains fixed for this invocation.
    """

    if wait_seconds < 0:
        raise ValueError(
            "wait_seconds must be non-negative"
        )

    if poll_interval <= 0:
        raise ValueError(
            "poll_interval must be positive"
        )

    service = get_service()

    earliest = since or (
        datetime.now(timezone.utc)
        - timedelta(minutes=minutes)
    )

    deadline = time.monotonic() + wait_seconds
    check_number = 0

    while True:
        check_number += 1

        if verbose:
            print(
                f"Checking Gmail (attempt {check_number})..."
            )

        code = find_code(
            company=company,
            since=earliest,
            service=service,
            verbose=verbose,
        )

        if code:
            return code

        remaining = deadline - time.monotonic()

        if remaining <= 0:
            return None

        time.sleep(
            min(poll_interval, remaining)
        )


# ==========================================================
# CLI
# ==========================================================

def main():
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--company",
        required=True,
    )

    parser.add_argument(
        "--minutes",
        type=int,
        default=15,
    )

    parser.add_argument(
        "--wait",
        type=int,
        default=120,
    )

    args = parser.parse_args()

    code = wait_for_code(
        company=args.company,
        minutes=args.minutes,
        wait_seconds=args.wait,
        verbose=True,
    )

    if code:
        print("\nVerification email found.")
        print("Security code:", code)
        print(
            "Enter the code in the Greenhouse browser."
        )
    else:
        print(
            "\nNo matching verification code found."
        )
        print(
            "Check the sender, employer name, "
            "and email age."
        )


if __name__ == "__main__":
    main()
