
"""Group-aware application question resolution."""

from .question_resolver import (
    QuestionResolver,
    exact_option,
)


def resolve_group(
    group,
    profile,
    memory,
    use_agent=False,
    experience=None,
    job=None,
):
    """Return (exact_answer, source) or (None, reason)."""

    group_type = group.get("type")

    if group_type != "single_choice":
        return (
            None,
            "Multiple-choice group requires review"
        )

    if not group.get("identified"):
        return (
            None,
            "Question label not reliably identified"
        )

    question = str(
        group.get("question") or ""
    ).strip()

    options = [
        item
        for item in group.get("options", [])
        if not item.get("disabled", False)
    ]

    if not question:
        return None, "Question text unavailable"

    if len(options) < 2:
        return None, "Insufficient available options"

    resolver = QuestionResolver(
        profile=profile,
        memory=memory,
        use_agent=use_agent,
        experience=experience or {},
    )

    item = {
        "label": question,
        "context": question,
        "type": "single_choice",
        "tag": "group",
        "name": group.get("name"),
    }

    result = resolver.resolve(
        item=item,
        options=options,
        resume="",
        job=job or {},
    )

    if result.answer is None:
        return (
            None,
            result.reason or "Unresolved question"
        )

    selected = exact_option(
        options,
        result.answer,
    )

    if selected is None:
        return (
            None,
            "Suggested answer does not match an option"
        )

    return selected, result.source
