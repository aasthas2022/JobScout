"""Lever-only autofill, inference, human review, and verified submission."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import browser
from .answers import classify, factual_answer, get, label, norm, option_match, safe_memory_answer, yes_no
from .inference import declaration, infer
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


def approved_memory(memory, question):
    if not question or sensitive(question):
        return None
    value = memory.get(question, allow_similar=False)
    return value if safe_memory_answer(question, value) else None


def stored_fact(memory, field):
    q = label(field)
    # A remembered value for an employer-dependent or changing question is unsafe.
    if any(x in norm(q) for x in ("salary", "compensation", "date", "post secondary education", "degree obtained")):
        return None
    return approved_memory(memory, q)


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


def declared_answer(group, profile, memory):
    q = norm(group.get("question"))
    if not group.get("identified"):
        return None, "unknown_question"
    if any(x in q for x in ("acknowledg", "consent", "certify", "attest", "signature")):
        return None, "attestation_requires_personal_action"
    # Eligibility never comes from LLM guesses.
    if "relocat" in q:
        return yes_no(get(profile, "willing_to_relocate", "relocation_willingness")) or yes_no(
            approved_memory(memory, group["question"])), "profile_or_approved_memory"
    if ("authorized to work" in q or "legally authorized" in q) and "united states" in q:
        return yes_no(get(profile, "authorized_to_work_us", "us_work_authorization")), "profile"
    if ("sponsor" in q or "sponsorship" in q) and "united states" in q:
        return yes_no(get(profile, "requires_sponsorship_us", "requires_visa_sponsorship", "requires_sponsorship")), "profile"
    if "security clearance" in q:
        return yes_no(get(profile, "security_clearance")), "profile"
    if any(x in q for x in ("disability", "race", "gender", "ethnicity", "veteran")):
        preference = (profile.get("application_preferences") or {}).get("eeo_response")
        if preference:
            return preference, "eeo_profile"
    return approved_memory(memory, group["question"]), "approved_memory"


def fill_groups(page, profile, memory, review_notes):
    groups = browser.choice_groups(page)
    print("Lever choice groups:", len(groups))
    for group in groups:
        if group.get("selected"):
            continue
        choice, source = declared_answer(group, profile, memory)
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
    fields, groups = browser.prepare(page), browser.choice_groups(page)
    grouped = set()
    for group in groups:
        controls = page.locator(f'[data-jobscout-lever-choice-group="{group["id"]}"]')
        grouped.update(controls.evaluate_all("els => els.map(e => e.getAttribute('data-jobscout-lever'))"))
    required, optional = [], []
    for field in fields:
        if field["control_id"] in grouped or browser.filled(page, field):
            continue
        target = required if field.get("required") else optional
        target.append(label(field) or "Unknown control")
    for group in groups:
        checked = page.locator(f'[data-jobscout-lever-choice-group="{group["id"]}"]').evaluate_all(
            "els => els.some(e => e.checked)")
        if not checked:
            (required if group.get("required") else optional).append(group.get("question") or "Choice group")
    invalid = page.evaluate("""() => [...document.querySelectorAll('form input,form select,form textarea')]
        .filter(e=>e.willValidate && !e.checkValidity() && e.type!=='hidden')
        .map(e=>e.getAttribute('aria-label') || e.name || e.id || 'Required field')""")
    required.extend(invalid)
    return list(dict.fromkeys(required)), list(dict.fromkeys(optional))


def capture(page):
    result = {}
    for field in browser.prepare(page):
        q = label(field)
        if not q or classify(field) != "fact" or sensitive(q) or field["type"] in ("file", "radio", "checkbox"):
            continue
        if browser.filled(page, field):
            try:
                result[q] = browser.locator(page, field).input_value()
            except Exception:
                pass
    return result


def learn(memory, before, after):
    candidates = [(q, v) for q, v in after.items() if v and before.get(q) != v and
                  not sensitive(q) and not declaration(q) and safe_memory_answer(q, v) and
                  memory.get(q, allow_similar=False) != v]
    if not candidates:
        return
    print("\nNew reusable factual answers:")
    for idx, (q, v) in enumerate(candidates, 1):
        print(f"{idx}. {q}: {redact(q, v)}")
    selection = ask("Save [all/none/1,3,...]").lower()
    indices = range(len(candidates)) if selection == "all" else [
        int(part.strip()) - 1 for part in selection.split(",") if part.strip().isdigit()]
    if selection in ("none", "", "no", "skip"):
        indices = []
    if sum(bool(memory.put(*candidates[idx])) for idx in set(indices) if 0 <= idx < len(candidates)):
        memory.save()


def submission_confirmed(page):
    try:
        body = page.locator("body").inner_text(timeout=5000).lower()
        return any(x in body for x in ("thank you for applying", "thanks for applying",
                                        "application successfully submitted", "your application has been submitted",
                                        "application received")) or "/thanks" in page.url.lower()
    except Exception:
        return False


def submit(page, auto=False):
    if not auto and ask("Press Enter to SUBMIT (type skip to cancel)"):
        return "ready_for_review"
    remaining, _ = form_state(page)
    if remaining:
        print("Cannot submit: required fields remain:", remaining)
        return "needs_review"
    button = browser.find_submit(page)
    if button is None:
        print("No unique Lever submit button; leaving for review")
        return "needs_review"
    try:
        button.click(timeout=10000)
        page.wait_for_timeout(1700)
    except Exception as exc:
        print("Submission uncertain:", type(exc).__name__)
        return "submission_unverified"
    if submission_confirmed(page):
        return "submitted_verified"
    print("Submission not confirmed: check before retrying")
    return "submission_unverified"


def process_job(context, job, profile, experience, resume, memory, args):
    page = context.new_page()
    status = "needs_review"
    inferred = []
    try:
        url = job.get("apply_url") or job.get("job_url")
        if not browser.is_lever_url(url):
            raise ValueError(f"Unsupported Lever URL: {url}")
        print("\nLEVER", job.get("company"), "-", job.get("title"))
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(750)
        if not browser.open_form(page):
            print("Application form not detected")
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
                    value = stored_fact(memory, field) if kind == "fact" else None
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
                    if browser.fill(page, field, value):
                        progress += 1
                        print("Filled:", question, f"[{source}]", redact(question, value) if kind == "fact" else "")
                except Exception as exc:
                    print("Fill failed:", question, type(exc).__name__)
            if progress == 0:
                break
        if args.agent:
            fill_cover_letter(page, job, profile, experience)
        fill_groups(page, profile, memory, inferred)
        required, optional = form_state(page)
        print("\nRequired unanswered:", len(required), "Optional unanswered:", len(optional))
        for question in required:
            print("REQUIRED:", question)
        if inferred:
            print("\nINFERENCES AND DECLARATIONS TO VERIFY:")
            for index, (q, reason) in enumerate(inferred, 1):
                print(f"{index}. {q} — {reason[:160]}")
        before = capture(page)
        response = ask("Review all answers and legal declarations in Chromium; press Enter to approve this application (or type skip)")
        if response:
            status = "ready_for_review"
            return status
        after = capture(page)
        learn(memory, before, after)
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
        print("Lever error:", type(exc).__name__, str(exc)[:320])
        status = "error"
        return status
    finally:
        record(args.log, job, status)
        if status != "submitted_verified":
            print("Application status:", status)
            ask("Press Enter to close this Lever application")
        else:
            print("Verified submission; continuing to next job")
        page.close()
