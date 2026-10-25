"""Ashby-only autofill, inference, human review, and verified submission."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import browser
from .answers import classify, factual_answer, get, label, norm, option_match, safe_memory_answer, yes_no
from .inference import declaration, infer
from .approved_answers import ApprovedAnswerBank, reusable
from ..memory import sensitive
from ..question_resolver import QuestionResolver, candidate_facts, semantic_answer


def ask(prompt):
    return input(prompt + ": ").strip()


def record(path, job, status):
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(), "company": job.get("company"),
             "title": job.get("title"), "job_url": job.get("job_url"), "status": status}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")


def approved_memory(memory, question, bank=None):
    if not question:
        return None
    if bank is not None:
        saved = bank.get(question)
        if saved is not None and safe_memory_answer(question, saved):
            return saved
    if sensitive(question):
        return None
    value = memory.get(question, allow_similar=False)
    return value if safe_memory_answer(question, value) else None


def stored_fact(memory, field, bank=None):
    q = label(field)
    # A remembered value for an employer-dependent or changing question is unsafe.
    if any(x in norm(q) for x in ("salary", "compensation", "date", "post secondary education", "degree obtained")):
        return None
    return approved_memory(memory, q, bank)


def redact(question, answer):
    q = norm(question)
    if any(x in q for x in ("address", "phone", "email")):
        return "[private value]"
    return str(answer)[:100]


def generate_letter(job, profile, experience):
    result = semantic_answer(
        question=f"Draft a tailored 220-320 word cover letter for {job.get('title', 'this role')} at "
                 f"{job.get('company', 'this company')}. Begin with a strong natural hook and use "
                 "confirmed engineering evidence. Preserve paragraphs; do not invent accomplishments.",
        options=[], facts=candidate_facts(profile, experience), job=job,
        writing_preferences=experience.get("writing_preferences", {}),
        application_strategies=experience.get("application_strategies", {}),
    )
    return result.answer.strip() if result.answer else None


def is_general_note(question):
    q = norm(question)
    return any(term in q for term in (
        "additional information", "additional comments", "additional details",
        "comments", "comment",
        "anything else", "a note you want to share", "note you want to share",
        "note to the hiring team", "note to the recruiter",
        "message to the hiring team", "message to the recruiter",
        "anything else we should know",
    ))


def generate_general_note(job, profile, experience):
    """Write a short, tailored cover-letter-style answer for optional notes."""
    result = semantic_answer(
        question=(
            f"Write a standalone, 100-150 word answer for the optional "
            f"'Additional information or note' field of the "
            f"{job.get('title', 'engineering')} job application at "
            f"{job.get('company', 'the company')}. "
            "Open with one engaging, specific line tied to this role or "
            "the job description, then include one concrete, verified "
            "engineering example relevant to this job. Connect it to "
            "what you would contribute. Sound natural, confident, and "
            "human; do not write a formal salutation, signature, or "
            "generic cover-letter boilerplate. Never invent company "
            "details, technology experience, numbers, or achievements. "
            "Use short paragraphs."
        ),
        options=[],
        facts=candidate_facts(profile, experience),
        job=job,
        writing_preferences=experience.get('writing_preferences', {}),
        application_strategies=experience.get('application_strategies', {}),
    )
    return result.answer.strip() if result.answer else None


def generate_achievement_answer(question, job, profile, experience):
    """Ground achievement narratives in candidate evidence, never invent metrics."""
    prompt = (
        f"Answer this job application question for {job.get('title', 'the role')} "
        f"at {job.get('company', 'the company')}: {question} "
        "Write in first person, directly answering what I personally contributed, "
        "how the system worked, and the real outcome. Prefer the documented "
        "production AI Material Validation Agent if it is relevant and supported "
        "by the provided candidate facts. Use only supported technical specifics. "
        "If there is no verified numerical metric, explicitly describe the actual "
        "non-numerical outcome (such as production deployment) without claiming "
        "a fabricated numerical impact. Do not invent leadership, exact metrics, "
        "performance results, or credentials. Write approximately 140-220 words "
        "in clear natural paragraphs without a greeting or signature."
    )
    result = semantic_answer(
        question=prompt,
        options=[],
        facts=candidate_facts(profile, experience),
        job=job,
        writing_preferences=experience.get("writing_preferences", {}),
        application_strategies=experience.get("application_strategies", {}),
    )
    return result.answer.strip() if result.answer else None


def is_achievement_question(question):
    q = norm(question)
    return any(x in q for x in (
        "most impressive thing", "personally built", "built or automated",
        "measurable outcome", "measurable impact", "most impactful thing",
        "most challenging project", "what have you built",
        "describe a system", "biggest technical accomplishment",
    ))


def fill_cover_letter(page, job, profile, experience):
    fields = [f for f in browser.prepare(page) if "cover letter" in norm(" ".join(
        (f.get("label", ""), f.get("name", ""), f.get("id", "")))) and not browser.filled(page, f)]
    if not fields:
        return
    try:
        letter = generate_letter(job, profile, experience)
    except Exception as exc:
        print("Cover letter generation failed:", type(exc).__name__)
        return
    if not letter:
        return
    for field in fields:
        answer = letter
        if field["type"] == "file":
            directory = Path("output/cover_letters")
            directory.mkdir(parents=True, exist_ok=True)
            name = re.sub(r"[^a-zA-Z0-9_-]+", "_", f"{job.get('company','company')}_{job.get('title','role')}")[:90]
            path = directory / f"{name}_cover_letter.txt"
            path.write_text(letter + "\n", encoding="utf-8")
            answer = str(path.resolve())
        try:
            if browser.fill(page, field, answer):
                print("Cover letter drafted for review")
        except Exception as exc:
            print("Cover letter fill requires review:", type(exc).__name__)


def declared_answer(group, profile, memory, bank=None):
    """Resolve explicit eligibility facts and previously approved preferences."""
    question = group.get("question", "")
    q = norm(question)
    if not group.get("identified"):
        return None, "unknown_question"
    if any(x in q for x in ("acknowledg", "consent", "certify", "attest", "signature")):
        return None, "attestation_requires_personal_action"

    # Exact approved answers are permitted for ordinary preference questions.
    saved = approved_memory(memory, question, bank)
    if any(x in q for x in ("sponsor", "sponsorship")) and not any(
        x in q for x in ("canada", "united kingdom", "uk", "europe", "india")
    ):
        value = yes_no(get(profile, "requires_sponsorship_us", "requires_visa_sponsorship", "requires_sponsorship"))
        return value or yes_no(saved), "profile_or_approved_memory"
    if any(x in q for x in ("authorized to work", "legally authorized", "work authorization")):
        value = yes_no(get(profile, "authorized_to_work_us", "us_work_authorization"))
        return value or yes_no(saved), "profile_or_approved_memory"
    if "relocat" in q:
        value = yes_no(get(profile, "willing_to_relocate", "relocation_willingness"))
        return yes_no(saved) or value, "profile_or_approved_memory"
    if any(x in q for x in ("office", "on site", "onsite", "days week", "hybrid")):
        pref = get(profile, "willing_to_work_onsite_4_days", "willing_to_work_onsite", "onsite_willingness")
        return yes_no(saved) or yes_no(pref), "approved_office_preference"
    if "security clearance" in q:
        return yes_no(get(profile, "security_clearance")) or yes_no(saved), "profile_or_approved_memory"
    if any(x in q for x in ("disability", "race", "gender", "ethnicity", "veteran")):
        preference = (profile.get("application_preferences") or {}).get("eeo_response")
        return preference or saved, "eeo_profile_or_approved_memory"
    return saved, "approved_memory"


def fill_groups(page, profile, memory, review_notes, bank=None):
    groups = browser.choice_groups(page)
    print("Ashby choice groups:", len(groups))
    for group in groups:
        if group.get("selected"):
            continue
        choice, source = declared_answer(group, profile, memory, bank)
        if not choice:
            review_notes.append((group.get("question", "Choice group"), source))
            continue
        matched = option_match(group.get("options", []), choice)
        if not matched:
            review_notes.append((group.get("question", "Choice group"), "No matching choice option"))
            continue
        try:
            if browser.select_choice(page, group, matched):
                print("Filled choice:", group.get("question"), f"[{source}]")
            else:
                review_notes.append((group.get("question", "Choice group"), "Choice not verified"))
        except Exception as exc:
            review_notes.append((group.get("question", "Choice group"), type(exc).__name__))


def form_state(page):
    if not browser.has_form(page):
        return ["Application form not detected"], []
    fields = browser.prepare(page)
    groups = browser.choice_groups(page)
    grouped = set()
    for group in groups:
        controls = page.locator(f'[data-jobscout-ashby-choice="{group["id"]}"]')
        grouped.update(controls.evaluate_all(
            "els => els.map(e => e.getAttribute('data-jobscout-ashby')).filter(Boolean)"
        ))
    required, optional = [], []
    for field in fields:
        if field["control_id"] in grouped or browser.filled(page, field):
            continue
        # Native radio controls are evaluated as a group when detected.
        target = required if field.get("required") else optional
        target.append(label(field) or "Unknown control")
    for group in groups:
        if not group.get("selected"):
            (required if group.get("required") else optional).append(
                group.get("question") or "Unanswered choice group"
            )
    # Do not trust HTML validity alone for custom segmented Yes/No controls.
    required.extend(browser.unresolved_segmented_questions(page))
    invalid = page.evaluate(r"""() => [...document.querySelectorAll('form input,form select,form textarea')]
        .filter(e => e.willValidate && !e.checkValidity() && e.type !== 'hidden')
        .map(e => e.getAttribute('aria-label') || e.name || e.id || 'Required field')""")
    required.extend(invalid)
    return list(dict.fromkeys(required)), list(dict.fromkeys(optional))


def capture(page):
    """Snapshot all reusable factual responses, including prefilled choices.

    Run AFTER final review so unchanged autofilled answers are also learned.
    """
    result = {}
    for field in browser.prepare(page):
        question = label(field)
        if not question or classify(field) != "fact" or field["type"] in ("file", "radio", "checkbox"):
            continue
        if browser.filled(page, field):
            try:
                value = browser.locator(page, field).input_value()
                if reusable(question, value):
                    result[question] = value
            except Exception:
                pass
    # Choice values are captured only when the DOM confirms a selection.
    for group in browser.choice_groups(page):
        question = group.get("question", "")
        selected = group.get("selected") or []
        if len(selected) == 1 and reusable(question, selected[0]):
            result[question] = selected[0]
    return result


def learn(memory, bank, after):
    """Save the entire approved form, not just changes made during review."""
    updated = [(q, v) for q, v in after.items() if bank.put(q, v)]
    if updated:
        bank.save()
    print(f"Approved answer bank: {len(updated)} new/updated reusable answers")
    # Existing shared memory is still READ, but only the private Ashby
    # bank is written automatically to avoid changing Greenhouse behavior.


def submission_confirmed(page):
    try:
        body = page.locator("body").inner_text(timeout=5000).lower()
        return any(x in body for x in ("thank you for applying", "thanks for applying",
                                        "application successfully submitted", "your application has been submitted",
                                        "application received", "application has been received")) or "/thanks" in page.url.lower()
    except Exception:
        return False


def submit(page, auto=False):
    if not auto and ask("Press Enter to SUBMIT (type skip to cancel)"):
        return "ready_for_review"
    remaining, _ = form_state(page)
    if remaining:
        print("Cannot submit: required fields remain:", remaining)
        return "needs_review"
    if browser.verification_present(page):
        print("Human verification is already active; finish it in Chromium.")
        input("Press Enter after verification: ")
        return "submitted_verified" if submission_confirmed(page) else "verification_pending"
    button = browser.find_submit(page)
    if button is None:
        print("No unique Ashby submit button; leaving for review")
        return "needs_review"
    try:
        button.click(timeout=10000)
        page.wait_for_timeout(1700)
    except Exception as exc:
        print("Submission uncertain:", type(exc).__name__)
        return "submission_unverified"
    for _ in range(5):
        if submission_confirmed(page):
            return "submitted_verified"
        if browser.verification_present(page):
            break
        page.wait_for_timeout(700)
    if browser.verification_present(page):
        print("Human verification required: complete it yourself in Chromium.")
        input("Press Enter after finishing verification: ")
        page.wait_for_timeout(1000)
        if submission_confirmed(page):
            return "submitted_verified"
        return "verification_pending"
    print("Submission not confirmed: check before retrying")
    return "submission_unverified"


def process_job(context, job, profile, experience, resume, memory, args):
    page = context.new_page()
    status = "needs_review"
    inferred = []
    bank = ApprovedAnswerBank(args.profile.parent / "ashby_approved_answers.json")
    print(f"Ashby approved answers loaded: {len(bank.values)}")
    try:
        url = job.get("apply_url") or job.get("job_url")
        if not browser.is_ashby_url(url):
            raise ValueError(f"Unsupported Ashby URL: {url}")
        print("\nASHBY", job.get("company"), "-", job.get("title"))
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(750)
        if not browser.open_form(page):
            print("Application form not detected; do not consider this job completed")
            return status
        resolver = QuestionResolver(profile=profile, memory=memory, use_agent=args.agent, experience=experience)
        attempted = set()
        for attempt in range(2):
            progress = 0
            print("Autofill pass", attempt + 1)
            for field in browser.prepare(page):
                if browser.filled(page, field):
                    continue
                kind, question = classify(field), label(field)
                if kind in ("choice", "cover"):
                    continue
                if kind == "manual":
                    # A legal statement can be filled only when an exact,
                    # explicitly approved profile answer exists. Never synthesize consent.
                    value = (profile.get("application_preferences") or {}).get("declaration_answers", {}).get(question)
                    if value is None:
                        continue
                    source = "confirmed_declaration"
                elif kind in ("fact", "file"):
                    value = stored_fact(memory, field, bank) if kind == "fact" else None
                    source = "approved_memory" if value is not None else "profile"
                    if value is None:
                        value = factual_answer(field, profile, experience, resume, job)
                    if value is None and kind == "fact" and args.agent and field["control_id"] not in attempted:
                        attempted.add(field["control_id"])
                        try:
                            guess = infer(question, profile, experience, job, field.get("options", []))
                            value = guess.answer
                            if value is not None:
                                source = "ai_inference_review"
                                inferred.append((question, guess.explanation))
                        except Exception as exc:
                            print("AI inference unavailable:", question, type(exc).__name__)
                else:
                    if not args.agent:
                        continue
                    if is_general_note(question):
                        try:
                            value = generate_general_note(job, profile, experience)
                            source = "ai_tailored_note_review"
                        except Exception as exc:
                            print("Optional-note drafting failed:", type(exc).__name__)
                            value, source = None, "note_generation_failed"
                    elif is_achievement_question(question):
                        try:
                            value = generate_achievement_answer(question, job, profile, experience)
                            source = "ai_achievement_draft_review"
                        except Exception as exc:
                            print("Achievement drafting failed:", type(exc).__name__)
                            value, source = None, "achievement_generation_failed"
                    else:
                        decision = resolver.resolve(item=field, options=field.get("options", []), resume=resume, job=job)
                        value, source = decision.answer, decision.source
                if value is None:
                    print("Review:", question, f"[{kind}]")
                    continue
                if field["tag"] == "select":
                    value = option_match(field["options"], value)
                    if value is None:
                        continue
                try:
                    if browser.is_searchable_location(page, field) or any(
                        x in norm(question) for x in ("which state", "province or region")
                    ):
                        success = browser.fill_searchable_dropdown(page, field, value)
                    else:
                        success = browser.fill(page, field, value)
                    if success:
                        progress += 1
                        print("Filled:", question, f"[{source}]", redact(question, value) if kind == "fact" else "")
                except Exception as exc:
                    print("Fill failed:", question, type(exc).__name__)
            if progress == 0:
                break
        if args.agent:
            fill_cover_letter(page, job, profile, experience)
        fill_groups(page, profile, memory, inferred, bank)
        required, optional = form_state(page)
        print("\nRequired unanswered:", len(required), "Optional unanswered:", len(optional))
        for question in required:
            print("REQUIRED:", question)
        if inferred:
            print("\nINFERENCES AND DECLARATIONS TO VERIFY:")
            for index, (q, reason) in enumerate(inferred, 1):
                print(f"{index}. {q} — {reason[:160]}")
        response = ask("Review all answers and legal declarations in Chromium; press Enter to approve this application (or type skip)")
        if response:
            status = "ready_for_review"
            return status
        after = capture(page)
        learn(memory, bank, after)
        required, _ = form_state(page)
        if required:
            print("Still incomplete:", required)
            return status
        if not args.allow_submit:
            print("DRY RUN — not submitted")
            status = "ready_for_review"
            return status
        status = submit(page, auto=args.auto_submit_reviewed)
        return status
    except Exception as exc:
        print("Ashby error:", type(exc).__name__, str(exc)[:320])
        status = "error"
        return status
    finally:
        record(args.log, job, status)
        if status != "submitted_verified":
            print("Application status:", status)
            ask("Press Enter to close this Ashby application")
        else:
            print("Verified submission; continuing to next job")
        page.close()
