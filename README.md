# Job Scout

> **One project, two stages:** discover and rank public openings, then optionally run independent application workflows for Ashby, Lever, and Greenhouse. Discovery is research-only; the application runners support review-first autofill and explicit submission options.

> **Illustrative output:** fictional employers and `.example` URLs in the sample CSV are not real openings.

## Contents

- [Quick start](#quick-start)
- [What Job Scout does](#what-job-scout-does)
- [How the workflow works](#how-the-workflow-works)
- [Requirements](#requirements)
- [Configuration](#configuration)
- [Application workflow and human review](#application-workflow-and-human-review)
- [Run options](#run-options)
- [Output files](#output-files)
- [Sample CSV output](#sample-csv-output)
- [Reading the results](#reading-the-results)
- [Coverage, limits, and responsible use](#coverage-limits-and-responsible-use)
- [Troubleshooting](#troubleshooting)
- [Current limitations and potential improvements](#current-limitations-and-potential-improvements)

## Quick start

Run discovery **from the parent directory of `job_scout/`** so that `python -m job_scout` resolves the package. Run application commands **from inside `job_scout/`** (their relative default input paths are project-root relative).

```bash
cd job_scout
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m playwright install chromium
cp .env.example .env
cp profile.example.json profile.json
mkdir -p application_private
cp application_private_profile.example.json application_private/profile.json
# Add OPENAI_API_KEY to .env and prepare resume.txt plus application_private/resume.pdf.
# Add application_private/experience_profile.json if using AI-generated application answers.
```

Edit `companies.csv` to include target employers (`company,careers_url,official_domain`). When `careers_url` is missing, Tavily discovery requires `TAVILY_API_KEY`. Existing entries in `companies.csv` were preserved.

### 1. Retrieve active listings and rank matches

```bash
# From the parent directory containing job_scout/:
python3 -m job_scout.discovery --profile job_scout/profile.json --companies job_scout/companies.csv --output job_scout/output
# Original command remains supported:
python3 -m job_scout --profile job_scout/profile.json --companies job_scout/companies.csv --output job_scout/output
```

Both entry points use the **same unchanged** `discovery/cli.py` → `discovery/scout.py` → `discovery/web.py` pipeline. Re-run to refresh results; `--resume` reuses valid checkpoints (so omit it if you want fresh checks). Look at `output/matches.csv`, `output/company_status.csv` and the diagnostics before applying. `--limit N` limits companies; omitting it targets the whole CSV. `--max-candidates` and `--verify-pool` also limit coverage (see reference).

### 2. Build independent ATS queues

```bash
# Run these from job_scout/:
python3 -m application.ashby.build_queue --matches output/matches.csv --output output/ashby_queue.jsonl
python3 -m application.lever.build_queue --matches output/matches.csv --output output/lever_queue.jsonl
python3 -m application.greenhouse.build_queue --input output/matches.csv --output output/greenhouse_queue.jsonl
```

The queue builders use the existing matched results rather than scanning all raw jobs. Check queue contents for accuracy and duplicates before running applications.

### 3. Apply through each independent runner

```bash
# From job_scout/, after preparing application_private/profile.json,
# application_private/experience_profile.json and your own PDF resume:
python3 -m application.ashby.run --queue output/ashby_queue.jsonl --resume application_private/resume.pdf --limit 1 --agent
python3 -m application.lever.run --queue output/lever_queue.jsonl --resume application_private/resume.pdf --limit 1 --agent
python3 -m application.greenhouse.run --queue output/greenhouse_queue.jsonl --resume application_private/resume.pdf --limit 1 --agent
```

### Persistent browser sessions (Ashby and Lever)

Both Ashby and Lever now launch Chromium with an independent persistent profile so site cookies and session preferences can survive between runs. Lever defaults to `application_private/browser_profiles/lever/` and Ashby defaults to `application_private/browser_profiles/ashby/`; neither directory is included in Git. To select a different directory:

```bash
python3 -m application.lever.run --queue output/lever_queue.jsonl --resume application_private/resume.pdf --limit 1 --agent --browser-profile application_private/browser_profiles/lever
```

Keep the same `--browser-profile` path between Lever runs. Close other browsers using that profile before launching, because Chromium prevents multiple processes from simultaneously locking one profile. Human CAPTCHA/identity verification is still completed in the visible browser when required; persistent sessions can reduce repeat prompts but cannot guarantee fewer challenges, and this change does not bypass detection or CAPTCHA systems.

By default, these are review-oriented workflows; passing `--allow-submit` enables the existing submission behavior. Only use `--auto-submit-reviewed` in workflows that already support that flag and after checking answers and attachments. Site-specific confirmation, CAPTCHA, and identity checks may need manual input. The code does not bypass them.

**Backward compatibility:** `python3 -m application.apply` still launches Greenhouse; `python3 -m application.extract` still builds the Greenhouse queue. Existing Ashby and Lever commands remain the same.

### Full example commands (optional auto-submission flags)

Only use submission flags after you have reviewed candidate answers, attachments, and the relevant site's terms. These are examples and do not submit anything until executed locally.

```bash
python3 -m compileall -q application

# Greenhouse (legacy-compatible entry point)
python3 -m application.apply \
  --queue output/greenhouse_queue.jsonl \
  --profile application_private/profile.json \
  --resume application_private/resume.pdf \
  --experience application_private/experience_profile.json \
  --limit 21 --agent --allow-submit \
  --gmail-delay 5 --gmail-wait 120 --gmail-minutes 15

# Lever
python3 -m application.lever.run \
  --queue output/lever_queue.jsonl \
  --profile application_private/profile.json \
  --resume application_private/resume.pdf \
  --experience application_private/experience_profile.json \
  --limit 1 --agent --allow-submit --auto-submit-reviewed

# Ashby
python3 -m application.ashby.run \
  --queue output/ashby_queue.jsonl \
  --profile application_private/profile.json \
  --resume application_private/resume.pdf \
  --experience application_private/experience_profile.json \
  --limit 25 --agent --allow-submit --auto-submit-reviewed
```

The availability of flags can vary by runner. Use `python3 -m application.<ats>.run --help` to verify options before running.

## What Job Scout does

For a list of target employers, Job Scout can:

1. Use a supplied career-page URL, or discover an official careers page when one is not supplied.
2. Retrieve public job listings from supported applicant-tracking-system (ATS) boards and employer career pages.
3. Identify relevant US-based SDE, backend, full-stack, Senior SDE, and hands-on FDE positions.
4. Extract a structured career profile from a plain-text resume, with evidence tied to the supplied resume text.
5. Compare each shortlisted job description with the candidate's demonstrated skills, responsibilities, and experience.
6. Exclude a role only for defined criteria such as an explicit sponsorship restriction in that role's own description, a required experience minimum above six years, a confirmed non-US location, a hard requirement conflict, role mismatch, or a fit score below the configured threshold.
7. Recheck a limited pool of promising roles and their job links before exporting them.
8. Write ranked matches, company-level retrieval status, run metrics, logs, and resumable checkpoints.

### Why it adds value

- **Evidence-based fit:** match reasons and gaps are grounded in the resume and job description rather than just title similarity.
- **A usable shortlist:** each exported row includes a direct posting URL, fit score, role category, location, and review details.
- **Clearer eligibility handling:** US location is checked separately from resume fit. Sponsorship is excluded only when the particular job description explicitly says it is unavailable; silence is not treated as a negative.
- **Coverage transparency:** the status report helps distinguish “no suitable jobs found” from “the career site could not be fully retrieved.”
- **Less repeated work:** stage checkpoints, assessment caching, and `--resume` can reuse prior work on a subsequent run.
- **Spend visibility:** run metrics capture model calls and tokens, cache hits, and search-provider counts/credits where available.

## How the workflow works

1. **Load and validate inputs.** The profile and company list are checked. The resume must contain at least 50 non-whitespace characters.
2. **Build a candidate profile.** The configured OpenAI model extracts career information and supporting evidence. Contact details and other non-career personal details are excluded from this generated profile.
3. **Find official career sources.** Job Scout uses the URL in the company CSV when provided. Otherwise, it uses Tavily to discover the employer's public career page. The optional official domain helps constrain discovery.
4. **Retrieve and normalize jobs.** Public listings are gathered from available career pages and ATS sources. Similar listings are deduplicated and normalized to a common job shape.
5. **Filter and shortlist.** Irrelevant roles and clearly non-US listings can be filtered locally. The model checks uncertain US-location evidence before resume matching; remote by itself is not proof that a role can be performed from the US. The `--max-candidates` cap controls how many discovered candidates proceed to full assessment.
6. **Assess each job.** The model classifies the role and seniority, scores resume fit, identifies matches and gaps, and extracts any explicit minimum experience requirement and sponsorship restriction. Quotes are checked against the supplied job text before being trusted.
7. **Apply eligibility rules.** Job Scout applies the configured minimum fit score, configured role preferences, confirmed US location, and hard exclusions. A stated required minimum above six years is excluded; preferred experience is not treated as required experience.
8. **Verify selected postings.** Up to `--verify-pool` eligible links per company are revisited. If the posting changed, the updated description is assessed again. At most seven verified results per company are exported.
9. **Export and update progress.** As companies finish, Job Scout updates the CSV and JSON results in the output directory.

The language model makes judgment calls for classification and fit. Scores are useful for prioritizing human review, not calibrated probabilities of getting an interview or offer.

## Requirements

- Python 3.11 or newer
- An OpenAI API key for candidate-profile extraction and job assessment
- A Tavily API key when Job Scout needs to discover career pages or run supplemental search fallback
- Internet access to the employer career pages, ATS endpoints, and configured API services
- Optional: Playwright and Chromium for rendering some JavaScript-heavy career pages
- Optional: a LangSmith account/key for tracing model and graph calls

Supported ATS retrieval includes Greenhouse, Lever, Ashby, SmartRecruiters, and Workday. Some Eightfold and iCIMS pages may be handled through available page-level retrieval. Coverage varies by employer and can change when a site changes its interface or blocks requests.

## Configuration

### Environment variables

| Variable | Required? | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | Yes | Authenticates model calls for resume extraction and job assessment. |
| `OPENAI_MODEL` | No | Model name. The example uses `gpt-4.1-mini`. |
| `TAVILY_API_KEY` | Conditional | Career-page discovery and supplemental search fallback. Without it, supply a careers URL for each company. |
| `LANGSMITH_TRACING` | No | Enable or disable LangSmith tracing. The example disables tracing. |
| `LANGSMITH_API_KEY` | No | Credential for LangSmith tracing when enabled. |
| `LANGSMITH_PROJECT` | No | LangSmith project name; defaults to `job-scout`. |
| `LANGSMITH_HIDE_INPUTS` | No | Hide inputs from traces when supported; example is `true`. |
| `LANGSMITH_HIDE_OUTPUTS` | No | Hide outputs from traces when supported; example is `true`. |

The checked-in `.env.example` intentionally contains blank secret fields. Do not put real keys in `.env.example`, source files, screenshots, or committed output.

### Candidate profile (`profile.json`)

Start from `profile.example.json`. The resume can be supplied as `resume_path` or directly as `resume_text`. Use one method; for normal use, keeping resume text in a separate ignored `.txt` file is easier to manage.

| Field | Type / accepted values | Default / effect |
| --- | --- | --- |
| `resume_path` | Path to a UTF-8 `.txt` file | `null`; a relative path is resolved from the profile JSON's directory. |
| `resume_text` | String | Empty; provide this instead of a path if desired. At least 50 non-whitespace characters are required after loading. |
| `role_preferences` | Array of distinct values: `sde`, `senior_sde`, `fde` | Example: `sde`, `fde`, `senior_sde`. Determines eligible role categories and preference ordering. |
| `ranking_mode` | `fit_first` or `role_first` | `fit_first`; sort by fit within the seniority preference group. `role_first` puts configured role order first within that group. |
| `min_fit_score` | Integer from 0 to 100 | `65`; lower-scoring roles are not exported. |
| `extra_information` | String | Empty; optional career context to use in matching. Do not use it for secrets or unrelated sensitive data. |
| `prefer_non_senior_roles` | Boolean | `true`; prioritizes junior and mid-level individual-contributor roles, while senior roles can still qualify. |
| `experience_years_override` | Number from 0 to 60, or `null` | The model schema defaults to `4.5` if omitted. The example explicitly sets `null`, which asks the model to estimate from the resume. Set a number to make that declared experience authoritative for matching. |

Example profile:

```json
{
  "resume_path": "resume.txt",
  "role_preferences": ["sde", "fde", "senior_sde"],
  "ranking_mode": "fit_first",
  "min_fit_score": 65,
  "extra_information": "",
  "prefer_non_senior_roles": true,
  "experience_years_override": null
}
```

### Company list (`companies.csv`)

CSV input requires a `company` column. `careers_url` and `official_domain` are optional. `official_domain` must be a hostname, such as `example.org`, without a scheme, path, or port.

```csv
company,careers_url,official_domain
Example Company,https://example.org/careers,example.org
Another Example,,another-example.org
```

When no `careers_url` is provided, Tavily is used to find a likely official career page. If discovery is unavailable or inaccurate, specify the known official page explicitly. A newline-delimited text file is also accepted, with one employer name per line:

```text
Example Company
Another Example
```

## Application workflow and human review

The application stage is **separate from discovery**. Discovery produces `output/matches.csv`; each ATS queue builder extracts matching entries into a JSONL file; the matching runner then opens each application in a visible browser. Queue extraction itself never submits applications.

### Prepare private application inputs

From inside `job_scout/`:

```bash
python3 --version                         # Python 3.11+
python3 -m pip install -r requirements.txt
python3 -m pip install playwright
python3 -m playwright install chromium
mkdir -p application_private
cp application_private_profile.example.json application_private/profile.json
ls -lh application_private/resume.pdf
```

The **discovery profile** (`profile.json`) describes resume-based matching and references a UTF-8 text resume. The **application profile** (`application_private/profile.json`) supplies reviewed application-form details and is deliberately separate. A PDF resume is used for application uploads, and `application_private/experience_profile.json` may supply additional verified work evidence. Keep all personal data out of Git.

To verify that Chromium launches:

```bash
python3 -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(); print('Chromium OK'); b.close(); p.stop()"
```

If the application agent needs OpenAI, configure `OPENAI_API_KEY` in `.env` (or the local environment); never commit credentials.

### Greenhouse queue and backward-compatible commands

The current organized Greenhouse entry points are `application.greenhouse.build_queue` and `application.greenhouse.run`. The older commands remain available:

```bash
# Build a queue of up to ten Greenhouse matches (legacy command)
python3 -m application.extract \
  --input output/matches.csv \
  --output output/greenhouse_queue.jsonl \
  --limit 10

# Fill the first Greenhouse application without authorizing submission
python3 -m application.apply \
  --queue output/greenhouse_queue.jsonl \
  --profile application_private/profile.json \
  --resume application_private/resume.pdf \
  --limit 1
```

A queue item can include the employer, title, posting URL, ATS job ID, source, fit score, overall rank, and application status. Inspect queues for duplicate or stale links before applying.

### What happens when an application runner starts

1. Playwright opens the relevant job page in a visible Chromium window.
2. The runner locates the application form and attempts to fill supported fields using the applicant profile.
3. A supported file input receives the resume PDF.
4. Previously approved answers may be reused; optional `--agent` mode supports matching semantically equivalent questions against reviewed evidence.
5. Unknown questions, custom fields, and CAPTCHA or identity challenges may require human input in the browser or terminal.
6. Submission depends on the particular runner's flags and review/approval flow. Do not infer success from a click alone: check the on-page confirmation or recorded outcome.

For example, a saved answer to “How many years have you worked with Python?” might be relevant to “What is your total Python development experience?”, but the system should not fabricate a number. Employment history and technical claims must be supported by the user's evidence. Legal authorizations, sponsorship, demographic declarations, and consent should be answered using explicitly reviewed facts, not guessed by a language model.

### Reusable answers and application history

The legacy Greenhouse MVP describes these local paths:

```text
application_private/answers.json         # Saved, reviewed answers
application_private/applications.jsonl   # Application activity/history
```

Other ATS implementations may have their own storage and naming conventions. Use a consistent working directory so reusable answer stores can be located between runs. Review company-specific responses each time; an answer correct for one posting may be inappropriate for another.

### Human-in-the-loop decision flow

```text
Form question
    ↓
Exact approved answer available? ── Yes → Prefill for review
    │ No
    ↓
Agent finds a reliable evidence-backed answer? ── Yes → Prefill for review
    │ No
    ↓
Ask candidate / request manual completion
    ↓
Review responses, attachments and required declarations
    ↓
Submission allowed by runner settings and review flow?
    ├── No → Leave unsubmitted
    └── Yes → Submit → Check confirmation → Record outcome
```

The original Greenhouse MVP required the person to type `SUBMIT` when `--allow-submit` was enabled. Newer runner options such as `--auto-submit-reviewed` can change the approval flow where supported, so consult each runner's `--help` and validate on a single application first. **Do not assume all three ATS implementations have identical submission or confirmation behavior.**

### Responsibilities by mode

| Task | Primary mode |
| --- | --- |
| Discover, normalize and rank public jobs | Discovery pipeline and model-assisted assessment |
| Select ATS matches and build queues | Deterministic extraction |
| Find conventional form fields, fill known details and upload PDF | Browser automation |
| Look up an exact previously approved answer | Deterministic answer memory |
| Identify paraphrased questions and propose supported responses | Optional agentic matching |
| Supply unknown information, legal declarations and approvals | Candidate review / manual input |
| Submit when enabled; record and verify confirmation | ATS-specific runner, with human verification where needed |

Application auto-submit settings increase the need for accurate profile facts, duplicate checks and a verified confirmation workflow. Begin with `--limit 1` and without `--allow-submit` before attempting a larger batch.

## Run options

```text
python -m job_scout --profile PROFILE --companies COMPANIES [options]
```

| Option | Default | Description |
| --- | --- | --- |
| `--profile PATH` | Required | Candidate profile JSON. |
| `--companies PATH` | Required | Company CSV or newline-delimited text file. |
| `--output PATH` | `output` | Directory for CSVs, logs, checkpoints, and JSON artifacts. |
| `--concurrency N` | `4` | Number of companies processed concurrently. |
| `--max-pages N` | `60` | Maximum page budget used during retrieval. |
| `--max-candidates N` | `150` | Maximum retrieved candidate jobs to assess; use `0` to assess all relevant retrieved jobs. |
| `--verify-pool N` | `12` | Maximum eligible jobs per company to recheck before export. |
| `--cache-hours N` | `12` | Lifetime for reusable checkpoint/cache entries. |
| `--resume` | Off | Reuse valid checkpoints and cached assessments from the output directory. |
| `--render` | Off | Use optional browser rendering for pages that need JavaScript. Requires Playwright and Chromium. |
| `--log-level LEVEL` | `INFO` | One of `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| `--log-file PATH` | `OUTPUT/agent.log` | Optional log destination; the default rotates at 10 MB and keeps three backups. |
| `--no-search-fallback` | Off | Disable supplemental Tavily search after direct retrieval finds no candidates or appears incomplete. |
| `--limit N` | No limit | Process only the first N companies from the input file; useful for a small trial run. |

Example: test two companies with more retrieval capacity and verbose logs:

```bash
python -m job_scout.discovery \
  --profile job_scout/profile.json \
  --companies job_scout/companies.csv \
  --output job_scout/output \
  --limit 2 \
  --max-pages 80 \
  --log-level DEBUG
```

To rerun using saved checkpoints:

```bash
python -m job_scout.discovery --profile job_scout/profile.json --companies job_scout/companies.csv --output job_scout/output --resume
```

Use the same output directory and the same inputs when resuming. To force a clean run, choose a new output directory or remove the old generated output after reviewing it.

### Optional JavaScript rendering

Install Playwright and its Chromium browser once in the active environment:

```bash
pip install playwright
playwright install chromium
```

Then pass `--render`. Rendering can help with some JavaScript-heavy pages, but it does not guarantee access to sites that block automated traffic or require authentication.

## Output files

By default, Job Scout discovery writes to `output/`:

| File or directory | Contents |
| --- | --- |
| `matches.csv` | Ranked, verified shortlist. This is the main spreadsheet for reviewing roles. |
| `company_status.csv` | Per-company status, retrieval/assessment counts, decisions, URLs, warnings, and errors. |
| `run_metrics.json` | Aggregate company and match counts, model call/token/cache statistics, and search-provider metrics where available. |
| `candidate_profile.json` | Structured career profile extracted from the resume. This can contain sensitive career details; keep it private. |
| `agent.log` | Timestamped run log, including warnings and progress. |
| `checkpoints/` | Intermediate retrieval and assessment cache used for resume/reuse. May contain job descriptions and candidate-derived data. |
| `companies/*.json` | Final state and evidence for each company. May contain public job-description text and matching data. |

Generated output, profile, company input, resume, virtual environment, and local environment file are ignored by the repository's `.gitignore`. Still review `git status` before committing: ignore rules do not untrack files that were already added to Git.

### `matches.csv` columns

| Column | Meaning |
| --- | --- |
| `overall_rank` | Position across all exported matches, after sorting the entire run. |
| `application_priority` | Convenience label derived from fit score: `high` at 85+, `medium` at 75–84, and `lower` below 75. This label does not submit or recommend an application automatically. |
| `company` | Employer name from the input list. |
| `company_rank` | Rank within that employer's verified results. |
| `title` | Current job title as retrieved. |
| `role` | Normalized role category: `sde`, `senior_sde`, or `fde`. |
| `seniority` | Inferred level such as `junior`, `mid`, `senior`, `staff`, `principal`, or `unknown`. |
| `location` | Location shown by the job source. |
| `fit_score` | Model assessment from 0 to 100 using skills/domain (40%), level/experience (30%), and demonstrated responsibilities (30%). It is not a probability. |
| `job_url` | Public posting URL rechecked during verification. |
| `why_match` | Concise model-generated explanation of fit. |
| `matched_skills` | Semicolon-separated relevant skills supported by the candidate profile. |
| `gaps` | Semicolon-separated material gaps or weaker evidence to review. |
| `required_experience` | Required experience text extracted from the job description, when stated. |
| `us_eligible` | `yes` for a role whose location was confirmed US-eligible before export. |
| `sponsorship` | Currently exported as `not_excluded_by_jd`. This means the role was not rejected for an explicit no-sponsorship statement in that job description. It does **not** mean sponsorship is offered or guaranteed. |
| `source` | Retrieval source or ATS type, when identified. |
| `posted_at` | Posting date when available from the source. |
| `link_verified` | `true` when the selected posting passed the final link/current-posting checks. |
| `checked_at` | UTC timestamp when the final verification result was exported. |

The CSV is written as UTF-8 with a byte-order mark for common spreadsheet compatibility. Cells beginning with spreadsheet formula characters are escaped to reduce formula-injection risk when opened in spreadsheet software.

## Sample CSV output

The following is **fabricated sample data** illustrating discovery output columns only. Scores and explanations do not represent actual Job Scout results.

```csv
overall_rank,application_priority,company,company_rank,title,role,seniority,location,fit_score,job_url,why_match,matched_skills,gaps,required_experience,us_eligible,sponsorship,source,posted_at,link_verified,checked_at
1,high,Northstar Analytics,1,Software Engineer - Data Platform,sde,mid,"Remote, United States",91,https://jobs.northstar.example/roles/engineer-104,"Strong backend and data-platform overlap; resume shows production APIs and ownership of reliable services.",Python; distributed systems; APIs,"No direct experience with the listed cloud warehouse",3+ years,yes,not_excluded_by_jd,greenhouse,2026-09-28,true,2026-10-13T17:25:14+00:00
2,medium,Cedar Labs,1,Forward Deployed Engineer,fde,mid,"New York, NY (Hybrid)",82,https://careers.cedar.example/jobs/fde-22,"Hands-on integrations and deployment work align with the role's customer problem-solving responsibilities.",Python; API integrations; production deployment,"Limited evidence of direct customer discovery",4+ years,yes,not_excluded_by_jd,ashby,2026-10-02,true,2026-10-13T17:25:18+00:00
```

## Reading the results

### Start with the shortlist

Open `matches.csv` in a spreadsheet or text editor. Sort/filter by `application_priority`, `fit_score`, `company`, `role`, or `location`. Read the job description at `job_url` before applying. Use `why_match`, `matched_skills`, `gaps`, and `required_experience` as prompts for your own review, not as a substitute for checking the original posting.

### Check company coverage

`company_status.csv` is important when a company has no matches. Status values include:

| Status | Meaning |
| --- | --- |
| `matched` | At least one role passed assessment and link verification. |
| `no_verified_matches` | Candidate jobs existed, but none remained after assessment and/or final verification. |
| `retrieval_incomplete` | No jobs were available to export and retrieval warnings indicate that coverage may have been incomplete. |
| `no_jobs_found` | No relevant candidate jobs were found in the retrieved sources, without a warning that changes the status to incomplete. |
| `error` | Processing failed for that company. Review the `error` and `warnings` columns. |

Review `warnings`, `careers_urls`, and `error` before interpreting an empty result as “this company has no suitable jobs.” Public sites may fail to load, limit requests, or expose only partial listings.

### Understand run metrics

`run_metrics.json` records aggregate counts and model/search usage information. Token totals cover successful model calls in the current process; cached input tokens are a subset. Tavily request/credit reporting excludes individual HTTP retries and uses reported credits from successful API responses. No fixed USD cost estimate is provided because rates and usage depend on provider pricing and run behavior.

## Coverage, limits, and responsible use

- The discovery module researches public career information and does not submit applications, sign into accounts, contact recruiters, or make hiring decisions. Separate application runners can interact with job forms and submit applications only when their submission options are enabled; they do not bypass access controls.
- Career-site markup and ATS interfaces can change. Some employers block automated requests, return incomplete results, or require JavaScript rendering. Review warnings and coverage status.
- Workday retrieval uses an undocumented interface and is best-effort. Optional rendering may improve some pages but cannot guarantee coverage.
- Search results and extracted listings can be stale or incomplete. Verify each posting and its requirements directly with the employer before acting.
- Role classification, experience extraction, and fit scores are model-generated. Check citations/evidence in the source posting and resume yourself.
- A `sponsorship` value of `not_excluded_by_jd` is deliberately narrow. The discovery module does not research employer sponsorship history or infer an individual's immigration status.
- “US eligible” means the posting appears to allow work in at least one US location. It does not establish personal work authorization, relocation eligibility, or legal eligibility to work.
- The configured experience exclusion applies to a stated **required minimum** above six years. Preferred experience is not an exclusion. Roles with no stated required minimum remain eligible for assessment.
- The run can incur API charges for OpenAI and Tavily. `--limit`, `--max-candidates`, caching, and `--resume` can help manage repeated or exploratory runs.
- Follow the terms of use and request limits of the services and career sites you access.

## Troubleshooting

| Symptom | Likely cause and next step |
| --- | --- |
| `Set OPENAI_API_KEY in .env` | Add a valid OpenAI key to the local `.env` file and rerun. |
| Tavily discovery error | Set `TAVILY_API_KEY`, or provide a valid `careers_url` for each company. |
| Resume path not found | Paths are resolved relative to the profile JSON. Check spelling and location; use UTF-8 plain-text `.txt`. |
| Resume is too short | Provide at least 50 non-whitespace characters in `resume_text` or the referenced text file. |
| No matches, but career site has jobs | Check `company_status.csv` warnings and discovered URLs. Try a direct official `careers_url`, raise `--max-pages` or `--max-candidates`, or try `--render` for a JavaScript page. |
| Site returns blocked/incomplete warnings | The employer may be limiting automated retrieval. Try a known public board URL, lower `--concurrency`, or review the site's public listings manually. |
| Some roles are absent | The maximum candidate and verification pools are capped. Increase `--max-candidates` and/or `--verify-pool`; use `--max-candidates 0` to assess every relevant retrieved job. |
| `playwright` or browser error | Install Playwright and Chromium in the active virtual environment, then rerun with `--render`. |
| `zsh: command not found: python` | Use `python3`, especially on macOS, or activate the correct virtual environment. |
| `ModuleNotFoundError: No module named 'job_scout'` | Run `python3 -m job_scout.discovery` from the parent of the `job_scout/` package; run `python3 -m application.*` inside the project root. |
| `BrowserType.launch: Executable doesn't exist` | Run `python3 -m playwright install chromium` using the same Python environment as the runner. |
| `No module named 'playwright'` | Install with `python3 -m pip install playwright` and install Chromium. |
| Application queue missing | Build the corresponding ATS queue, then check `ls -lh output/*_queue.jsonl`. |
| Application resume/profile missing | Check `ls -lh application_private/` and pass correct `--profile`, `--resume`, and `--experience` paths. |
| Browser opens but fields remain blank | Look for an Apply button, embedded iframe, custom controls, delayed or conditional fields, or selector changes; review manually. |
| CAPTCHA or browser verification appears | Complete it manually in the visible browser; do not bypass access controls or CAPTCHA. |
| Persistent profile is locked | Close another Chromium instance using the same `--browser-profile` directory. |
| A result appears stale | Open the employer's posting. Run again without relying on stale checkpoints, or use a fresh output directory. |

For more detail during diagnosis, pass `--log-level DEBUG`. Avoid sharing logs or output JSON publicly without checking for sensitive candidate-derived details.


## Current limitations and potential improvements

Discovery is best-effort and can miss jobs because of retrieval limits, dynamic sites, or ranking caps. Application forms change regularly and may include unsupported dynamic controls, iframes, conditional fields, or site-specific verification. Although Ashby and Lever now use persistent browser profiles, **a remembered profile does not mean that every login or CAPTCHA will persist**. Answer memory is local, and submission receipts and duplicate protection should be verified rather than assumed.

Potential improvements described in the older Greenhouse MVP include durable application state/checkpointing; an explicit `queued → filling → needs_input → ready_for_review → submitted → verified` status flow; better question-confidence checks; stronger selectors and conditional-field support; confirmation and duplicate-submission safeguards; and an optional review dashboard. These are roadmap ideas, **not claims that all these features are already implemented**. Ashby and Lever workflows are already present, so the older proposal to add those providers is obsolete.
