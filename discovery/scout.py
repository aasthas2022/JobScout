"""Job discovery, assessment, ranking, and verification graph."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from langchain_openai import ChatOpenAI
from langgraph.graph import START, END, StateGraph

from .config import CURRENT_COMPANY, JD_MAX_REQUIRED_EXPERIENCE, LOG, MAX_MATCHES_PER_COMPANY
from .models import (Assessment, BatchAssessment, Candidate, CareerChoice, Company, Job,
                     Locations, PageDetail, Profile, State)
from .prompts import RANKING_POLICY, SYSTEM
from .utils import (Checkpoints, address_countries, canonical, digest, employer_routes,
                    explicitly_no_sponsor, host, in_scope, jsonld_job_countries,
                    local_country_status, rank_key, relevant_title, retrieval_error,
                    scoring_job, structured_page_job, title_seniority, us_hint, visible_soup,
                    ats_identity, crawl_kind, level_priority, links, plain, recruiting_host)
from .web import Web

class Scout:
    def __init__(self, web: Web, profile: Profile, candidate: Candidate, store: Checkpoints, args):
        self.web, self.profile, self.candidate, self.store, self.args = web, profile, candidate, store, args
        self.model_name = os.getenv('OPENAI_MODEL', 'gpt-4.1-mini')
        self.llm = ChatOpenAI(model=self.model_name, temperature=0, timeout=90, max_retries=3)
        self.metrics = {'llm_calls': 0, 'input_tokens': 0, 'output_tokens': 0,
                        'model_cache_hits': 0, 'assessment_cache_hits': 0, 'cached_input_tokens': 0}
        self.model_locks = {}
        self.model_memory = {}
        self.token_lock = asyncio.Lock()
        self.namespace = digest({'profile': profile.model_dump(), 'candidate': candidate.model_dump(),
                                 'model': os.getenv('OPENAI_MODEL'), 'pages': args.max_pages,
                                 'candidates': args.max_candidates, 'render': args.render})

    async def ask(self, schema, instruction, data, key, fresh=False):
        # Analysis is reusable across company stages/crawl settings only when
        # model, schema, instructions, and the complete input are identical.
        cache_key = 'model-content-v1:' + digest({'model': self.model_name, 'schema': schema.model_json_schema(),
                                                'system': SYSTEM, 'instruction': instruction, 'data': data})
        async with self.model_locks.setdefault(cache_key, asyncio.Lock()):
            return await self.ask_cached(schema, instruction, data, cache_key, fresh)

    async def ask_cached(self, schema, instruction, data, cache_key, fresh=False):
        cached = None if fresh else self.model_memory.get(cache_key)
        if cached is None and not fresh:
            cached = self.store.get(cache_key)
        if cached is not None:
            try:
                result = schema.model_validate(cached)
                self.validate_model_result(result, data)
            except ValueError:
                self.store.discard(cache_key)
                self.model_memory.pop(cache_key, None)
                LOG.warning('Invalid model cache discarded: %s', schema.__name__)
            else:
                self.metrics['model_cache_hits'] += 1
                LOG.info('Model checkpoint reused: %s', schema.__name__)
                return result
        started = time.monotonic()
        LOG.info('Model call started: %s', schema.__name__)
        response = await self.llm.with_structured_output(schema, include_raw=True).ainvoke([
            ('system', SYSTEM + '\n' + instruction),
            ('human', json.dumps(data, ensure_ascii=False, separators=(',', ':'), sort_keys=True))])
        result = response['parsed']
        if result is None:
            raise ValueError('Structured output parse failed')
        usage = getattr(response['raw'], 'usage_metadata', None) or {}
        async with self.token_lock:
            self.metrics['llm_calls'] += 1
            self.metrics['input_tokens'] += usage.get('input_tokens', 0)
            self.metrics['output_tokens'] += usage.get('output_tokens', 0)
            self.metrics['cached_input_tokens'] += (usage.get('input_token_details') or {}).get('cache_read', 0)
        self.validate_model_result(result, data)
        self.store.put(cache_key, result.model_dump())
        self.model_memory[cache_key] = result.model_dump()
        if len(self.model_memory) > 512:
            self.model_memory.pop(next(iter(self.model_memory)))
        LOG.info('Model call finished: %s elapsed=%.1fs input_tokens=%d output_tokens=%d',
                 schema.__name__, time.monotonic() - started, usage.get('input_tokens', 0), usage.get('output_tokens', 0))
        return result

    @staticmethod
    def validate_model_result(result, data):
        if isinstance(result, (BatchAssessment, Locations)):
            rows = result.assessments if isinstance(result, BatchAssessment) else result.decisions
            expected = {j.get('id') or j.get('job_id') for j in data.get('jobs', [])}
            received = [row.job_id for row in rows]
            if len(received) != len(expected) or set(received) != expected:
                raise ValueError('Model omitted/duplicated job IDs; incomplete results are not cached')

    def company_key(self, state, stage):
        return self.namespace + ':' + digest(state['company']) + ':' + stage

    async def stage(self, state, name, function):
        key = self.company_key(state, name)
        cached = self.store.get(key)
        if cached is not None:
            retry_partial = name == 'fetch' and (not cached.get('jobs') or any(re.search(r'failed|incomplete|blocked|partial|limit reached', w, re.I) for w in cached.get('warnings', [])))
            if not retry_partial:
                LOG.info('Stage %s: checkpoint reused', name)
                return cached
            LOG.info('Stage %s: retrying incomplete checkpoint', name)
        started = time.monotonic()
        LOG.info('Stage %s: started', name)
        try:
            result = await function(state)
        except Exception as exc:
            LOG.error('Stage %s failed: %s elapsed=%.1fs', name, type(exc).__name__, time.monotonic() - started)
            raise
        # Partial retrieval is saved for inspection and retried on resume.
        self.store.put(key, result)
        LOG.info('Stage %s: finished elapsed=%.1fs', name, time.monotonic() - started)
        return result

    async def discover(self, state):
        async def work(s):
            company = Company(**s['company'])
            if company.careers_url:
                return {'roots': [canonical(company.careers_url)], 'warnings': []}
            hits = await self.web.search(company.company + ' official careers jobs',
                                         [company.official_domain] if company.official_domain else None)
            choice = await self.ask(CareerChoice,
                'Select the named employer\'s official careers URL from supplied results. '
                'Exclude aggregators and unrelated companies. Return empty if uncertain.',
                {'company': company.company, 'results': hits}, self.company_key(s, 'discover-choice'))
            if not choice.url or choice.url not in {h['url'] for h in hits}:
                raise ValueError('Careers discovery failed; supply careers_url')
            if company.official_domain and not (host(choice.url) == company.official_domain or host(choice.url).endswith('.' + company.official_domain)):
                raise ValueError('Careers URL does not match official_domain')
            return {'roots': [canonical(choice.url)], 'warnings': ['Discovered employer identity: ' + choice.reason]}
        return await self.stage(state, 'discover', work)

    async def page_job(self, url, company, roots, fresh=False, fetched=None):
        final, html = fetched if fetched is not None else await self.web.html(url)
        if not in_scope(final, roots):
            raise ValueError('Job detail redirected outside verified employer/ATS scope')
        soup = BeautifulSoup(html, 'html.parser')
        metadata = [tag.get_text() for tag in soup.select('script[type="application/ld+json"]')]
        countries = jsonld_job_countries(metadata)
        if local_country_status('', countries) == 'no':
            return None  # Known non-US job: no OpenAI page extraction or resume matching.
        soup = visible_soup(html)
        main = soup.select_one('main, [role="main"], article')
        text = (main or soup).get_text(' ', strip=True)
        if len(text) < 200:
            raise ValueError('No readable job description (JavaScript shell); install Playwright and retry with --render')
        if re.search(r'access denied|verify you are human|captcha', text[:1500], re.I):
            raise ValueError('Blocked or unreadable job page')
        if len(text) > 55000:
            raise ValueError('Job page too large to assess completely')
        structured = structured_page_job(metadata, text, final, company)
        if structured is not None:
            LOG.info('Complete structured JobPosting extracted without a model call: %s', structured.title)
            return structured
        detail = await self.ask(PageDetail,
            'Is this a single open job for the named employer or clearly identified subsidiary? '
            'Require the full job description and an application link/button; listing/homepages are not jobs. '
            'Reject closure notices and expired validThrough dates. Extract the exact title/location. '
            'Do not infer the employer solely from an unrelated footer.',
            {'company': company, 'url': final, 'today': datetime.now(timezone.utc).date().isoformat(),
             'text': text, 'json_ld': metadata[:10]}, 'page:' + final)
        if not detail.is_single_job or not detail.employer_matches or not detail.appears_open:
            return None
        if not detail.title or detail.title not in text + ' '.join(metadata):
            return None
        description = text + '\n' + '\n'.join(metadata[:10])
        return Job(id='page:' + digest(canonical(final)), external_id=canonical(final), title=detail.title,
                   location=detail.location, description=description, url=canonical(final),
                   source='icims' if host(final).endswith('.icims.com') else 'careers_page',
                   board_url=url, posted_at=detail.date_posted, country_codes=countries)

    async def fetch(self, state):
        async def work(s):
            roots, warnings = list(s['roots']), list(s.get('warnings', []))
            def warning(message):
                if message not in warnings:
                    warnings.append(message)
                    LOG.warning('%s', message)
            fallback = s.get('_search_phase', False)
            jobs = list(s.get('_direct_jobs', []))
            fetched_boards = set(s.get('_fetched_boards', set()))
            successful_boards = set(s.get('_successful_boards', set()))
            for route in employer_routes(roots):
                if route not in roots:
                    roots.append(route)
            seed = deque()
            if fallback:
                LOG.info('Tavily fallback triggered: %s', s['_fallback_reason'])
                try:
                    # One combined query, rather than five searches per company.
                    hits = await self.web.search(s['company']['company'] +
                        ' software engineer senior backend full stack forward deployed engineer United States careers jobs',
                        sorted({host(r) for r in roots}))
                    seed.extend((canonical(h['url']), 'search') for h in hits
                                if in_scope(h['url'], roots) and crawl_kind(h['url'], h.get('title', '')))
                except Exception as exc:
                    warning('Search fallback failed: ' + retrieval_error(exc))
            else:
                LOG.info('Direct retrieval first; Tavily supplemental search deferred')
                preferred = {canonical(r) for r in employer_routes(s['roots'])}
                seed.extend((r, 'root') for r in sorted(roots, key=lambda r: canonical(r) not in preferred))
                seed.extendleft((r, 'portal') for r in reversed(roots) if ats_identity(r))
            visited = set(s.get('_visited', set()))
            pages = s.get('_direct_pages', 0)
            reserve = min(15, self.args.max_pages // 4) if self.args.search_fallback and os.getenv('TAVILY_API_KEY') else 0
            page_budget = self.args.max_pages if fallback else self.args.max_pages - reserve
            while seed:
                url, label = seed.popleft()
                if url in visited:
                    continue
                visited.add(url)
                if any((ats_identity(route) is not None and ats_identity(route) in successful_boards) or
                       any(j.description and in_scope(j.url, [route]) for j in jobs)
                       for route in employer_routes([url]) if canonical(route) != canonical(url)):
                    continue  # Established API board already covers this employer's marketing routes.
                identity = ats_identity(url)
                if identity and identity not in fetched_boards:
                    fetched_boards.add(identity)
                    try:
                        board_key = self.company_key(s, 'board') + ':' + digest(identity)
                        saved_board = self.store.get(board_key)
                        if saved_board is not None:
                            LOG.info('ATS board checkpoint reused: %s', url)
                            board_jobs = [Job(**j) for j in saved_board]
                        else:
                            warning_count = len(warnings)
                            board_jobs = await self.web.ats(url, warning)
                            if len(warnings) == warning_count:
                                self.store.put(board_key, [j.model_dump() for j in board_jobs])
                        jobs.extend(board_jobs)
                        LOG.info('ATS board collected: %s postings=%d', identity[0], len(board_jobs))
                        # Employer ATS APIs can endorse custom-hosted job URLs.
                        for job in board_jobs:
                            if job.url and not in_scope(job.url, roots):
                                roots.append(canonical(job.url))
                        successful_boards.add(identity)
                        continue
                    except Exception as exc:
                        warning(f'{identity[0]} adapter failed; using public pages: {retrieval_error(exc)}')
                elif identity in successful_boards:
                    continue
                if pages >= page_budget:
                    warning('Generic/iCIMS page limit reached; coverage is partial' if fallback or not reserve else
                            'Direct crawl budget reached; coverage is partial (pages reserved for conditional search fallback)')
                    break
                pages += 1
                LOG.info('Crawl page %d/%d kind=%s url=%s', pages, self.args.max_pages, label, url)
                try:
                    final, html = await self.web.html(url)
                    if re.search(r'/login|/register|/authsvc|/userhome', urlsplit(final).path, re.I) or host(final).startswith(('login.', 'auth.')):
                        warning('Skipped sign-in redirect: ' + final)
                        continue
                    # Marketing copy can conceal a JS-only listing despite a long body.
                    # Retry only pages without useful job/portal links, and only when requested.
                    if isinstance(self.web, Web) and self.web.render and label in ('root', 'listing', 'portal'):
                        useful_links = [target for target, text, tag in links(html, final)
                                        if crawl_kind(target, text) == 'detail' or ats_identity(target)]
                        has_job_data = 'JobPosting' in html
                        if not useful_links and not has_job_data:
                            final, html = await self.web.html(final, force_render=True)
                            if re.search(r'/login|/register|/authsvc|/userhome', urlsplit(final).path, re.I) or host(final).startswith(('login.', 'auth.')):
                                warning('Skipped sign-in redirect: ' + final)
                                continue
                    # Root redirects and same employer-host redirects establish portal scope.
                    if canonical(final) != canonical(url) and (label == 'root' or host(final) == host(url)):
                        if canonical(final) not in roots:
                            roots.append(canonical(final))
                    elif not in_scope(final, roots):
                        warning('Skipped unrelated redirect: ' + final)
                        continue
                    if ats_identity(final) and ats_identity(final) not in fetched_boards:
                        seed.appendleft((canonical(final), 'root'))
                    for target, text, tag in links(html, final):
                        kind = crawl_kind(target, text)
                        if not kind and tag != 'iframe':
                            continue
                        identity = ats_identity(target)
                        portal = host(target).endswith('.icims.com') and (tag == 'iframe' or re.search(r'career|job|opening|position', text, re.I))
                        if identity or portal or recruiting_host(target, s['company']):
                            if not in_scope(target, roots):
                                selection = CareerChoice(url=target) if recruiting_host(target, s['company']) else await self.ask(CareerChoice,
                                    'Approve this linked recruiting portal only if the supplied employer page identifies '
                                    'it as recruiting for the named employer. Do not approve partner jobs, advertisements, '
                                    'vendor homepages or unrelated embedded boards. Return exactly target_url or empty.',
                                    {'company': s['company']['company'], 'source_url': final, 'target_url': target,
                                     'link_text': text, 'page_text': plain(html)[:12000]}, 'portal:' + target)
                                if selection.url != target:
                                    continue
                            if not in_scope(target, roots):
                                roots.append(target)
                            seed.appendleft((target, 'portal'))
                        elif in_scope(target, roots):
                            # iCIMS next/start offsets are followed exactly as linked.
                            if kind == 'detail':
                                seed.appendleft((target, 'detail'))
                            elif kind == 'listing':
                                seed.append((target, 'listing'))
                    soup = BeautifulSoup(html, 'html.parser')
                    headings = ' '.join(tag.get_text(' ', strip=True) for tag in soup.select('title,h1,h2'))
                    has_ld = any('JobPosting' in tag.get_text() for tag in soup.select('script[type="application/ld+json"]'))
                    # No first-600-character filter: inspect title/headings and detail URLs.
                    if has_ld or relevant_title(headings) or label in ('search', 'detail'):
                        item = await self.page_job(final, s['company']['company'], roots, fetched=(final, html))
                        if item:
                            jobs.append(item)
                            LOG.info('Job description collected: %s | %s', item.title, item.location)
                except Exception as exc:
                    warning('Page retrieval/extraction failed: ' + retrieval_error(exc) + ': ' + url)
            unique = {}
            for job in jobs:
                if relevant_title(job.title) and job.description and job.url:
                    unique.setdefault(canonical(job.url), job)
            if not unique:
                warning('No usable relevant job descriptions retrieved; this is not evidence that the employer has no openings. '
                        'For JavaScript-only pages, install Playwright and retry with --render; check the HTTP/error details above.')
            # Assess location BEFORE the shortlist; only confirmed US jobs reach ranking.
            located = []
            location_counts = {'yes': 0, 'no': 0, 'unknown': 0}
            all_jobs = []
            local_skipped = 0
            for j in unique.values():
                answer = local_country_status(j.location, j.country_codes)
                if answer == 'no':
                    location_counts['no'] += 1
                    local_skipped += 1
                elif answer == 'yes':
                    location_counts['yes'] += 1
                    located.append(('yes', j))
                else:
                    all_jobs.append(j)
            for start in range(0, len(all_jobs), 30):
                group = all_jobs[start:start + 30]
                LOG.info('US location check: batch=%d/%d jobs=%d', start // 30 + 1, (len(all_jobs) + 29) // 30, len(group))
                evidence = {}
                for j in group:
                    sentences = re.split(r'(?<=[.!?])\s+|\n', j.description)
                    relevant = [line for line in sentences if re.search(r'United States|\bUSA?\b|remote|location|based in|work from', line, re.I)]
                    evidence[j.id] = j.location + '\n' + '\n'.join(relevant)[:6000]
                try:
                    output = await self.ask(Locations,
                        'Determine whether each posting permits working anywhere within the United States or at a specific US location. '
                        'US on-site/hybrid/remote all qualify. Any US city qualifies; no candidate address is relevant. '
                        'Remote alone is unknown. Explicitly overseas-only is no. Quote supplied text exactly. '
                        'Return one decision per job_id.',
                        {'jobs': [{'job_id': j.id, 'location_evidence': evidence[j.id]} for j in group]},
                        self.company_key(s, 'locations') + ':' + digest(evidence))
                    decisions = {d.job_id: d for d in output.decisions}
                    for j in group:
                        d = decisions.get(j.id)
                        answer = d.us_eligible if d and d.quote and d.quote in evidence[j.id] else 'unknown'
                        location_counts[answer] += 1
                        if answer == 'yes':
                            located.append((answer, j))
                except Exception as exc:
                    warning('Location prefilter incomplete: ' + type(exc).__name__)
                    location_counts['unknown'] += len(group)
            if location_counts['unknown']:
                warning(f'Skipped {location_counts["unknown"]} roles with unconfirmed US location before resume matching')
            skill_tokens = set(x.lower() for x in self.candidate.skills)
            def shortlist(pair):
                country, j = pair
                text = (j.title + ' ' + j.description).lower()
                return (0 if country == 'yes' else 1,
                        level_priority('sde', title_seniority(j.title), self.profile),
                        -sum(x in text for x in skill_tokens), j.url)
            candidates = [j.model_copy(update={'us_eligible': 'yes'}) for _, j in sorted(located, key=shortlist)]
            if self.args.max_candidates and len(candidates) > self.args.max_candidates:
                warning(f'Shortlisted {self.args.max_candidates}/{len(candidates)} roles; increase --max-candidates or set 0 for all')
                candidates = candidates[:self.args.max_candidates]
            LOG.info('US filter: relevant=%d US=%d non_US=%d unknown=%d locally_skipped=%d shortlisted=%d',
                     len(unique), location_counts['yes'], location_counts['no'], location_counts['unknown'], local_skipped, len(candidates))
            retrieval_incomplete = any(re.search(r'failed|blocked|incomplete|partial|limit reached|No usable', w, re.I) for w in warnings)
            if not fallback and self.args.search_fallback and (not candidates or retrieval_incomplete):
                reason = 'no relevant confirmed-US jobs found' if not candidates else 'direct retrieval was incomplete'
                if os.getenv('TAVILY_API_KEY'):
                    return await work(dict(s) | {'roots': roots, 'warnings': warnings, '_search_phase': True,
                        '_fallback_reason': reason, '_direct_jobs': list(unique.values()), '_direct_pages': pages,
                        '_visited': visited, '_fetched_boards': fetched_boards, '_successful_boards': successful_boards})
                warning('Tavily fallback needed (' + reason + ') but TAVILY_API_KEY is not set; returning direct results')
            elif not fallback:
                LOG.info('Tavily supplemental search skipped: %s', 'disabled' if not self.args.search_fallback else 'direct retrieval succeeded')
            return {'roots': roots, 'jobs': [j.model_dump() for j in candidates], 'warnings': warnings,
                    'metrics': {'retrieved_relevant': len(unique), 'shortlisted': len(candidates), 'generic_pages': pages,
                                'locations': location_counts, 'non_us_skipped_locally': local_skipped,
                                'tavily_fallback_queries': int(fallback)}}
        return await self.stage(state, 'fetch', work)

    def assessment_key(self, job: Job):
        return 'job-assessment-v1:' + digest({'model': self.model_name,
            'profile': self.profile.model_dump(), 'candidate': self.candidate.model_dump(),
            'job': scoring_job(job)})

    async def evaluate(self, jobs: list[Job], key):
        jobs = [j for j in jobs if local_country_status(j.location, j.country_codes) != 'no']
        if not jobs:
            return []
        requested = list(jobs)
        cached_assessments, pending = {}, []
        for job in jobs:
            cached = self.store.get(self.assessment_key(job))
            if cached is not None:
                assessment = Assessment.model_validate(cached)
                if assessment.job_id == job.id:
                    cached_assessments[job.id] = assessment
                    self.metrics['assessment_cache_hits'] += 1
                    continue
            pending.append(job)
        if cached_assessments:
            LOG.info('Unchanged job assessments reused: %d; new/changed=%d', len(cached_assessments), len(pending))
        if not pending:
            return [cached_assessments[job.id] for job in requested]
        jobs = pending
        matching_candidate = self.candidate.model_dump()
        if self.profile.experience_years_override is not None:
            matching_candidate['experience_years'] = self.profile.experience_years_override
        data = {'candidate': matching_candidate, 'preferences': self.profile.role_preferences,
                'declared_experience_years': self.profile.experience_years_override,
                'prefer_non_senior_roles': self.profile.prefer_non_senior_roles,
                'location_requirement': 'Any location within the United States is acceptable; US remote, on-site and hybrid qualify.',
                'jobs': [scoring_job(j) for j in jobs]}
        output = await self.ask(BatchAssessment,
            'Assess every job_id exactly once. Fit: skills/domain 40, level/experience 30, demonstrated responsibilities 30. '
            'Use declared_experience_years when supplied as the authoritative professional experience, '
            'even if resume dates, summary, or previous titles imply a different level. '
            'Classify seniority as junior/mid/senior/staff/principal/unknown from the JD and title. '
            'When prefer_non_senior_roles is true, junior and mid-level backend/full-stack/SDE/FDE roles are preferred. '
            'Do not penalize them just because the candidate previously held a Senior title. '
            'Do not equate junior preference with internships or student-only jobs. '
            'Senior SDE: production systems, ownership, reliability, distributed systems; '
            'FDE: hands-on coding, customer/problem discovery, integrations, deployment and AI workflows; '
            'SDE: coding fundamentals, stack and delivery. Do not demand FDE experience for SDE roles. '
            'Classify sde/senior_sde/fde/other. Backend and full-stack engineering are included: '
            'classify non-senior backend/full-stack roles as sde and senior/staff/principal backend/full-stack roles as senior_sde. '
            'Do not mark these roles other just because their titles do not say Software Engineer. '
            'Preserve supplied confirmed job us_eligible=yes; it was checked before resume matching. '
            'For unconfirmed jobs US eligibility must be explicit in location/JD; remote alone is unknown. '
            'Sponsorship exclusion ONLY for explicit no-sponsorship language for THIS role. '
            'No candidate address or visa status is used. Location anywhere in the US qualifies. '
            'hard_block ONLY for explicit student-only or other non-visa requirements contradicted by the resume; '
            'do not infer a citizenship/clearance/visa incompatibility because candidate status is not supplied. '
            'Quotes must be exact substrings of the supplied location/description. '
            'Extract required experience only when stated. Extract min_required_experience_years from '
            'the minimum required professional experience for this role, with experience_requirement_quote '
            'copied exactly from the JD. For 5-8 years use 5; for 7+ or at least 7 use 7. '
            'Use the least required experience across explicit alternative qualification paths the '
            'candidate qualifies for based on their stated education. For bachelors+8 or masters+5, '
            'use 5 only if the candidate satisfies the stated masters qualification; otherwise use 8 '
            'if the bachelors path is satisfied. Do not add overlapping skill-specific '
            'requirements together. Ignore preferred/nice-to-have experience, company age and candidate '
            'experience. If no required minimum is stated, use null and an empty quote. '
            'Roles requiring a minimum above 6 years are excluded by policy, independent of fit. '
            'Give concrete matching skills and gaps.\n\n' + RANKING_POLICY, data, key)
        by_id, result, seen = {j.id: j for j in jobs}, [], set()
        for a in output.assessments:
            if a.job_id not in by_id or a.job_id in seen:
                continue
            seen.add(a.job_id)
            job = by_id[a.job_id]
            explicit_level = title_seniority(job.title)
            if explicit_level != 'unknown':
                a.seniority = explicit_level
            if job.us_eligible == 'yes' or local_country_status(job.location, job.country_codes) == 'yes':
                a.us_eligible = 'yes'
            evidence = job.location + ' ' + job.description + ' ' + ' '.join(job.country_codes)
            if not a.experience_requirement_quote or a.experience_requirement_quote not in job.description:
                a.min_required_experience_years = None
                a.experience_requirement_quote = ''
            if not (a.sponsorship_quote and a.sponsorship_quote in job.description and explicitly_no_sponsor(a.sponsorship_quote)):
                a.sponsorship_not_available = False
                a.sponsorship_quote = ''
            if not a.hard_block_quote or a.hard_block_quote not in evidence:
                a.hard_block = False
                a.hard_block_quote = ''
            result.append(a)
        if len(seen) != len(jobs):
            raise ValueError('Ranker omitted or duplicated job IDs; batch will be retried on resume')
        for assessment in result:
            self.store.put(self.assessment_key(by_id[assessment.job_id]), assessment.model_dump())
        combined = cached_assessments | {a.job_id: a for a in result}
        return [combined[j.id] for j in requested]

    def rejection(self, a: Assessment):
        if a.min_required_experience_years is not None and a.min_required_experience_years > JD_MAX_REQUIRED_EXPERIENCE:
            return 'experience_requirement_over_6'
        if a.sponsorship_not_available:
            return 'explicit_no_sponsorship'
        if a.us_eligible != 'yes':
            return 'location_not_confirmed_us'
        if a.hard_block:
            return 'hard_requirement_conflict'
        if a.role not in self.profile.role_preferences:
            return 'role_mismatch'
        if a.fit_score < self.profile.min_fit_score:
            return 'low_fit'
        return ''

    async def rank(self, state):
        jobs = [Job(**j) for j in state['jobs']]
        LOG.info('Ranking started: jobs=%d batches=%d', len(jobs), (len(jobs) + 3) // 4)
        assessments, warnings = [], list(state['warnings'])
        for offset in range(0, len(jobs), 4):
            batch = jobs[offset:offset + 4]
            LOG.info('Ranking batch %d/%d: jobs=%d', offset // 4 + 1, (len(jobs) + 3) // 4, len(batch))
            try:
                assessments.extend(await self.evaluate(batch, self.company_key(state, 'rank-batch') + ':' + digest([j.model_dump() for j in batch])))
            except Exception as exc:
                LOG.error('Ranking batch %d failed: %s', offset // 4 + 1, type(exc).__name__)
                warnings.append('Ranking batch incomplete: ' + type(exc).__name__)
        counts = {}
        for a in assessments:
            reason = self.rejection(a) or 'eligible'
            counts[reason] = counts.get(reason, 0) + 1
        LOG.info('Ranking finished: assessed=%d decisions=%s', len(assessments), json.dumps(counts))
        return {'assessments': [a.model_dump() for a in assessments], 'warnings': warnings,
                'metrics': state['metrics'] | {'assessed': len(assessments), 'decisions': counts}}

    async def verify_one(self, job: Job, state, ashby_cache):
        # Re-read the current API detail, or current board membership for Ashby.
        if job.source == 'ashby':
            identity = ats_identity(job.board_url)
            if identity not in ashby_cache:
                ashby_cache[identity] = {j.id: j for j in await self.web.ats(job.board_url, state['warnings'].append)}
            current = ashby_cache[identity].get(job.id)
            if not current:
                return None
        elif job.detail_api:
            data = await self.web.data('GET', job.detail_api)
            kind, token = ats_identity(job.board_url)
            if kind == 'workday':
                data['_job_url'], data['_detail_api'] = job.url, job.detail_api
            elif kind == 'eightfold':
                if data.get('isPrivate'):
                    return None
                data['_job_url'] = job.url
            current = self.web.normalize(job.board_url, kind, token, data)
        else:
            current = await self.page_job(job.url, state['company']['company'], state['roots'], fresh=True)
        if not current or not relevant_title(current.title) or local_country_status(current.location, current.country_codes) == 'no':
            return None
        # Also confirm the human-facing link is reachable. Thin ATS shells are
        # acceptable only when the fresh employer API confirms active membership.
        response = await self.web.request('GET', current.url)
        if not in_scope(str(response.url), state['roots']):
            raise ValueError('Application link redirected outside employer scope')
        if re.search(r'(job|position|posting).{0,50}(no longer available|has been filled|has expired)', plain(response.text), re.I):
            return None
        return current

    async def verify(self, state):
        warnings = list(state['warnings'])
        state = dict(state) | {'warnings': warnings}
        jobs = {j['id']: Job(**j) for j in state['jobs']}
        eligible = [Assessment(**a) for a in state['assessments'] if not self.rejection(Assessment(**a))]
        eligible.sort(key=lambda a: rank_key(a, self.profile))
        LOG.info('Verification started: eligible=%d pool=%d', len(eligible), min(len(eligible), self.args.verify_pool))
        verified, checked, ashby_cache = [], 0, {}
        # Verify a pool, then re-rank; changed JDs get fresh assessments.
        for old in eligible[:self.args.verify_pool]:
            checked += 1
            LOG.info('Verifying link %d/%d: %s', checked, min(len(eligible), self.args.verify_pool), jobs[old.job_id].url)
            try:
                current = await self.verify_one(jobs[old.job_id], state, ashby_cache)
                if current is None:
                    LOG.warning('Closed/unavailable role skipped: %s', jobs[old.job_id].url)
                    warnings.append('Closed/unavailable role skipped: ' + jobs[old.job_id].url)
                    continue
                changed = (current.description, current.location, current.title) != (jobs[old.job_id].description, jobs[old.job_id].location, jobs[old.job_id].title)
                if (current.location, current.country_codes) == (jobs[old.job_id].location, jobs[old.job_id].country_codes):
                    current.us_eligible = jobs[old.job_id].us_eligible
                a = (await self.evaluate([current], 'fresh-verify:' + current.id + ':' + digest(current.model_dump())))[0] if changed else old
                if not self.rejection(a):
                    verified.append((a, current))
                    LOG.info('Verified: %s fit=%d', current.title, a.fit_score)
            except Exception as exc:
                LOG.warning('Link verification failed: %s | %s', retrieval_error(exc), jobs[old.job_id].url)
                warnings.append('Link verification failed: ' + type(exc).__name__ + ': ' + jobs[old.job_id].url)
        if len(eligible) > self.args.verify_pool:
            warnings.append('Verification pool capped; unverified eligible roles remain')
        verified.sort(key=lambda pair: rank_key(pair[0], self.profile))
        rows = []
        for a, job in verified[:MAX_MATCHES_PER_COMPANY]:
            rows.append({'company': state['company']['company'], 'company_rank': len(rows) + 1,
                         'title': job.title, 'role': a.role, 'seniority': a.seniority, 'location': job.location,
                         'fit_score': a.fit_score, 'job_url': job.url, 'why_match': a.reason,
                         'matched_skills': '; '.join(a.matched_skills), 'gaps': '; '.join(a.gaps),
                         'required_experience': a.required_experience, 'us_eligible': a.us_eligible,
                         'sponsorship': 'not_excluded_by_jd', 'source': job.source,
                         'posted_at': job.posted_at, 'link_verified': True,
                         'checked_at': datetime.now(timezone.utc).isoformat()})
        LOG.info('Verification finished: links_checked=%d verified=%d exported_matches=%d', checked, len(verified), len(rows))
        return {'rows': rows, 'warnings': warnings,
                'metrics': state['metrics'] | {'verified_pool': len(verified), 'links_checked': checked},
                'status': ('matched' if rows else 'no_verified_matches' if state['jobs'] else
                           'retrieval_incomplete' if warnings else 'no_jobs_found')}

    def graph(self):
        builder = StateGraph(State)
        for name, function in [('discover', self.discover), ('fetch', self.fetch), ('rank', self.rank), ('verify', self.verify)]:
            builder.add_node(name, function)
        for before, after in [(START, 'discover'), ('discover', 'fetch'), ('fetch', 'rank'), ('rank', 'verify'), ('verify', END)]:
            builder.add_edge(before, after)
        return builder.compile()
