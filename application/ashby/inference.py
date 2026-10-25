"""Typed, reviewable AI inferences for unknown Lever questions.

No model-generated consent, attestation, immigration status, health claim,
security clearance or undisclosed credential can be inserted as a fact.
"""

import json
import re
from dataclasses import dataclass
from typing import Optional

from pydantic import BaseModel, Field

from .answers import norm
from ..question_resolver import candidate_facts


@dataclass
class Inference:
    answer: Optional[str] = None
    explanation: str = ""
    review_required: bool = True


class InferenceResult(BaseModel):
    answer: Optional[str] = None
    reason: str = ""
    evidence: list[str] = Field(default_factory=list)


DECLARATIONS = (
    "consent", "acknowledg", "certify", "attest", "agree to", "signature",
    "non compete", "non disclosure", "contractual obligation", "legal restriction",
    "accommodation", "disability", "health condition", "security clearance",
    "criminal", "felony", "visa", "sponsor", "work authorization", "authorized to work",
    "citizenship", "gender", "race", "ethnicity", "veteran",
)


def declaration(question):
    return any(term in norm(question) for term in DECLARATIONS)


def valid_answer(question, value, options=()):
    if not isinstance(value, str) or not value.strip():
        return False
    q, v = norm(question), value.strip()
    if len(v) > 320 or len(v.split()) > 42:
        return False
    if v.lower().startswith(("building ai systems", "the most technically", "i am writing to")):
        return False
    if options:
        labels = [str(o.get("label", "")) if isinstance(o, dict) else str(o) for o in options]
        return sum(norm(o) == norm(v) for o in labels) == 1
    if any(x in q for x in ("percentage", "travel", "percent")):
        match = re.fullmatch(r"\s*(\d{1,3})\s*%?\s*", v)
        return bool(match) and 0 <= int(match.group(1)) <= 100
    if any(x in q for x in ("years of experience", "how many years")):
        return bool(re.fullmatch(r"\d{1,2}(?:\.\d)?\+?", v))
    if any(x in q for x in ("yes or no", "y n", "willing to relocate")):
        return v.lower() in ("yes", "no")
    if "date" in q:
        return False
    if any(x in q for x in ("salary", "compensation", "address", "residen", "name", "certification")):
        return False
    return True


def infer(question, profile, experience, job, options=()):
    """Return only an evidence-supported short preference/qualification draft."""
    if declaration(question):
        return Inference(explanation="Personal or legal declaration requires explicit confirmed data")
    if any(x in norm(question) for x in ("salary", "compensation", "date", "address", "residen", "certification")):
        return Inference(explanation="Needs a direct factual mapping or confirmed preference")

    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(model="gpt-4.1-mini", temperature=0).with_structured_output(InferenceResult)
    fact_map = candidate_facts(profile, experience)
    response = model.invoke([
        ("system", """You are suggesting a short job-application answer for mandatory human review.
Use only candidate facts. Reasonable synthesis is allowed for skills and qualifications;
never invent dates, certifications, licenses, degrees, employment history,
performance statistics, travel or relocation preferences, or legal facts.
Do not infer consent, legal attestations, disability, veteran, gender, race,
security clearance, citizenship, immigration authorization, or contractual obligations.
When applicant evidence is insufficient return answer=null.
For options, answer MUST exactly match one provided option.
Do NOT use hook-first marketing copy for factual inputs. Keep factual text concise.
List factual evidence paths where possible."""),
        ("human", json.dumps({
            "question": question,
            "available_options": [o.get("label") if isinstance(o, dict) else str(o) for o in options],
            "candidate_facts": fact_map,
            "target_job": {"company": job.get("company"), "title": job.get("title"), "location": job.get("location")},
        }, ensure_ascii=False)),
    ])
    if not valid_answer(question, response.answer, options):
        return Inference(explanation=response.reason or "Invalid or unsupported inferred answer")
    return Inference(answer=response.answer.strip(), explanation=response.reason, review_required=True)
