"""Command-line entry point and run orchestration."""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

from .config import CURRENT_COMPANY, JD_MAX_REQUIRED_EXPERIENCE, LOG, setup_logging
from .models import Candidate, Company, Profile
from .scout import Scout
from .utils import (Checkpoints, atomic_json, company_filenames, digest,
                    level_priority, write_csv)
from .web import Web

FIELDS = ['overall_rank', 'application_priority', 'company', 'company_rank', 'title', 'role', 'seniority', 'location',
          'fit_score', 'job_url', 'why_match', 'matched_skills', 'gaps', 'required_experience',
          'us_eligible', 'sponsorship', 'source', 'posted_at', 'link_verified', 'checked_at']

AUDIT_FIELDS = ['company', 'status', 'matches', 'retrieved_relevant', 'shortlisted', 'assessed',
                'non_us_skipped_locally', 'tavily_fallback_queries', 'links_checked', 'decisions', 'careers_urls', 'warnings', 'error']

def load_profile(path):
    p = Profile.model_validate_json(path.read_text(encoding='utf-8-sig'))
    if p.resume_path:
        resume = Path(p.resume_path)
        if not resume.is_absolute():
            resume = path.parent / resume
        if resume.suffix.lower() != '.txt':
            raise ValueError('resume_path must point to a UTF-8 .txt file, such as resume.txt')
        try:
            p.resume_text = resume.read_text(encoding='utf-8-sig')
        except FileNotFoundError as exc:
            raise ValueError(f'Resume file not found: {resume}') from exc
        except UnicodeDecodeError as exc:
            raise ValueError('Save your resume.txt file as UTF-8 plain text') from exc
    if len(p.resume_text.strip()) < 50:
        raise ValueError('Provide resume_text or a readable UTF-8 TXT resume_path (at least 50 characters)')
    if not p.role_preferences or 'other' in p.role_preferences or len(set(p.role_preferences)) != len(p.role_preferences):
        raise ValueError('role_preferences must contain distinct sde/senior_sde/fde values')
    return p

def load_companies(path):
    if path.suffix.lower() == '.csv':
        with path.open(encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or 'company' not in reader.fieldnames:
                raise ValueError('Companies CSV requires a company column')
            rows = [Company(**{k: v or '' for k, v in row.items() if k in Company.model_fields}) for row in reader]
    else:
        rows = [Company(company=line.strip()) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    unique = {}
    for c in rows:
        c.company = c.company.strip()
        c.careers_url = c.careers_url.strip()
        c.official_domain = c.official_domain.strip().lower()
        if c.official_domain and ('/' in c.official_domain or ':' in c.official_domain):
            raise ValueError('official_domain must be a hostname')
        if c.company:
            unique.setdefault(c.company.casefold(), c)
    return list(unique.values())

async def run(args):
    setup_logging(args.output, getattr(args, 'log_level', 'INFO'), getattr(args, 'log_file', None))
    run_started = time.monotonic()
    load_dotenv()
    os.environ.setdefault('LANGSMITH_PROJECT', 'job-scout-v2')
    os.environ.setdefault('LANGSMITH_HIDE_INPUTS', 'true')
    os.environ.setdefault('LANGSMITH_HIDE_OUTPUTS', 'true')
    if not os.getenv('OPENAI_API_KEY'):
        raise ValueError('Set OPENAI_API_KEY in .env')
    profile = load_profile(args.profile)
    companies = load_companies(args.companies)
    if args.limit:
        companies = companies[:args.limit]
    LOG.info('Run started: companies=%d concurrency=%d resume=%s render=%s',
             len(companies), args.concurrency, args.resume, args.render)
    LOG.info('JD experience policy: required minimum must be at most %d years; unstated requirements remain eligible', JD_MAX_REQUIRED_EXPERIENCE)
    LOG.info('Level preference: non-senior first=%s professional experience override=%s',
             profile.prefer_non_senior_roles, profile.experience_years_override)
    output_names = company_filenames(companies)
    args.output.mkdir(parents=True, exist_ok=True)
    store = Checkpoints(args.output / 'checkpoints_v2', args.resume, args.cache_hours * 3600)
    async with httpx.AsyncClient(timeout=45, headers={'User-Agent': 'JobScout/2.0 public-careers-research'}) as client:
        web = Web(client, args.render, store=store)
        # Parse once per profile/model; no raw resume sent in company searches.
        dummy = Candidate(skills=[], experience_summary='', experience_years=0,
                          production_systems=[], ai_agent_experience=[], customer_implementation_experience=[],
                          accomplishments=[], education=[], evidence=[])
        scout = Scout(web, profile, dummy, store, args)
        LOG.info('Preparing resume matching profile')
        candidate = await scout.ask(Candidate,
            'Extract a career matching profile from the resume. Do not invent skills or results. '
            'Compute professional experience without double-counting overlapping periods; do not count education as employment. '
            'Use supplied current date for Present. Customer implementation and AI claims need supplied evidence. '
            'If declared_experience_years is supplied, use it for professional experience and summary '
            'instead of calculating a conflicting total from dates or overlapping early roles. '
            'Leave absent experience empty. Include exact supporting resume substrings in evidence. '
            'Exclude name, contact details, street address and other non-career personal details.',
            {'resume': profile.resume_text, 'extra': profile.extra_information,
             'declared_experience_years': profile.experience_years_override,
             'today': datetime.now(timezone.utc).date().isoformat()}, 'candidate-profile')
        candidate.evidence = [e for e in candidate.evidence if e in profile.resume_text + profile.extra_information]
        if profile.experience_years_override is not None:
            candidate.experience_years = profile.experience_years_override
        atomic_json(args.output / 'candidate_profile.json', candidate.model_dump())
        scout.candidate = candidate
        graph = scout.graph()
        sem, export_lock, completed = asyncio.Semaphore(args.concurrency), asyncio.Lock(), {}
        def export():
            states = [completed[i] for i in sorted(completed)]
            all_rows = [dict(row) for state in states for row in state.get('rows', [])]
            def key(row):
                order = profile.role_preferences.index(row['role'])
                within_level = ((order, -row['fit_score'], row['company'], row['job_url']) if profile.ranking_mode == 'role_first'
                                else (-row['fit_score'], order, row['company'], row['job_url']))
                return (level_priority(row['role'], row.get('seniority', 'unknown'), profile),) + within_level
            all_rows.sort(key=key)
            for index, row in enumerate(all_rows, 1):
                row['overall_rank'] = index
                row['application_priority'] = 'high' if row['fit_score'] >= 85 else ('medium' if row['fit_score'] >= 75 else 'lower')
            write_csv(args.output / 'matches.csv', all_rows, FIELDS)
            audit = []
            for state in states:
                metrics = state.get('metrics', {})
                audit.append({'company': state['company']['company'], 'status': state.get('status', 'error'),
                              'matches': len(state.get('rows', [])), **metrics,
                              'decisions': json.dumps(metrics.get('decisions', {})),
                              'careers_urls': '; '.join(state.get('roots', [])),
                              'warnings': ' | '.join(state.get('warnings', [])), 'error': state.get('error', '')})
            write_csv(args.output / 'company_status.csv', audit, AUDIT_FIELDS)
            atomic_json(args.output / 'run_metrics.json', {'completed_companies': len(completed),
                        'matches': len(all_rows), **scout.metrics, **web.stats,
                        'note': 'Tokens cover successful model calls in this process; cached input tokens are a subset. '
                                'Tavily counts exclude individual HTTP retries; reported credits come from successful API responses. '
                                'No fixed USD estimate.'})
        async def process(index, company):
            async with sem:
                CURRENT_COMPANY.set(company.company)
                started = time.monotonic()
                LOG.info('Company started: %d/%d careers_url=%s', index + 1, len(companies), company.careers_url or 'discover automatically')
                try:
                    state = await graph.ainvoke({'company': company.model_dump()}, config={
                        'run_name': 'job_scout_company', 'tags': ['job-scout-v2'], 'metadata': {'company': company.company}})
                except Exception as exc:
                    LOG.error('Company failed: %s', type(exc).__name__)
                    state = {'company': company.model_dump(), 'status': 'error', 'rows': [],
                             'error': type(exc).__name__ + ': ' + str(exc)[:200]}
                # Full final state supplements stage/batch checkpoint files.
                company_file = args.output / 'companies' / output_names[company.company.casefold()]
                atomic_json(company_file, state)
                LOG.info('Company checkpoint saved: %s', company_file)
                legacy_file = args.output / 'companies' / (digest(company.model_dump()) + '.json')
                if legacy_file.exists():
                    try:
                        legacy = json.loads(legacy_file.read_text(encoding='utf-8'))
                        if legacy.get('company', {}).get('company') == company.company:
                            legacy_file.unlink()  # New readable file was saved successfully first.
                    except (OSError, ValueError, AttributeError):
                        pass
            async with export_lock:
                completed[index] = state
                export()
                LOG.info('Company finished: status=%s matches=%d elapsed=%.1fs completed=%d/%d CSVs updated',
                         state.get('status'), len(state.get('rows', [])), time.monotonic() - started, len(completed), len(companies))
                print(f'[{len(completed)}/{len(companies)}] {company.company}: {state.get("status")} ({len(state.get("rows", []))} matches)', flush=True)
                if not state.get('rows') and state.get('warnings'):
                    print('  ' + state['warnings'][0] + '\n  See company_status.csv for all warnings.', flush=True)
        await asyncio.gather(*(process(i, company) for i, company in enumerate(companies)))
        export()
    LOG.info('Run finished: companies=%d matches=%d elapsed=%.1fs model_calls=%d',
             len(completed), sum(len(s.get('rows', [])) for s in completed.values()), time.monotonic() - run_started, scout.metrics['llm_calls'])
    print(f'Saved matches.csv, company_status.csv and run_metrics.json in {args.output}')

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--companies', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('output'))
    parser.add_argument('--concurrency', type=int, default=4)
    parser.add_argument('--max-pages', type=int, default=60)
    parser.add_argument('--max-candidates', type=int, default=150, help='0 = assess all relevant jobs')
    parser.add_argument('--verify-pool', type=int, default=12)
    parser.add_argument('--cache-hours', type=float, default=12)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--render', action='store_true')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO')
    parser.add_argument('--log-file', type=Path, help='Default: OUTPUT/agent.log (rotates at 10 MB; 3 backups)')
    parser.add_argument('--no-search-fallback', dest='search_fallback', action='store_false')
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    if min(args.concurrency, args.max_pages, args.verify_pool) < 1 or args.max_candidates < 0 or args.cache_hours < 0 or (args.limit is not None and args.limit < 1):
        parser.error('Invalid limits')
    asyncio.run(run(args))
