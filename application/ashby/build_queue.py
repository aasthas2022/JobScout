"""Build independent Ashby queue from output/matches.csv."""

import argparse
import csv
import json
from pathlib import Path

from .browser import is_ashby_url


def pick(row, *keys):
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--matches", type=Path, default=Path("output/matches.csv"))
    p.add_argument("--output", type=Path, default=Path("output/ashby_queue.jsonl"))
    args = p.parse_args()
    if not args.matches.is_file():
        p.error(f"Missing matches file: {args.matches}")
    jobs, seen = [], set()
    with args.matches.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            url = pick(row, "apply_url", "job_url", "url", "link", "application_url")
            if not is_ashby_url(url) or url in seen:
                continue
            seen.add(url)
            jobs.append({
                "source": "ashby", "company": pick(row, "company", "company_name"),
                "title": pick(row, "title", "job_title", "position"),
                "job_url": url, "apply_url": url,
                "description": pick(row, "description", "job_description", "description_text"),
                "location": pick(row, "location", "job_location"),
                "salary": pick(row, "salary", "salary_range", "compensation"),
            })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(j, ensure_ascii=False) + "\n" for j in jobs), encoding="utf-8")
    print(f"Wrote {len(jobs)} Ashby jobs to {args.output}")


if __name__ == "__main__":
    main()
