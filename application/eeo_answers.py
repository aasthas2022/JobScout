
"""Deterministic EEO answer selection.

Prefer non-disclosure if supported.
Otherwise match explicitly configured answers.
"""

import re


DECLINE_ALIASES = (
    "prefer not to answer",
    "prefer not to disclose",
    "prefer not to say",
    "decline to state",
    "decline to answer",
    "decline to self identify",
    "i do not wish to answer",
    "i don't wish to answer",
    "i do not wish to self identify",
    "choose not to disclose",
    "do not want to disclose",
    "i don't want to disclose",
    "i do not want to answer",
    "i choose not to disclose",
    "i wish not to answer",
)


def normalize(text):
    return re.sub(
        r"[^a-z0-9]+", " ",
        str(text or "").casefold()
    ).strip()


def matches_alias(value, aliases):
    value = normalize(value)

    return any(
        value == normalize(alias)
        for alias in aliases
    )


def question_category(question):
    q = normalize(question)

    if any(x in q for x in (
        "veteran",
        "military service",
        "protected veteran"
    )):
        return "veteran_status"

    if any(x in q for x in (
        "disability",
        "disabled",
        "impairment"
    )):
        return "disability"

    if any(x in q for x in (
        "ethnicity",
        "race",
        "racial background"
    )):
        return "ethnicity"

    if any(x in q for x in (
        "gender",
        "gender identity",
        "sex assigned"
    )):
        return "gender"

    if any(x in q for x in (
        "sexual orientation",
        "lgbtq"
    )):
        return "lgbtq"

    return None


FALLBACK_ALIASES = {
    "gender": (
        "female",
        "woman",
    ),
    "ethnicity": (
        "south asian",
        "asian",
        "asian not hispanic or latino",
    ),
    "disability": (
        "no",
        "no i do not have a disability",
        "no i do not have a disability and "
        "have not had one in the past",
        "i do not have a disability",
    ),
    "veteran_status": (
        "not a veteran",
        "i am not a protected veteran",
        "not a protected veteran",
        "no",
    ),
    "lgbtq": (),
}


def eeo_answer(question, options, profile):
    category = question_category(question)

    if not category:
        return None

    config = profile.get(
        "equal_opportunity_information", {}
    )

    if not options:
        return None

    labels = [
        opt.get("label", "")
        if isinstance(opt, dict)
        else str(opt)
        for opt in options
    ]

    # Priority 1: Decline to disclose
    if config.get("prefer_not_to_disclose", True):
        for label in labels:
            if matches_alias(label, DECLINE_ALIASES):
                return label

    # Priority 2: Explicit saved fallback
    saved_value = config.get(category)

    if not saved_value:
        return None

    aliases = FALLBACK_ALIASES.get(category, ())

    for label in labels:
        if normalize(label) == normalize(saved_value):
            return label

    for label in labels:
        if matches_alias(label, aliases):
            return label

    return None
