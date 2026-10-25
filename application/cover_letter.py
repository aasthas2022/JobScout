
"""Greenhouse cover-letter generation and manual-entry automation.

Works with Greenhouse forms where the Cover Letter upload
widget offers an "Enter manually" action.

Does not click the Resume/CV manual-entry button.
"""

import re

from .question_resolver import (
    candidate_facts,
    semantic_answer,
)


BUTTON_MARKER = "data-jobscout-cover-letter-button"
EDITOR_MARKER = "data-jobscout-cover-letter-editor"


def find_manual_entry_button(page):
    """Mark the manual-entry button associated with Cover Letter."""

    result = page.evaluate(
        r"""() => {
            const marker = 'data-jobscout-cover-letter-button';

            document.querySelectorAll(`[${marker}]`)
                .forEach(el => el.removeAttribute(marker));

            const text = el =>
                (el?.innerText || el?.textContent || '')
                    .replace(/\s+/g, ' ')
                    .trim();

            const visible = el => {
                if (!el) return false;
                const style = getComputedStyle(el);
                return style.display !== 'none' &&
                       style.visibility !== 'hidden' &&
                       !!(el.getClientRects().length);
            };

            const all = Array.from(
                document.querySelectorAll('body *')
            );

            // Prefer label-like elements instead of a large
            // parent whose text contains "Cover Letter".
            const labels = all.filter(el => {
                if (!visible(el)) return false;
                if (text(el).toLowerCase() !== 'cover letter') {
                    return false;
                }

                return !Array.from(el.children).some(
                    child =>
                        text(child).toLowerCase() === 'cover letter'
                );
            });

            const buttons = Array.from(
                document.querySelectorAll(
                    'button, [role="button"], ' +
                    'input[type="button"], a'
                )
            ).filter(el => {
                const label = (
                    el.tagName === 'INPUT'
                        ? el.value
                        : text(el)
                );
                return visible(el) &&
                    label.toLowerCase() === 'enter manually';
            });

            if (!labels.length) {
                return {
                    found: false,
                    reason: 'Cover Letter label not found'
                };
            }

            if (!buttons.length) {
                return {
                    found: false,
                    reason: 'No visible Enter manually button'
                };
            }

            // Find the closest "Enter manually" control
            // occurring AFTER the cover-letter label.
            const candidates = [];

            for (const label of labels) {
                for (const button of buttons) {
                    const relation =
                        label.compareDocumentPosition(button);

                    if (!(relation &
                          Node.DOCUMENT_POSITION_FOLLOWING)) {
                        continue;
                    }

                    // Reject buttons preceded by the next
                    // distinct field label. This prevents
                    // selecting an unrelated control below.
                    const between = all.filter(el => {
                        const afterLabel =
                            !!(label.compareDocumentPosition(el) &
                              Node.DOCUMENT_POSITION_FOLLOWING);
                        const beforeButton =
                            !!(el.compareDocumentPosition(button) &
                              Node.DOCUMENT_POSITION_FOLLOWING);

                        return afterLabel && beforeButton;
                    });

                    const hasNextField = between.some(el => {
                        const tag = el.tagName;
                        if (![
                            'LABEL', 'LEGEND', 'H2', 'H3', 'H4'
                        ].includes(tag)) {
                            return false;
                        }

                        const value = text(el).toLowerCase();

                        return value &&
                            value !== 'cover letter' &&
                            value !== 'attach' &&
                            value !== 'dropbox' &&
                            value !== 'google drive' &&
                            value !== 'enter manually';
                    });

                    if (hasNextField) continue;

                    candidates.push(button);
                }
            }

            const unique = [...new Set(candidates)];

            if (unique.length !== 1) {
                return {
                    found: false,
                    reason:
                        `Expected one cover-letter button, found ${unique.length}`
                };
            }

            unique[0].setAttribute(marker, 'true');

            return {
                found: true,
                reason: 'Cover-letter manual-entry button identified'
            };
        }"""
    )

    if not result["found"]:
        print(
            "Cover letter button detection:",
            result["reason"],
        )
        return None

    button = page.locator(
        f'[{BUTTON_MARKER}="true"]'
    )

    return button if button.count() == 1 else None


def mark_cover_letter_editor(page, before_count):
    """Identify the editor shown after manual entry is opened."""

    result = page.evaluate(
        r"""(beforeCount) => {
            const marker = 'data-jobscout-cover-letter-editor';

            document.querySelectorAll(`[${marker}]`)
                .forEach(el => el.removeAttribute(marker));

            const visible = el =>
                !!el.getClientRects().length &&
                getComputedStyle(el).visibility !== 'hidden';

            const editors = Array.from(
                document.querySelectorAll(
                    'textarea, [contenteditable="true"]'
                )
            ).filter(visible);

            // Strongest signal: field name or id.
            const named = editors.filter(el => {
                const identity = [
                    el.id,
                    el.name,
                    el.getAttribute('aria-label'),
                    el.getAttribute('placeholder')
                ].filter(Boolean).join(' ').toLowerCase();

                return identity.includes('cover_letter') ||
                       identity.includes('cover-letter') ||
                       identity.includes('cover letter') ||
                       identity.includes('coverletter');
            });

            if (named.length === 1) {
                named[0].setAttribute(marker, 'true');
                return {
                    found: true,
                    strategy: 'editor identity'
                };
            }

            // Usually Greenhouse adds one textarea after
            // clicking "Enter manually".
            if (editors.length === beforeCount + 1) {
                const newEditor = editors[editors.length - 1];
                newEditor.setAttribute(marker, 'true');

                return {
                    found: true,
                    strategy: 'newly revealed editor'
                };
            }

            // Fallback: editor after Cover Letter label,
            // before the next field's label.
            const labelElements = Array.from(
                document.querySelectorAll(
                    'label, legend, strong, h2, h3, h4, div, span'
                )
            ).filter(el => (
                el.textContent || ''
            ).replace(/\s+/g, ' ').trim().toLowerCase()
                === 'cover letter');

            for (const label of labelElements) {
                const following = editors.filter(editor =>
                    !!(label.compareDocumentPosition(editor) &
                       Node.DOCUMENT_POSITION_FOLLOWING)
                );

                if (following.length === 1) {
                    following[0].setAttribute(marker, 'true');

                    return {
                        found: true,
                        strategy: 'following Cover Letter label'
                    };
                }
            }

            return {
                found: false,
                count: editors.length,
                beforeCount
            };
        }""",
        before_count,
    )

    if not result["found"]:
        print(
            "Could not uniquely identify the cover-letter editor.",
            result,
        )
        return None

    editor = page.locator(
        f'[{EDITOR_MARKER}="true"]'
    )

    return editor if editor.count() == 1 else None


def generate_cover_letter(job, profile, experience):
    """Generate a tailored, evidence-grounded cover letter."""

    company = job.get("company") or "the company"
    title = job.get("title") or "Software Engineer"

    question = (
        f"Write a compelling cover letter for the "
        f"{title} role at {company}. "
        "Start with a strong, memorable, natural hook. "
        "Show clear technical alignment with the role, "
        "using concrete evidence from the candidate's "
        "professional projects. "
        "Include the candidate's production-deployed "
        "AI Material Validation Agent at Cimpress when relevant. "
        "Explain genuine interest using the supplied job "
        "description and verified company context. "
        "Aim for approximately 220 to 320 words. "
        "Use natural paragraphs, first-person language, "
        "and a professional closing. "
        "Do not invent achievements or company facts."
    )

    result = semantic_answer(
        question=question,
        options=[],
        facts=candidate_facts(
            profile,
            experience,
        ),
        job=job,
        writing_preferences=experience.get(
            "writing_preferences", {}
        ),
        application_strategies=experience.get(
            "application_strategies", {}
        ),
    )

    if not result.answer:
        print(
            "Cover letter generation failed:",
            result.reason,
        )
        return None

    return result.answer.strip()


def fill_cover_letter(page, job, profile, experience):
    """Open Greenhouse manual entry and fill generated text."""

    print("\nLocating Cover Letter manual entry...")

    button = find_manual_entry_button(page)

    if button is None:
        print(
            "Unable to locate the Cover Letter "
            "'Enter manually' button."
        )
        return False

    before_count = page.locator(
        'textarea:visible, [contenteditable="true"]:visible'
    ).count()

    print("Clicking Cover Letter -> Enter manually")

    try:
        button.click(timeout=6000)
    except Exception as exc:
        print(
            "Cover-letter button click failed:",
            type(exc).__name__,
            str(exc)[:200],
        )
        return False

    editor = None

    for _ in range(15):
        page.wait_for_timeout(250)

        editor = mark_cover_letter_editor(
            page,
            before_count,
        )

        if editor is not None:
            break

    if editor is None:
        print(
            "Manual entry opened, but the editor "
            "could not be identified."
        )
        return False

    is_contenteditable = (
        editor.get_attribute("contenteditable") == "true"
    )

    if is_contenteditable:
        existing = editor.inner_text().strip()
    else:
        existing = editor.input_value().strip()

    if existing:
        print(
            "Cover letter already contains text. "
            "Preserving existing content."
        )
        return True

    print(
        f"Generating cover letter for "
        f"{job.get('company')} — {job.get('title')}"
    )

    try:
        letter = generate_cover_letter(
            job,
            profile,
            experience,
        )
    except Exception as exc:
        print(
            "Cover letter generation error:",
            type(exc).__name__,
            str(exc)[:250],
        )
        return False

    if not letter:
        return False

    try:
        editor.fill(letter, timeout=10000)

        actual = (
            editor.inner_text()
            if is_contenteditable
            else editor.input_value()
        )

        if actual.strip() != letter.strip():
            print(
                "Cover letter was entered, but verification "
                "did not match the generated text."
            )
            return False

    except Exception as exc:
        print(
            "Cover letter fill error:",
            type(exc).__name__,
            str(exc)[:250],
        )
        return False

    print("\nCover letter successfully entered.")
    print("-" * 65)
    print(letter)
    print("-" * 65)
    print("Review it in Chromium before submitting.")

    return True
