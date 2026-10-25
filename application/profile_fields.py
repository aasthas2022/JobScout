
"""Map unambiguous profile facts to application fields."""

import re
from .auto_answers import norm, category


def has(text, *phrases):
    q = f" {norm(text)} "
    return any(f" {norm(p)} " in q for p in phrases)


def position_index(text, prefixes):
    for n in range(1, 15):
        if any(has(text, f"{p} {n}") for p in prefixes):
            return n - 1
    return None


def date_part(value, text):
    if not value:
        return None

    parts = value.split("-")

    if has(text, "year"):
        return parts[0]

    if has(text, "month"):
        return parts[1] if len(parts) > 1 else None

    return value


def known_profile_value(item, profile, resume):
    label = norm(item.get("label"))
    name = norm(item.get("name"))
    field_id = norm(item.get("id"))
    context = norm(item.get("context"))
    kind = item.get("type", "").lower()

    identity = " ".join((label, name, field_id))

    # Dedicated matching handles EEO and immigration.
    if category(identity):
        return None

    if has(
        identity,
        "consent", "certify", "signature",
        "background check", "criminal",
    ):
        return None

    if kind == "file":
        if has(
            identity,
            "cover letter", "coverletter",
            "additional attachment", "other document"
        ):
            return None

        if has(
            identity,
            "resume", "curriculum vitae", "cv"
        ):
            return str(resume)

        if has(context, "resume upload", "resume cv"):
            return str(resume)

        return None

    if kind in ("checkbox", "radio", "password", "hidden"):
        return None

    if has(
        identity,
        "phone country", "country code",
        "dial code", "calling code"
    ):
        return None

    if has(identity, "phone", "mobile", "telephone"):
        phone = profile.get("phone")

        if (
            phone and phone.startswith("+1")
            and has(identity, "national number", "without country code")
        ):
            return phone[2:]

        return phone

    basics = (
        (("preferred name",), "preferred_name"),
        (("first name", "given name", "firstname"), "first_name"),
        (("last name", "family name", "surname", "lastname"), "last_name"),
        (("email", "e mail"), "email"),
        (("linkedin",), "linkedin"),
        (("github",), "github"),
        (("portfolio", "personal website"), "portfolio"),
    )

    for aliases, key in basics:
        if has(identity, *aliases):
            return profile.get(key)

    address = profile.get("address", {})

    if has(identity, "address line 2", "apartment", "suite"):
        return address.get("address_line_2")

    if has(identity, "street address", "address line 1"):
        return address.get("street")

    if has(identity, "zip code", "postal code", "zip"):
        return address.get("postal_code")

    if label in ("city", "current city"):
        return address.get("city")

    if label in ("state", "state province"):
        return address.get("state")

    if has(identity, "which u s state or canadian province"):
        return address.get("state")

    if label in ("country", "country of residence"):
        return address.get("country")

    professional = profile.get("professional", {})
    employment = profile.get("employment", [])

    if has(identity, "current employer", "current company"):
        return professional.get("current_employer")

    if has(identity, "current job title", "current position"):
        return professional.get("current_title")

    job_index = position_index(
        identity,
        ("employment", "experience", "job", "company", "employer")
    )

    if job_index is not None and job_index < len(employment):
        job = employment[job_index]

        if has(label, "company", "employer"):
            return job.get("employer")

        if has(label, "job title", "position", "role title"):
            return job.get("title")

        if has(label, "start date", "start year", "start month"):
            return date_part(job.get("start_date"), identity)

        if has(label, "end date", "end year", "end month"):
            return date_part(job.get("end_date"), identity)

    education = profile.get("education", [])
    edu_index = position_index(
        identity, ("education", "school")
    )

    if has(identity, "masters degree", "master degree"):
        edu_index = 0
    elif has(identity, "bachelor degree"):
        edu_index = 1

    if edu_index is not None and edu_index < len(education):
        school = education[edu_index]

        if has(label, "school", "university", "institution"):
            return school.get("institution")

        if has(label, "major", "field of study"):
            return school.get("field_of_study")

        if has(label, "degree"):
            return school.get("degree")

        if has(label, "graduation", "end year", "end date"):
            return date_part(school.get("end_date"), identity)

    if has(identity, "highest degree") and education:
        return education[0].get("degree")

    return None
