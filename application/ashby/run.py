"""Independent Ashby runner: python3 -m application.ashby.run."""

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

from .browser import is_ashby_url
from .workflow import process_job
from ..memory import AnswerMemory


def load_jobs(path, limit):
    seen, jobs = set(), []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            job = json.loads(line)
        except ValueError:
            continue
        url = job.get("apply_url") or job.get("job_url") or ""
        if not is_ashby_url(url) or url in seen:
            continue
        seen.add(url)
        job["source"] = "ashby"
        job["job_url"] = url
        jobs.append(job)
        if limit > 0 and len(jobs) >= limit:
            break
    return jobs


def history_for(path):
    history = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
                if event.get("job_url"):
                    history[event["job_url"]] = event.get("status")
            except ValueError:
                pass
    return history


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--queue", type=Path, default=Path("output/ashby_queue.jsonl"))
    p.add_argument("--profile", type=Path, default=Path("application_private/profile.json"))
    p.add_argument("--experience", type=Path, default=Path("application_private/experience_profile.json"))
    p.add_argument("--resume", type=Path, required=True)
    p.add_argument("--memory", type=Path, default=Path("application_private/answers.json"))
    p.add_argument("--log", type=Path, default=Path("application_private/applications.jsonl"))
    p.add_argument("--limit", type=int, default=1)
    p.add_argument("--agent", action="store_true")
    p.add_argument("--allow-submit", action="store_true")
    p.add_argument("--auto-submit-reviewed", action="store_true")
    p.add_argument(
        "--browser-profile", type=Path,
        default=Path(__file__).resolve().parents[2] / "application_private" / "browser_profiles" / "ashby",
        help="Persistent Chromium profile directory",
    )
    args = p.parse_args()
    if args.auto_submit_reviewed and not args.allow_submit:
        p.error("--auto-submit-reviewed requires --allow-submit")
    for path in (args.queue, args.profile, args.experience, args.resume):
        if not path.is_file():
            p.error(f"Missing file: {path}")
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    experience = json.loads(args.experience.read_text(encoding="utf-8"))
    memory = AnswerMemory(args.memory)
    jobs = load_jobs(args.queue, args.limit)
    history = history_for(args.log)
    print("ASHBY RUNNER | Jobs:", len(jobs), "| AI:", args.agent,
          "| Submit:", args.allow_submit, "| Auto after review:", args.auto_submit_reviewed)
    if not jobs:
        print("No Ashby jobs in queue")
        return
    profile_dir = args.browser_profile.expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    print("Persistent browser profile:", profile_dir)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir), headless=False, slow_mo=60,
            accept_downloads=True, viewport={"width": 1440, "height": 1000},
        )
        try:
            for index, job in enumerate(jobs, 1):
                url = job["job_url"]
                print(f"\nApplication {index}/{len(jobs)}:", job.get("company"), job.get("title"))
                if history.get(url) == "submitted_verified":
                    print("Already submitted; skipping")
                    continue
                if history.get(url) in (
                    "submission_unverified", "verification_pending", "verification_failed"
                ):
                    if input("Earlier submission uncertain. Confirm NOT submitted [yes/no]: ").strip().lower() != "yes":
                        continue
                status = process_job(context, job, profile, experience, args.resume.resolve(), memory, args)
                history[url] = status
                print("Status:", status)
        finally:
            context.close()


if __name__ == "__main__":
    main()
