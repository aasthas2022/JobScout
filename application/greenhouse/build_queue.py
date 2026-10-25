
"""Export Greenhouse listings from JobScout matches.csv."""

import argparse
import csv
import json
import re

from pathlib import Path
from urllib.parse import urlparse, parse_qs


def greenhouse_job(
    url: str,
    source: str = ""
) -> bool:

    host = (
        urlparse(url or "").hostname or ""
    ).lower()

    return (
        source.strip().lower() == "greenhouse"
        or host in {
            "boards.greenhouse.io",
            "job-boards.greenhouse.io",
            "boards.eu.greenhouse.io"
        }
        or host.endswith(".greenhouse.io")
    )


def identifier(url: str) -> str:
    parsed = urlparse(url)

    match = re.search(
        r"/jobs/(\d+)",
        parsed.path
    )

    if match:
        return match.group(1)

    return (
        parse_qs(parsed.query).get("gh_jid")
        or [""]
    )[0]


def export_queue(
    input_path: Path,
    output_path: Path,
    limit: int = 0
) -> int:

    with input_path.open(
        encoding="utf-8-sig",
        newline=""
    ) as file:
        rows = list(csv.DictReader(file))

    queue = []
    seen = set()

    for row in rows:
        url = (
            row.get("job_url") or ""
        ).strip()

        if not url:
            continue

        if not greenhouse_job(
            url,
            row.get("source") or ""
        ):
            continue

        key = (
            url.split("#", 1)[0]
               .split("?", 1)[0]
               .rstrip("/")
        )

        if key in seen:
            continue

        seen.add(key)

        queue.append({
            "company": row.get("company", ""),
            "title": row.get("title", ""),
            "job_url": url,
            "job_id": identifier(url),
            "source": "greenhouse",
            "fit_score": row.get("fit_score", ""),
            "overall_rank": row.get("overall_rank", ""),
            "status": "queued"
        })

        if limit and len(queue) >= limit:
            break

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with output_path.open(
        "w",
        encoding="utf-8"
    ) as file:
        for row in queue:
            file.write(
                json.dumps(
                    row,
                    ensure_ascii=False
                ) + "\n"
            )

    print(
        f"Exported {len(queue)} Greenhouse jobs "
        f"to {output_path}"
    )

    return len(queue)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--input",
        type=Path,
        default=Path("output/matches.csv")
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "output/greenhouse_queue.jsonl"
        )
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=0
    )

    args = parser.parse_args()

    export_queue(
        args.input,
        args.output,
        args.limit
    )


if __name__ == "__main__":
    main()
