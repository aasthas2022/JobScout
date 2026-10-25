
"""Build Lever-only application queue from JobScout matches."""

import argparse
import csv
import json
from pathlib import Path

from .browser import is_lever_url


def pick(row, *keys):
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def main():
    parser = argparse.ArgumentParser(
        description="Build Lever queue"
    )
    parser.add_argument(
        "--matches",
        type=Path,
        default=Path("output/matches.csv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/lever_queue.jsonl"),
    )

    args = parser.parse_args()

    if not args.matches.is_file():
        parser.error(
            f"Missing matches CSV: {args.matches}"
        )

    jobs = []
    seen = set()

    with args.matches.open(
        newline="",
        encoding="utf-8-sig",
    ) as handle:
        for row in csv.DictReader(handle):
            url = pick(
                row,
                "apply_url",
                "job_url",
                "url",
                "link",
                "application_url",
            )

            if not is_lever_url(url) or url in seen:
                continue

            seen.add(url)

            jobs.append({
                "source": "lever",
                "company": pick(
                    row,
                    "company",
                    "company_name",
                ),
                "title": pick(
                    row,
                    "title",
                    "job_title",
                    "position",
                ),
                "job_url": url,
                "apply_url": url,
                "description": pick(
                    row,
                    "description",
                    "job_description",
                    "description_text",
                ),
                "location": pick(
                    row,
                    "location",
                    "job_location",
                ),
                "salary": pick(
                    row,
                    "salary",
                    "salary_range",
                    "compensation",
                ),
            })

    args.output.parent.mkdir(
        parents=True, exist_ok=True
    )

    with args.output.open(
        "w", encoding="utf-8"
    ) as stream:
        for job in jobs:
            stream.write(
                json.dumps(
                    job,
                    ensure_ascii=False,
                ) + "\n"
            )

    print(
        f"Wrote {len(jobs)} Lever jobs "
        f"to {args.output}"
    )


if __name__ == "__main__":
    main()
