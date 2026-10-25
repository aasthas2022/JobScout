
"""Agentic suggestion from approved answer memory."""

from pydantic import BaseModel, Field
from .memory import sensitive


class Suggestion(BaseModel):
    answer: str = Field(
        description=(
            "Exact answer from an approved memory entry, "
            "or an empty string"
        )
    )

    confidence: float = Field(ge=0, le=1)
    reason: str


def suggest(
    question: str,
    options: list[str],
    known: dict[str, str]
) -> str | None:

    if sensitive(question) or not known:
        return None

    try:
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            model="gpt-4.1-mini",
            temperature=0
        ).with_structured_output(Suggestion)

        result = llm.invoke([
            (
                "system",
                """
You match job application questions to
previously USER-APPROVED answers.

Rules:
1. Never invent personal facts.
2. Only return an answer present in memory.
3. Do not infer work authorization.
4. Do not infer sponsorship eligibility.
5. Do not answer demographic questions.
6. Do not guess company-specific answers.
7. Return an empty answer when uncertain.
"""
            ),
            (
                "human",
                f"""
Question: {question}

Allowed options:
{options}

Previously approved answers:
{known}
"""
            )
        ])

        approved_values = list(known.values())

        if result.confidence < 0.92:
            return None

        if result.answer not in approved_values:
            return None

        if options and result.answer not in options:
            return None

        return result.answer

    except Exception as exc:
        print(
            "Agent suggestion unavailable:",
            type(exc).__name__
        )
        return None
