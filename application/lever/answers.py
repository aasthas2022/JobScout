"""Factual Lever answers. Narrative AI must never fill these categories."""

import re
from datetime import date


def norm(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def label(field):
    return str(field.get("label") or field.get("context") or field.get("name") or "").strip()


def get(profile, *keys):
    for section in (profile, profile.get("personal") or {}, profile.get("personal_info") or {},
                    profile.get("contact") or {}, profile.get("address") or {},
                    profile.get("professional") or {}, profile.get("employment_information") or {},
                    profile.get("application_preferences") or {}):
        if isinstance(section, dict):
            for key in keys:
                value = section.get(key)
                if value is not None and value != "":
                    return value
    return None


def yes_no(value):
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return norm(value).title() if norm(value) in ("yes", "no") else None


def option_match(options, answer):
    available = [o["label"] if isinstance(o, dict) else str(o) for o in options]
    exact = [x for x in available if norm(x) == norm(answer)]
    if len(exact) == 1:
        return exact[0]
    refusals = ("prefer not to disclose", "prefer not to answer", "decline to answer", "i do not want to answer",
                "i decline to self identify", "i decline to self identify my veteran status", "i choose not to disclose")
    if norm(answer) in refusals:
        matches = [x for x in available if any(p in norm(x) for p in refusals)]
        return matches[0] if len(matches) == 1 else None
    return None


def education_text(profile, experience):
    sources = [profile.get("education") or [], (experience.get("candidate") or {}).get("education") or []]
    entries, seen = [], set()
    for items in sources:
        for item in items:
            if not isinstance(item, dict):
                continue
            school = item.get("university") or item.get("institution") or item.get("school")
            degree = item.get("degree")
            major = item.get("field") or item.get("major")
            if not school or not degree:
                continue
            if major and norm(major) not in norm(degree):
                degree = f"{degree} in {major}"
            entry = f"{school}; {degree}"
            if norm(entry) not in seen:
                entries.append(entry)
                seen.add(norm(entry))
    return "\n".join(entries) or None


def compensation(job):
    """Posted salary first; market framing; user-approved $140k-$160k fallback."""
    posted = job.get("salary") or job.get("salary_range") or job.get("compensation")
    if isinstance(posted, dict):
        posted = posted.get("text") or posted.get("range")
    if not posted:
        description = str(job.get("description") or job.get("job_description") or job.get("description_text") or "")
        match = re.search(r"\$\s*([\d,.]+)\s*([kK]?)\s*(?:-|–|—|to)\s*\$?\s*([\d,.]+)\s*([kK]?)", description)
        if match:
            first, first_k, second, second_k = match.groups()
            low, high = float(first.replace(",", "")), float(second.replace(",", ""))
            if first_k or (second_k and low < 1000):
                low *= 1000
            if second_k or (first_k and high < 1000):
                high *= 1000
            if 60000 <= low <= high <= 1000000:
                posted = f"${low:,.0f}–${high:,.0f}"
    if posted:
        return (f"I'm considering the posted compensation range of {posted}, alongside market-standard pay "
                "for the responsibilities and location. As a fallback reference, my preferred base salary "
                "range is $140,000–$160,000; I'm flexible depending on total compensation and benefits.")
    return ("I'm seeking market-aligned compensation for this role and location. As a fallback reference, "
            "my preferred base salary range is $140,000–$160,000, with flexibility based on total compensation and benefits.")


def classify(field):
    q = norm(label(field))
    if field.get("type") == "file":
        return "cover" if "cover letter" in q else "file"
    if "cover letter" in q:
        return "cover"
    if field.get("type") in ("checkbox", "radio"):
        return "choice"
    if any(x in q for x in ("acknowledg", "consent", "certify", "signature", "non compete", "contractual obligation")):
        return "manual"
    factual = ("salary", "compensation", "travel", "relocat", "resid", "address", "city", "state", "postal",
               "degree", "education", "school", "college", "university", "certification", "security clearance",
               "sponsor", "visa", "authoriz", "right to work", "date", "birth", "name", "email", "phone",
               "gender", "race", "ethnic", "veteran", "disability", "linkedin", "github", "portfolio",
               "website", "notice period", "work eligibility", "accommodation")
    if any(x in q for x in factual):
        return "fact"
    narrative = ("why do you", "why are you", "why should we", "why would you", "tell us about", "describe your",
                 "describe a", "explain your experience", "what excites you", "what motivates you", "what makes you",
                 "how have you", "how would you", "experience building", "additional information about yourself")
    return "narrative" if any(x in q for x in narrative) else "fact"


def factual_answer(field, profile, experience, resume, job):
    q = norm(label(field))
    if field.get("type") == "file":
        return str(resume) if any(x in q for x in ("resume", "curriculum", " cv ")) else None
    if "email" in q:
        return get(profile, "email", "email_address")
    if "phone" in q or "mobile" in q:
        return get(profile, "phone", "phone_number")
    if "linkedin" in q:
        return get(profile, "linkedin", "linkedin_url") or (profile.get("links") or {}).get("linkedin")
    if "github" in q:
        return get(profile, "github", "github_url") or (profile.get("links") or {}).get("github")
    if "website" in q or "portfolio" in q:
        return get(profile, "portfolio", "portfolio_url") or (profile.get("links") or {}).get("portfolio")
    if "first name" in q:
        return get(profile, "first_name")
    if "last name" in q:
        return get(profile, "last_name")
    if q in ("name", "full name", "candidate name", "your name"):
        return get(profile, "full_name") or " ".join(filter(None, (get(profile, "first_name"), get(profile, "last_name")))) or None
    if q in ("date", "current date", "today s date"):
        return date.today().strftime("%m/%d/%Y")
    if any(x in q for x in ("post secondary education", "education attained", "degree obtained", "college name", "universities attended")):
        return education_text(profile, experience)
    if any(x in q for x in ("currently reside", "city state", "city and state")):
        return ", ".join(str(x) for x in (get(profile, "city"), get(profile, "state", "state_province")) if x) or None
    if any(x in q for x in ("primary residence address", "street address", "home address")):
        return get(profile, "street_address", "address_line_1", "address1")
    if q in ("location", "current location", "current city"):
        return ", ".join(str(x) for x in (get(profile, "city"), get(profile, "state")) if x) or None
    if "travel" in q:
        return get(profile, "travel_percentage", "max_travel_percent")
    if "where" in q and "relocat" in q:
        value = get(profile, "relocation_locations")
        return ", ".join(map(str, value)) if isinstance(value, list) else value
    if "relocat" in q:
        return yes_no(get(profile, "willing_to_relocate", "relocation_willingness"))
    if "certification" in q:
        value = get(profile, "certifications")
        return ", ".join(map(str, value)) if isinstance(value, list) and value else ("None" if value == [] else value)
    if "salary" in q or "compensation" in q:
        return compensation(job)
    if "security clearance" in q:
        return yes_no(get(profile, "security_clearance"))
    if any(x in q for x in ("authorized to work", "legally authorized", "work authorization")) and ("united states" in q or " usa " in f" {q} "):
        return yes_no(get(profile, "authorized_to_work_us", "us_work_authorization"))
    if ("sponsor" in q or "sponsorship" in q) and ("united states" in q or " usa " in f" {q} "):
        return yes_no(get(profile, "requires_sponsorship_us", "requires_visa_sponsorship", "requires_sponsorship"))
    if "current employer" in q or "most recent employer" in q:
        return get(profile, "current_employer", "most_recent_employer") or (experience.get("career") or {}).get("most_recent_employer")
    if "job title" in q:
        return get(profile, "current_title", "most_recent_title") or (experience.get("career") or {}).get("most_recent_title")
    if q in ("gender", "race", "ethnicity", "veteran status", "disability status"):
        return (profile.get("application_preferences") or {}).get("eeo_response", "Prefer not to disclose")
    return None


def safe_memory_answer(question, value):
    """Reject the bad prose saved during earlier Lever runs."""
    if not isinstance(value, str) or not value.strip():
        return False
    q, v = norm(question), value.strip()
    if any(x in q for x in ("consent", "acknowledg", "certify", "signature", "accommodation", "disability", "gender", "race", "veteran", "date")):
        return False
    if norm(v).startswith(("building ai systems", "the most technically", "what excites me", "my experience spans")):
        return False
    if "travel" in q:
        m = re.fullmatch(r"\s*(\d{1,3})\s*%?\s*", v)
        return bool(m) and int(m.group(1)) <= 100
    if any(x in q for x in ("sponsor", "authorized", "security clearance", "willing to relocate")):
        return norm(v) in ("yes", "no")
    if any(x in q for x in ("salary", "relocat", "address", "location", "certification")) and len(v) > 300:
        return False
    return True
