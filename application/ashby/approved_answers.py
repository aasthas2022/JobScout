"""Private Ashby answer bank: explicit post-review reuse for non-sensitive answers."""
import json
import os
import re
from pathlib import Path


def normalize(value):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value).casefold())).strip()


RESTRICTED = (
    "consent", "acknowledg", "certif", "attest", "signature", "agree", "accommodat",
    "disability", "gender", "race", "ethnic", "veteran", "health", "medical",
    "criminal", "convict", "clearance", "citizen", "visa", "immigration",
    "sponsor", "authoriz", "right to work", "work permit", "birth", "age",
    "salary", "compensation", "date", "previously employed", "worked for",
    "currently work for", "work in our", "in office", "onsite", "on site",
)


def reusable(question, answer):
    q = normalize(question)
    value = str(answer or "").strip()
    if not q or not value or any(s in q for s in RESTRICTED):
        return False
    if len(value) > 350 or len(value.split()) > 45:
        return False
    if q.startswith(("why ", "describe ", "tell us ", "how have ", "what excites", "additional ")):
        return False
    if any(term in q for term in ("location", "address", "postal", "phone", "email")):
        # These can be reused but are already in the profile; never overgeneralize.
        return True
    if "travel" in q:
        match = re.fullmatch(r"(\d{1,3})\s*%?", value)
        return bool(match) and 0 <= int(match.group(1)) <= 100
    return True


def key_for(question):
    q = normalize(question)
    # Normalize only meaning-equivalent phrasings. Avoid employer-specific generalization.
    if q in ("current location", "your current location", "location"):
        return "current_location"
    if q in ("what percent of travel are you open to", "what of travel are you open to", "travel percentage"):
        return "travel_percentage"
    if q in ("physical mailing address", "primary residence address", "mailing address"):
        return "mailing_address"
    if q in ("which state province or region do you currently live in", "state province or region"):
        return "current_state"
    return "question:" + q


class ApprovedAnswerBank:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.values = raw if isinstance(raw, dict) else {}
        else:
            self.values = {}

    def get(self, question):
        answer = self.values.get(key_for(question))
        return answer if reusable(question, answer) else None

    def put(self, question, answer):
        if not reusable(question, answer):
            return False
        key = key_for(question)
        value = str(answer).strip()
        if self.values.get(key) == value:
            return False
        self.values[key] = value
        return True

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.values, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
