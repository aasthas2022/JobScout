
"""JobScout Greenhouse application runner.

Features:
- Standard profile autofill
- AI-powered application question answering
- Experience-grounded answers
- Hook-first narrative drafting
- Automatic cover letter generation
- Greenhouse "Enter manually" cover letter support
- Grouped radio question handling
- Human review before submission
- Approved answer memory
- Gmail verification retrieval
- macOS clipboard support
- Multiple applications per run
- Duplicate submission protection
"""

import argparse
import json
import subprocess
import sys
import time

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

# Load .env before initializing any AI clients.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(
    PROJECT_ROOT / ".env",
    override=False,
)

from .browser import (
    get_questions,
    visible_fields,
    already_filled,
    dropdown_options,
    fill_field,
)

from ..group_fields import (
    extract_groups,
    choose_single,
    completed,
)

from ..group_answers import resolve_group
from ..memory import AnswerMemory, sensitive
from ..question_resolver import QuestionResolver
from ..cover_letter import fill_cover_letter


# ==========================================================
# Helpers
# ==========================================================

def ask(message):
    return input(f"{message}: ").strip()


def field_label(field):
    label = str(
        field.get("label") or ""
    ).strip()

    context = str(
        field.get("context") or ""
    ).strip()

    if (
        label.lower() in (
            "select",
            "select...",
            "choose",
            "choose...",
        )
        or label.lower().startswith("field ")
    ):
        return context or label

    return label or context


def is_cover_letter_field(field):
    """Identify cover letter fields without confusing resumes."""

    values = [
        field.get("label"),
        field.get("context"),
        field.get("name"),
        field.get("id"),
    ]

    combined = " ".join(
        str(value or "")
        for value in values
    ).lower()

    normalized = combined.replace(
        "_", " "
    ).replace("-", " ")

    return (
        "cover letter" in normalized
        or "coverletter" in normalized
    )


def options_for(page, field):
    if field.get("tag") == "select":
        return field.get("options", [])

    if field.get("type") == "combobox":
        return dropdown_options(
            page,
            field,
        )

    return []


# ==========================================================
# Application History
# ==========================================================

def record(path, job, status):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    event = {
        "timestamp": datetime.now(
            timezone.utc
        ).isoformat(),
        "company": job.get("company"),
        "title": job.get("title"),
        "job_url": job.get("job_url"),
        "status": status,
    }

    with path.open(
        "a",
        encoding="utf-8",
    ) as handle:
        handle.write(
            json.dumps(
                event,
                ensure_ascii=False,
            ) + "\n"
        )


def application_history(path):
    history = {}

    if not path.exists():
        return history

    for line in path.read_text(
        encoding="utf-8"
    ).splitlines():
        try:
            entry = json.loads(line)
        except (ValueError, TypeError):
            continue

        url = entry.get("job_url")
        status = entry.get("status")

        if url and status:
            history[url] = status

    return history


def load_jobs(path, limit):
    jobs = []
    seen = set()

    for line in path.read_text(
        encoding="utf-8"
    ).splitlines():

        if not line.strip():
            continue

        try:
            job = json.loads(line)
        except (ValueError, TypeError):
            continue

        url = job.get("job_url") or ""
        parsed = urlparse(url)

        if not (
            parsed.scheme == "https"
            and parsed.hostname
        ):
            continue

        source = str(
            job.get("source") or "greenhouse"
        ).lower()

        if source != "greenhouse":
            continue

        if url in seen:
            continue

        seen.add(url)
        jobs.append(job)

        if limit > 0 and len(jobs) >= limit:
            break

    return jobs


# ==========================================================
# Form State
# ==========================================================

def form_state(page):
    """Count grouped questions once, not once per option."""

    groups = extract_groups(page)
    fields = visible_fields(page)

    group_names = {
        str(group.get("name"))
        for group in groups
        if group.get("name")
    }

    filled = 0
    required = []
    optional = []

    for field in fields:
        kind = str(
            field.get("type") or ""
        ).lower()

        name = str(
            field.get("name") or ""
        )

        if kind == "radio":
            continue

        if (
            kind == "checkbox"
            and name in group_names
        ):
            continue

        label = field_label(field)

        try:
            is_filled = already_filled(
                page,
                field,
            )
        except Exception:
            is_filled = False

        if is_filled:
            filled += 1

        elif field.get("required"):
            required.append(label)

        else:
            optional.append(label)

    for group in groups:
        label = (
            group.get("question")
            or "Unidentified choice group"
        )

        if completed(group):
            filled += 1

        elif (
            group.get("required")
            or not group.get("identified")
        ):
            required.append(label)

        else:
            optional.append(label)

    required = list(
        dict.fromkeys(
            value for value in required
            if value
        )
    )

    optional = list(
        dict.fromkeys(
            value for value in optional
            if value
        )
    )

    return {
        "filled": filled,
        "required": required,
        "optional": optional,
        "groups": groups,
    }


def print_summary(page):
    state = form_state(page)

    print("\n" + "=" * 65)
    print("APPLICATION SUMMARY")
    print("=" * 65)

    print(
        "Filled controls/groups:",
        state["filled"],
    )

    print(
        "Required unanswered:",
        len(state["required"]),
    )

    print(
        "Optional unanswered:",
        len(state["optional"]),
    )

    if state["required"]:
        print(
            "\nQUESTIONS REQUIRING REVIEW:"
        )

        for index, question in enumerate(
            state["required"],
            1,
        ):
            print(
                f"{index}. {question}"
            )

    return state


# ==========================================================
# Standard Autofill
# ==========================================================

def autofill_standard(
    page,
    job,
    profile,
    experience,
    resume,
    memory,
    use_agent,
):
    resolver = QuestionResolver(
        profile=profile,
        memory=memory,
        use_agent=use_agent,
        experience=experience,
    )

    attempts = {}

    for pass_number in range(3):
        progress = 0

        print(
            f"\nStandard autofill pass "
            f"{pass_number + 1}"
        )

        fields = visible_fields(page)

        for field in fields:
            key = field.get("control_id")

            kind = str(
                field.get("type") or ""
            ).lower()

            if not key:
                continue

            # Radio and checkbox controls are handled
            # separately or left for human review.
            if kind in (
                "radio",
                "checkbox",
            ):
                continue

            # Cover letters have their own workflow.
            # Never attempt to upload the resume as
            # a cover letter.
            if is_cover_letter_field(field):
                continue

            try:
                if already_filled(
                    page,
                    field,
                ):
                    continue
            except Exception:
                pass

            if attempts.get(key, 0) >= 2:
                continue

            attempts[key] = (
                attempts.get(key, 0) + 1
            )

            question = field_label(field)

            try:
                resolution = resolver.resolve(
                    item=field,
                    options=options_for(
                        page,
                        field,
                    ),
                    resume=resume,
                    job=job,
                )

            except Exception as exc:
                print(
                    f"Resolver error: {question}"
                )
                print(
                    "  Error:",
                    type(exc).__name__,
                    str(exc)[:250],
                )
                continue

            if resolution.answer is None:
                print(
                    f"Review: {question}"
                )

                if resolution.reason:
                    print(
                        "  Reason:",
                        resolution.reason,
                    )

                continue

            answer = resolution.answer

            try:
                success = fill_field(
                    page,
                    field,
                    answer,
                )

            except Exception as exc:
                success = False

                print(
                    "Fill error:",
                    question,
                    type(exc).__name__,
                    str(exc)[:150],
                )

            if success:
                progress += 1

                if kind == "file":
                    preview = "[document]"

                else:
                    preview = str(answer)

                    if len(preview) > 200:
                        preview = (
                            preview[:200]
                            + "..."
                        )

                print(
                    f"Filled: {question}"
                )
                print(
                    f"  Source: "
                    f"{resolution.source}"
                )
                print(
                    f"  Answer: {preview}"
                )

            else:
                print(
                    f"Could not verify: "
                    f"{question}"
                )

        if progress == 0:
            break

        page.wait_for_timeout(400)


# ==========================================================
# Cover Letter Autofill
# ==========================================================

def autofill_cover_letter(
    page,
    job,
    profile,
    experience,
    use_agent,
):
    """Generate and enter a tailored cover letter."""

    if not use_agent:
        print(
            "\nCover letter generation skipped: "
            "AI agent disabled."
        )
        return False

    print("\n" + "=" * 65)
    print("COVER LETTER")
    print("=" * 65)

    print(
        "Company:",
        job.get("company"),
    )

    print(
        "Role:",
        job.get("title"),
    )

    try:
        success = fill_cover_letter(
            page=page,
            job=job,
            profile=profile,
            experience=experience,
        )

        if success:
            print(
                "Cover letter entered successfully."
            )
            print(
                "Please review it before submitting."
            )

        else:
            print(
                "Cover letter requires manual review."
            )

        return success

    except Exception as exc:
        print(
            "Cover letter error:",
            type(exc).__name__,
            str(exc)[:350],
        )

        return False


# ==========================================================
# Grouped Questions
# ==========================================================

def autofill_groups(
    page,
    job,
    profile,
    experience,
    memory,
    use_agent,
):
    groups = extract_groups(page)

    print(
        "\nDetected choice groups:",
        len(groups),
    )

    for group in groups:
        if completed(group):
            continue

        question = (
            group.get("question")
            or "Unidentified group"
        )

        try:
            answer, source = resolve_group(
                group=group,
                profile=profile,
                memory=memory,
                use_agent=use_agent,
                experience=experience,
                job=job,
            )

        except Exception as exc:
            print(
                "Group resolver error:",
                question,
                type(exc).__name__,
            )
            continue

        if answer is None:
            print(
                f"Review group: {question}"
            )
            print(
                f"  Reason: {source}"
            )
            continue

        try:
            success = choose_single(
                page,
                group,
                answer,
            )

        except Exception:
            success = False

        if success:
            print(
                f"Filled group: {question}"
            )
            print(
                f"  Answer: {answer}"
            )
            print(
                f"  Source: {source}"
            )

        else:
            print(
                "Could not verify group:",
                question,
            )


# ==========================================================
# Capture Answers
# ==========================================================

def capture_answers(page):
    answers = {}

    for field in visible_fields(page):
        label = field_label(field)
        key = field.get("control_id")

        kind = str(
            field.get("type") or ""
        ).lower()

        if (
            not label
            or not key
            or sensitive(label)
        ):
            continue

        if is_cover_letter_field(field):
            continue

        if kind in (
            "radio",
            "checkbox",
            "file",
            "password",
            "hidden",
            "submit",
            "button",
        ):
            continue

        try:
            value = page.evaluate(
                """(info) => {
                    const elements = Array.from(
                        document.querySelectorAll(
                            '[data-jobscout-control]'
                        )
                    );

                    let element = elements.find(
                        el => el.getAttribute(
                            'data-jobscout-control'
                        ) === String(info.key)
                    );

                    if (!element && info.id) {
                        element = document.getElementById(
                            info.id
                        );
                    }

                    if (!element) return null;

                    if (
                        element.getAttribute('role')
                        === 'combobox'
                    ) {
                        let parent = element.parentElement;

                        for (
                            let i = 0;
                            i < 7 && parent;
                            i++
                        ) {
                            const selected =
                                parent.querySelector(
                                    '[class*="singleValue"]'
                                );

                            if (selected) {
                                return (
                                    selected.textContent
                                    || ''
                                ).trim();
                            }

                            parent = parent.parentElement;
                        }

                        return null;
                    }

                    if (element.tagName === 'SELECT') {
                        const option =
                            element.selectedOptions[0];

                        return option
                            ? option.textContent.trim()
                            : null;
                    }

                    return (
                        element.value || ''
                    ).trim();
                }""",
                {
                    "key": key,
                    "id": field.get("id"),
                },
            )

        except Exception:
            continue

        if (
            isinstance(value, str)
            and value.strip()
        ):
            answers[label] = value.strip()

    for group in extract_groups(page):
        label = str(
            group.get("question") or ""
        ).strip()

        if not group.get("identified"):
            continue

        if sensitive(label):
            continue

        if (
            group.get("type")
            != "single_choice"
        ):
            continue

        selected = group.get(
            "selected", []
        )

        if len(selected) == 1:
            answers[label] = selected[0]

    return answers


# ==========================================================
# Reusable Answer Memory
# ==========================================================

def learn_answers(
    memory,
    before,
    after,
):
    candidates = []

    for question, answer in after.items():
        if sensitive(question):
            continue

        # Company-specific narrative answers should
        # be regenerated for each application.
        question_lower = question.lower()

        if any(
            phrase in question_lower
            for phrase in (
                "cover letter",
                "why do you want to work",
                "why our company",
                "why this company",
                "why are you interested in us",
            )
        ):
            continue

        if before.get(question) == answer:
            continue

        existing = memory.get(
            question,
            allow_similar=False,
        )

        if existing == answer:
            continue

        candidates.append(
            (question, answer)
        )

    if not candidates:
        print(
            "\nNo new reusable answers detected."
        )
        return

    print("\n" + "=" * 65)
    print("APPROVE REUSABLE ANSWERS")
    print("=" * 65)

    for index, (question, answer) in enumerate(
        candidates,
        1,
    ):
        print(
            f"\n{index}. {question}"
        )
        print(
            f"   Answer: {answer[:350]}"
        )

    selection = ask(
        "Save [all/none/1,3,...]"
    ).lower()

    if selection == "all":
        selected = set(
            range(len(candidates))
        )

    elif selection in (
        "",
        "none",
        "skip",
        "no",
    ):
        selected = set()

    else:
        selected = set()

        for part in selection.split(","):
            part = part.strip()

            if part.isdigit():
                index = int(part) - 1

                if (
                    0 <= index
                    < len(candidates)
                ):
                    selected.add(index)

    count = 0

    for index in sorted(selected):
        question, answer = candidates[index]

        if memory.put(
            question,
            answer,
        ):
            count += 1

    if count:
        memory.save()

    print(
        f"Saved {count} approved answers."
    )


# ==========================================================
# Submission
# ==========================================================

def submit_control(page):
    selectors = (
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Submit Application")',
        'button:has-text("Submit")',
    )

    for selector in selectors:
        matches = page.locator(
            selector
        )

        visible = [
            matches.nth(i)
            for i in range(matches.count())
            if matches.nth(i).is_visible()
        ]

        if len(visible) == 1:
            return visible[0]

    return None


def verification_required(page):
    try:
        body = page.locator(
            "body"
        ).inner_text(
            timeout=5000
        ).lower()

        return (
            "a verification code was sent" in body
            or "enter the 8-character code" in body
        )

    except Exception:
        return False


def submission_confirmation(page):
    try:
        body = page.locator(
            "body"
        ).inner_text(
            timeout=5000
        ).lower()

        phrases = (
            "application has been submitted",
            "application was submitted",
            "thank you for applying",
            "application submitted successfully",
            "your application has been received",
        )

        return any(
            phrase in body
            for phrase in phrases
        )

    except Exception:
        return False


# ==========================================================
# Gmail Verification
# ==========================================================

def copy_code(code):
    if sys.platform != "darwin":
        return False

    try:
        subprocess.run(
            ["pbcopy"],
            input=code,
            text=True,
            check=True,
            timeout=5,
        )

        return True

    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ):
        return False


def retrieve_code(company, args):
    print(
        "\nGREENHOUSE EMAIL VERIFICATION"
    )

    print(
        f"Waiting {args.gmail_delay} seconds "
        "before checking Gmail..."
    )

    time.sleep(
        args.gmail_delay
    )

    try:
        from ..gmail_reader import wait_for_code

        code = wait_for_code(
            company=company,
            minutes=args.gmail_minutes,
            wait_seconds=args.gmail_wait,
            poll_interval=5,
            verbose=True,
        )

        if not code:
            print(
                "No matching code found."
            )
            return False

        if copy_code(code):
            print(
                "Code copied to clipboard."
            )
            print(
                "Paste it into Greenhouse "
                "using Cmd+V."
            )

        else:
            print(
                "Code:", code
            )
            print(
                "Enter the code manually."
            )

        return True

    except Exception as exc:
        print(
            "Gmail error:",
            type(exc).__name__,
            str(exc)[:250],
        )

        return False


def submit_application(
    page,
    company,
    args,
):
    choice = ask(
        "Press Enter to SUBMIT "
        "(type skip to cancel)"
    )

    if choice:
        return "ready_for_review"

    button = submit_control(page)

    if (
        button is None
        or not button.is_enabled()
    ):
        print(
            "No enabled Submit control found."
        )
        return "needs_review"

    try:
        button.click(
            timeout=10000
        )

    except Exception as exc:
        print(
            "Submit click error:",
            str(exc)[:250],
        )

        return "submission_unverified"

    page.wait_for_timeout(
        1800
    )

    if verification_required(page):
        retrieve_code(
            company,
            args,
        )

        print(
            "Complete verification and final "
            "submission manually in Chromium."
        )

        ask(
            "Press Enter after finishing "
            "verification and submission"
        )

        page.wait_for_timeout(
            500
        )

        if (
            submission_confirmation(page)
            and not verification_required(page)
        ):
            return "submitted_verified"

        confirmed = ask(
            "Did Greenhouse explicitly confirm "
            "submission? [yes/no]"
        ).lower()

        return (
            "submitted_verified"
            if confirmed == "yes"
            else "verification_pending"
        )

    if submission_confirmation(page):
        return "submitted_verified"

    confirmed = ask(
        "Did the website explicitly confirm "
        "submission? [yes/no]"
    ).lower()

    return (
        "submitted_verified"
        if confirmed == "yes"
        else "submission_unverified"
    )


# ==========================================================
# Process One Application
# ==========================================================

def process_job(
    context,
    job,
    profile,
    experience,
    resume,
    memory,
    args,
):
    page = context.new_page()
    status = "needs_review"

    print("\n" + "=" * 65)
    print("Company:", job.get("company"))
    print("Role:", job.get("title"))
    print("URL:", job.get("job_url"))

    try:
        metadata = get_questions(
            job["job_url"]
        )

        print(
            "Greenhouse API questions:",
            len(metadata),
        )

        page.goto(
            job["job_url"],
            wait_until="domcontentloaded",
            timeout=45000,
        )

        page.wait_for_timeout(
            1200
        )

        if not page.locator(
            'input[id="first_name"],'
            'input[name="first_name"]'
        ).count():

            print(
                "Open the application form "
                "in Chromium."
            )

            ask(
                "Press Enter when the form "
                "is ready"
            )

        # --------------------------------------------------
        # 1. Regular profile fields
        # --------------------------------------------------

        autofill_standard(
            page=page,
            job=job,
            profile=profile,
            experience=experience,
            resume=resume,
            memory=memory,
            use_agent=args.agent,
        )

        # --------------------------------------------------
        # 2. Generate and enter the cover letter
        # --------------------------------------------------

        autofill_cover_letter(
            page=page,
            job=job,
            profile=profile,
            experience=experience,
            use_agent=args.agent,
        )

        # --------------------------------------------------
        # 3. Grouped choice questions
        # --------------------------------------------------

        autofill_groups(
            page=page,
            job=job,
            profile=profile,
            experience=experience,
            memory=memory,
            use_agent=args.agent,
        )

        # --------------------------------------------------
        # 4. Review
        # --------------------------------------------------

        print_summary(page)

        before = capture_answers(page)

        print(
            "\nReview all generated answers "
            "in Chromium."
        )

        print(
            "Check the cover letter, technical "
            "claims, company details, eligibility, "
            "and legal questions."
        )

        ask(
            "Press Enter after completing review"
        )

        after = capture_answers(page)

        learn_answers(
            memory,
            before,
            after,
        )

        state = print_summary(page)

        if state["required"]:
            print(
                "Required questions remain. "
                "Submission blocked."
            )

            status = "needs_review"
            return status

        # --------------------------------------------------
        # 5. Optional submission
        # --------------------------------------------------

        if not args.allow_submit:
            print(
                "DRY RUN — not submitted."
            )

            status = "ready_for_review"
            return status

        status = submit_application(
            page=page,
            company=job.get("company", ""),
            args=args,
        )

        return status

    except Exception as exc:
        print(
            "Application error:",
            type(exc).__name__,
            str(exc)[:450],
        )

        status = "error"
        return status

    finally:
        record(
            args.log,
            job,
            status,
        )

        ask(
            "Press Enter to close this job page"
        )

        page.close()


# ==========================================================
# CLI
# ==========================================================

def main():
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--queue",
        type=Path,
        default=Path(
            "output/greenhouse_queue.jsonl"
        ),
    )

    parser.add_argument(
        "--profile",
        type=Path,
        default=Path(
            "application_private/profile.json"
        ),
    )

    parser.add_argument(
        "--experience",
        type=Path,
        default=Path(
            "application_private/experience_profile.json"
        ),
    )

    parser.add_argument(
        "--resume",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--memory",
        type=Path,
        default=Path(
            "application_private/answers.json"
        ),
    )

    parser.add_argument(
        "--log",
        type=Path,
        default=Path(
            "application_private/applications.jsonl"
        ),
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--agent",
        action="store_true",
    )

    parser.add_argument(
        "--allow-submit",
        action="store_true",
    )

    parser.add_argument(
        "--gmail-delay",
        type=int,
        default=15,
    )

    parser.add_argument(
        "--gmail-wait",
        type=int,
        default=120,
    )

    parser.add_argument(
        "--gmail-minutes",
        type=int,
        default=15,
    )

    args = parser.parse_args()

    if (
        args.gmail_delay < 0
        or args.gmail_wait < 0
        or args.gmail_minutes < 1
    ):
        parser.error(
            "Invalid Gmail timing settings"
        )

    required_files = (
        args.queue,
        args.profile,
        args.experience,
        args.resume,
    )

    for path in required_files:
        if not path.is_file():
            parser.error(
                f"Missing file: {path}"
            )

    profile = json.loads(
        args.profile.read_text(
            encoding="utf-8"
        )
    )

    experience = json.loads(
        args.experience.read_text(
            encoding="utf-8"
        )
    )

    memory = AnswerMemory(
        args.memory
    )

    jobs = load_jobs(
        args.queue,
        args.limit,
    )

    history = application_history(
        args.log
    )

    print(
        "Applications queued:",
        len(jobs),
    )

    print(
        "Experience projects loaded:",
        len(
            experience.get(
                "projects", []
            )
        ),
    )

    print(
        "AI agent:",
        "enabled" if args.agent
        else "disabled",
    )

    print(
        "Submission:",
        "enabled with manual approval"
        if args.allow_submit
        else "dry run",
    )

    if not jobs:
        return

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=False,
            slow_mo=60,
        )

        try:
            context = browser.new_context()

            for job in jobs:
                url = job["job_url"]
                previous = history.get(url)

                if previous == "submitted_verified":
                    print(
                        "Skipping submitted:",
                        url,
                    )
                    continue

                if previous in (
                    "verification_pending",
                    "submission_unverified",
                ):
                    print(
                        "Previous attempt uncertain:",
                        previous,
                    )

                    retry = ask(
                        "Confirmed NOT submitted "
                        "and want to retry? [yes/no]"
                    ).lower()

                    if retry != "yes":
                        continue

                status = process_job(
                    context=context,
                    job=job,
                    profile=profile,
                    experience=experience,
                    resume=args.resume.resolve(),
                    memory=memory,
                    args=args,
                )

                history[url] = status

        finally:
            browser.close()


if __name__ == "__main__":
    main()
