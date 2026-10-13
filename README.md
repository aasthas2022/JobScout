# Job Scout

Job Scout researches public employer career pages, finds software engineering and Forward Deployed Engineer (FDE) openings that may fit a candidate, and exports a ranked, reviewable shortlist. It pairs resume evidence with job descriptions, checks US location eligibility, applies a small set of explicit eligibility rules, rechecks selected job links, and records how much of each company's career site it could inspect.

The intended outcome is a more focused job search: instead of repeatedly opening dozens of company boards, a candidate can review one shortlist with fit evidence, gaps, links, and a company-by-company coverage report. Job Scout is a research assistant; it does not submit applications or make hiring decisions.

> **Illustrative output only:** the sample rows in this README use fictional employers and `.example` URLs. They do not describe real job openings.

## Contents

- [What Job Scout does](#what-job-scout-does)
- [How the workflow works](#how-the-workflow-works)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Input files](#input-files)
- [Run options](#run-options)
- [Output files](#output-files)
- [Sample CSV output](#sample-csv-output)
- [Reading the results](#reading-the-results)
- [Coverage, limits, and responsible use](#coverage-limits-and-responsible-use)
- [Troubleshooting](#troubleshooting)

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

## Quick start

### 1. Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Configure API keys

```bash
cp .env.example .env
```

Edit `.env` and add your own credentials. `.env` is ignored by Git. `OPENAI_API_KEY` is required. Add `TAVILY_API_KEY` if you want automatic career-page discovery or supplemental search fallback. If you do not use Tavily, include a valid `careers_url` for every company.

### 4. Prepare a private resume and profile

```bash
cp profile.example.json profile.json
```

Put your resume in a UTF-8 plain-text `.txt` file. The example expects `resume.txt` next to `profile.json`; update `resume_path` if you use a different path. Keep real profile and resume files out of the public repository.

### 5. Prepare the company list

```bash
cp companies.example.csv companies.csv
```

Replace the example company with your target employers. Include `careers_url` when you already know the official public careers page.

### 6. Run Job Scout

```bash
python -m job_scout --profile profile.json --companies companies.csv --resume
```

Review `output/matches.csv` and `output/company_status.csv`. Use `python -m job_scout --help` for the full list of options.

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
python -m job_scout \
  --profile profile.json \
  --companies companies.csv \
  --output output \
  --limit 2 \
  --max-pages 80 \
  --log-level DEBUG
```

To rerun using saved checkpoints:

```bash
python -m job_scout --profile profile.json --companies companies.csv --resume
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

By default, Job Scout writes to `output/`:

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

The following is **fabricated sample data** illustrating the column layout only. Scores and explanations do not represent actual Job Scout results.

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

- Job Scout only researches public career information. It does not sign into accounts, bypass access controls, submit applications, contact recruiters, or make hiring decisions.
- Career-site markup and ATS interfaces can change. Some employers block automated requests, return incomplete results, or require JavaScript rendering. Review warnings and coverage status.
- Workday retrieval uses an undocumented interface and is best-effort. Optional rendering may improve some pages but cannot guarantee coverage.
- Search results and extracted listings can be stale or incomplete. Verify each posting and its requirements directly with the employer before acting.
- Role classification, experience extraction, and fit scores are model-generated. Check citations/evidence in the source posting and resume yourself.
- A `sponsorship` value of `not_excluded_by_jd` is deliberately narrow. Job Scout does not research employer sponsorship history or infer an individual's immigration status.
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
| A result appears stale | Open the employer's posting. Run again without relying on stale checkpoints, or use a fresh output directory. |

For more detail during diagnosis, pass `--log-level DEBUG`. Avoid sharing logs or output JSON publicly without checking for sensitive candidate-derived details.
