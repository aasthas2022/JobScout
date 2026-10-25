
"""Approved reusable answers for JobScout.

Never store OTPs, consent, signatures, or sensitive disclosures.
Only answers explicitly approved by the user are written.
"""

import json
import re
from difflib import SequenceMatcher
from pathlib import Path


def normalize(value):
    return re.sub(
        r"\s+", " ",
        re.sub(r"[^a-z0-9]+", " ", str(value).lower()),
    ).strip()


BLOCKED_WORDS = (
    "security code", "verification code", "one time password",
    "captcha", "recaptcha", "privacy notice", "consent",
    "acknowledge", "acknowledgement", "signature",
    "certify", "certification", "terms and conditions",
    "criminal", "conviction", "arrest", "background check",
    "transgender", "sexual orientation", "gender identity",
    "race", "ethnicity", "disability", "veteran",
    "work authorization", "authorized to work",
    "legally eligible to work", "visa", "sponsorship",
    "immigration", "previously employed", "worked for",
    "employed by", "employment history",
)


def sensitive(question):
    q = normalize(question)
    return any(normalize(word) in q for word in BLOCKED_WORDS)


class AnswerMemory:
    def __init__(self, path):
        self.path = Path(path)
        self.answers = {}

        if self.path.exists():
            try:
                data = json.loads(
                    self.path.read_text(encoding="utf-8")
                )
                if isinstance(data, dict):
                    # Support either a plain mapping or a
                    # {"answers": {...}} file.
                    source = data.get("answers", data)
                    if isinstance(source, dict):
                        for question, value in source.items():
                            if isinstance(value, dict):
                                value = value.get("answer")
                            if isinstance(value, (str, int, float)):
                                self.answers[str(question)] = str(value)
            except (OSError, ValueError):
                print("Warning: unable to read answer memory.")

    def get(self, question, allow_similar=True):
        if sensitive(question):
            return None

        wanted = normalize(question)

        for saved_question, answer in self.answers.items():
            if normalize(saved_question) == wanted:
                return answer

        if not allow_similar or len(wanted) < 25:
            return None

        # Very conservative reuse of near-identical wording.
        matches = []
        for saved_question, answer in self.answers.items():
            if sensitive(saved_question):
                continue

            score = SequenceMatcher(
                None, wanted, normalize(saved_question)
            ).ratio()

            if score >= 0.94:
                matches.append((score, answer))

        matches.sort(reverse=True)

        # Ambiguous matches must be reviewed.
        if len(matches) > 1:
            if matches[0][1] != matches[1][1]:
                return None

        return matches[0][1] if matches else None

    def put(self, question, answer):
        question = str(question or "").strip()
        answer = str(answer or "").strip()

        if not question or not answer or sensitive(question):
            return False

        if len(question) > 2000 or len(answer) > 5000:
            return False

        # Update an existing equivalent question.
        for saved_question in list(self.answers):
            if normalize(saved_question) == normalize(question):
                self.answers[saved_question] = answer
                return True

        self.answers[question] = answer
        return True

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # Write atomically to avoid corrupting existing answers.
        temp_path = self.path.with_name(
            self.path.name + ".tmp"
        )

        temp_path.write_text(
            json.dumps(
                self.answers,
                indent=2,
                ensure_ascii=False,
            ) + "\n",
            encoding="utf-8",
        )

        temp_path.replace(self.path)

        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def remember(self, question, answer):
        if self.put(question, answer):
            self.save()
            return True
        return False
