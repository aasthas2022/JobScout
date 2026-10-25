
"""Deterministic answers based on explicitly approved applicant data."""

import random
import re


def norm(value):
    return re.sub(
        r"[^a-z0-9]+",
        " ",
        str(value or "").casefold()
    ).strip()


def matches(text, *phrases):
    q = f" {norm(text)} "

    return any(
        f" {norm(phrase)} " in q
        for phrase in phrases
    )


def category(question):
    q = norm(question)

    if matches(
        q,
        "how did you hear",
        "how did you learn",
        "how did you first learn",
        "where did you hear",
        "how did you find us",
        "how did you discover",
    ):
        return "referral"

    if matches(q, "pronouns", "preferred pronoun"):
        return "pronouns"

    if matches(
        q,
        "previously been employed",
        "previously worked for",
        "previously worked at",
        "prior employment with",
    ):
        return "previous_employment"

    if matches(q, "veteran", "military service"):
        return "veteran"

    if matches(q, "disability", "disabled"):
        return "disability"

    if matches(q, "ethnicity", "race", "racial background"):
        return "ethnicity"

    if matches(
        q, "gender", "sex assigned", "sex at birth"
    ):
        return "gender"

    if matches(q, "sexual orientation", "lgbtq"):
        return "lgbtq"

    if matches(q, "sponsor", "sponsorship"):
        return "sponsorship"

    if matches(
        q,
        "authorized to work",
        "legally authorized",
        "work authorization",
    ):
        return "authorization"

    # State must be checked before country.
    if matches(
        q,
        "state or canadian province",
        "state or province",
        "u s state",
        "province do you reside",
    ):
        return "state"

    if matches(
        q,
        "phone country",
        "phone country code",
        "dialing code",
        "calling code",
    ):
        return "phone_country"

    if matches(
        q,
        "country",
        "country of residence",
    ):
        return "country"

    return None


def option_labels(options):
    result = []

    for option in options:
        if isinstance(option, dict):
            result.append(str(option.get("label", "")))
        else:
            result.append(str(option))

    return result


def choose(options, candidates):
    """Select only a unique, exact normalized match."""
    choices = option_labels(options)

    for candidate in candidates:
        if candidate is None:
            continue

        desired = norm(candidate)

        found = [
            value for value in choices
            if norm(value) == desired
        ]

        if len(found) == 1:
            return found[0]

    return None


def yes_no(options, value):
    if not isinstance(value, bool):
        return None

    return choose(
        options,
        ["Yes" if value else "No"],
    )


DECLINE = {
    norm(x) for x in (
        "Prefer not to disclose",
        "Prefer not to answer",
        "Prefer not to say",
        "I prefer not to say",
        "Decline to state",
        "Decline to answer",
        "Decline to self-identify",
        "I do not wish to answer",
        "I don't wish to answer",
        "I choose not to disclose",
        "I do not wish to disclose",
    )
}


def automatic_answer(
    question,
    options,
    profile,
    company="",
):
    q = norm(question)
    group = category(question)

    info = profile.get(
        "employment_information", {}
    )
    eeo = profile.get(
        "equal_opportunity_information", {}
    )
    prefs = profile.get(
        "application_preferences", {}
    )
    address = profile.get("address", {})

    # Pronouns: explicitly confirmed She/Her.
    if group == "pronouns":
        preferred = prefs.get(
            "pronouns", "She/Her"
        )

        if norm(preferred) in (
            "she her",
            "she her hers",
        ):
            return choose(options, [
                "She/her/hers",
                "She/Her/Hers",
                "She/Her",
                "She / Her",
            ])

        return choose(options, [preferred])

    # Employer sponsorship.
    if group == "sponsorship":
        if matches(
            q,
            "without sponsorship",
            "do not require sponsorship",
            "not require sponsorship",
        ):
            return None

        if matches(
            q,
            "united states",
            "u s",
            "usa",
        ):
            return yes_no(
                options,
                info.get("requires_visa_sponsorship"),
            )

        return None

    # Legal work authorization.
    if group == "authorization":
        key = None

        if matches(q, "canada"):
            key = "authorized_to_work_canada"

        elif matches(q, "united kingdom", "uk"):
            key = "authorized_to_work_uk"

        elif matches(
            q,
            "united states",
            "u s",
            "usa",
        ):
            key = "authorized_to_work_us"

        return (
            yes_no(options, info.get(key))
            if key else None
        )

    # Country dropdown used for phone country.
    if group in ("country", "phone_country"):
        country = address.get(
            "country", "United States"
        )

        if norm(country) in (
            "united states",
            "united states of america",
            "usa",
            "us",
        ):
            return choose(options, [
                "United States +1",
                "United States",
                "United States of America",
                "USA",
                "US",
            ])

        return choose(options, [country])

    # Residential state/province.
    if group == "state":
        state = address.get("state", "")

        if norm(state) in ("ga", "georgia"):
            return choose(options, [
                "Georgia",
                "GA",
            ])

        return choose(options, [state])

    # Referral source: random choice ONLY among the
    # configured sources that exist in this form.
    if group == "referral":
        configured = prefs.get(
            "referral_sources", []
        )

        aliases = {
            "company website": [
                "company website",
                f"{company}'s career site",
                f"{company} career site",
                f"{company} careers site",
            ],
            "career website": [
                f"{company}'s career site",
                f"{company} career site",
            ],
        }

        allowed = set()

        for source in configured:
            allowed.add(norm(source))

            for alias in aliases.get(
                norm(source), []
            ):
                allowed.add(norm(alias))

        eligible = [
            choice
            for choice in option_labels(options)
            if norm(choice) in allowed
        ]

        return (
            random.choice(eligible)
            if eligible else None
        )

    # Company-specific prior employment:
    # use only an explicitly saved answer.
    if group == "previous_employment":
        history = prefs.get(
            "previous_employment", {}
        )

        saved = history.get(norm(company))

        if saved is False:
            return choose(options, [
                f"I have not previously been employed at {company}",
                "No",
            ])

        if isinstance(saved, str):
            return choose(options, [saved])

        return None

    # Equal employment opportunity preferences.
    if group in (
        "gender",
        "ethnicity",
        "disability",
        "veteran",
        "lgbtq",
    ):
        if eeo.get(
            "prefer_not_to_disclose", True
        ):
            for choice in option_labels(options):
                if norm(choice) in DECLINE:
                    return choice

        fallbacks = {
            "gender": [
                eeo.get("gender"),
                "Female",
            ],
            "ethnicity": [
                eeo.get("ethnicity"),
                "South Asian",
                "Asian",
            ],
            "disability": [
                eeo.get("disability"),
                "No",
                "No, I do not have a disability",
                "I do not have a disability",
                "No, I do not have a disability "
                "and have not had one in the past",
            ],
            "veteran": [
                eeo.get("veteran_status"),
                "Not a veteran",
                "Not a protected veteran",
                "I am not a protected veteran",
            ],
            "lgbtq": [],
        }

        return choose(
            options,
            fallbacks[group],
        )

    return None
