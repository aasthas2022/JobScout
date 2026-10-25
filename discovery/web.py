"""HTTP, search, browser-rendering, and ATS integrations."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import deque
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from .config import LOG
from .models import Job
from .utils import (address_countries, ats_identity, canonical, crawl_kind, digest,
                    explicitly_no_sponsor, host, in_scope, links, local_country_status,
                    plain, public_url, recruiting_host, relevant_title, retrieval_error,
                    structured_page_job, us_hint, visible_soup)

class Web:
    def __init__(self, client: httpx.AsyncClient, render=False, store=None):
        self.client, self.render = client, render
        self.store = store
        self.search_locks = {}
        self.search_memory = {}
        self.stats = {'tavily_search_requests': 0, 'tavily_search_cache_hits': 0, 'tavily_reported_credits': 0}
        self.locks = {}
        self.last = {}

    async def request(self, method: str, url: str, **kwargs):
        for attempt in range(4):
            target = url
            try:
                for _ in range(6):
                    await asyncio.to_thread(public_url, target)
                    h = host(target)
                    LOG.debug('HTTP %s %s%s attempt=%d/4', method, h, urlsplit(target).path, attempt + 1)
                    async with self.locks.setdefault(h, asyncio.Lock()):
                        await asyncio.sleep(max(0, .35 - (time.monotonic() - self.last.get(h, 0))))
                        response = await self.client.request(method, target, follow_redirects=False, **kwargs)
                        self.last[h] = time.monotonic()
                    LOG.debug('HTTP response: status=%d host=%s bytes=%d', response.status_code, h, len(response.content))
                    if response.status_code in (301, 302, 303, 307, 308):
                        if method != 'GET':
                            raise ValueError('API redirect refused')
                        target = urljoin(target, response.headers['location'])
                        continue
                    if response.status_code == 429 or response.status_code >= 500:
                        LOG.warning('HTTP %d from %s; retry=%d/4 delay=%ds', response.status_code, h, attempt + 1, min(30, 2 ** attempt))
                        await asyncio.sleep(min(30, 2 ** attempt))
                        break
                    response.raise_for_status()
                    if len(response.content) > 20_000_000:
                        raise ValueError('Response exceeded 20 MB')
                    return response
                else:
                    raise ValueError('Too many redirects')
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                LOG.warning('HTTP %s from %s; attempt=%d/4', type(exc).__name__, host(target), attempt + 1)
                if attempt == 3:
                    raise
                await asyncio.sleep(2 ** attempt)
        raise RuntimeError('Request retries exhausted')

    async def data(self, method: str, url: str, **kwargs):
        return (await self.request(method, url, **kwargs)).json()

    async def search(self, query: str, domains=None):
        options = {'query': query, 'max_results': 10, 'search_depth': 'advanced',
                   'include_domains': sorted(set(domains or [])), 'include_answer': False,
                   'include_raw_content': False, 'include_images': False, 'include_usage': True}
        cache_key = 'tavily-search-v1:' + digest(options)
        async with self.search_locks.setdefault(cache_key, asyncio.Lock()):
            cached = self.search_memory.get(cache_key)
            if cached is None and self.store is not None:
                cached = self.store.get(cache_key)
            if cached is not None:
                self.stats['tavily_search_cache_hits'] += 1
                LOG.info('Tavily search cache reused: %s', query)
                return cached
            results = await self.search_uncached(query, options)
            self.search_memory[cache_key] = results
            if self.store is not None:
                self.store.put(cache_key, results)
            return results

    async def search_uncached(self, query: str, options: dict):
        LOG.info('Search discovery: %s', query)
        if not os.getenv('TAVILY_API_KEY'):
            raise ValueError('Set TAVILY_API_KEY for careers discovery/search fallback')
        payload = dict(options) | {'api_key': os.environ['TAVILY_API_KEY']}
        self.stats['tavily_search_requests'] += 1
        response = await self.data('POST', 'https://api.tavily.com/search', json=payload)
        self.stats['tavily_reported_credits'] += (response.get('usage') or {}).get('credits', 0)
        # Keep all discovery evidence, omit unused provider metadata.
        results = [{k: hit.get(k, '') for k in ('url', 'title', 'content', 'score')}
                   for hit in response.get('results', [])]
        LOG.info('Search returned %d links', len(results))
        return results

    async def html(self, url: str, *, force_render: bool = False):
        response = await self.request('GET', url)
        dynamic_jobs_host = host(str(response.url)) in ('www.metacareers.com', 'jobs.apple.com', 'jobs.nvidia.com',
            'careers.qualcomm.com', 'careers.amd.com', 'explore.jobs.netflix.net', 'jobs.netflix.com',
            'jobs.uber.com', 'careers.microsoft.com', 'joinbytedance.com', 'www.ibm.com',
            'careers.ibm.com', 'careers.zoom.us', 'www.careers.zoom.us')
        if not self.render or (not force_render and not dynamic_jobs_host and len(visible_soup(response.text).get_text(' ', strip=True)) >= 1500):
            return str(response.url), response.text
        from playwright.async_api import async_playwright
        LOG.info('Rendering JavaScript page: %s', url)
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                context = await browser.new_context(service_workers='block')
                async def guard(route):
                    try:
                        await asyncio.to_thread(public_url, route.request.url)
                        await route.continue_()
                    except Exception:
                        await route.abort()
                await context.route('**/*', guard)
                page = await context.new_page()
                await page.goto(str(response.url), wait_until='domcontentloaded', timeout=45000)
                try:
                    await page.wait_for_function('document.body && document.body.innerText.length > 500', timeout=15000)
                except Exception:
                    pass  # Page extraction reports empty shells/blocked pages below.
                await page.wait_for_timeout(2000)
                return page.url, await page.content()
            finally:
                await browser.close()

    def normalize(self, root: str, kind: str, token: str, j: dict) -> Job:
        api = ''
        posted = ''
        countries = []
        if kind == 'greenhouse':
            jid, title = str(j['id']), j['title']
            location, description = j['location']['name'], plain(j.get('content', ''))
            url, posted = j['absolute_url'], j.get('updated_at', '')
            api = f'https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{jid}'
        elif kind.startswith('lever'):
            jid, title = j['id'], j['text']
            c = j.get('categories') or {}
            location = '; '.join(c.get('allLocations') or [c.get('location', '')]) + ' ' + (j.get('country') or '')
            description = ' '.join(j.get(k) or '' for k in ('openingPlain', 'descriptionPlain', 'descriptionBodyPlain', 'additionalPlain'))
            description += ' ' + ' '.join(plain(x.get('content', '')) for x in j.get('lists', []))
            description += ' ' + plain(j.get('additional', ''))
            url = j['hostedUrl']
            base = 'https://api.eu.lever.co' if kind == 'lever_eu' else 'https://api.lever.co'
            api = f'{base}/v0/postings/{token}/{jid}?mode=json'
            countries = address_countries({'country': j.get('country')})
        elif kind == 'ashby':
            jid, title = j['jobUrl'].rstrip('/').split('/')[-1], j['title']
            location = '; '.join([j.get('location', '')] + [x.get('location', '') for x in j.get('secondaryLocations', [])])
            location += ' ' + json.dumps(j.get('address') or {}) + ' ' + json.dumps(j.get('secondaryLocations') or [])
            description = j.get('descriptionPlain') or plain(j.get('descriptionHtml', ''))
            url, posted = j['jobUrl'], j.get('publishedAt', '')
            countries = address_countries([j.get('address', {}), j.get('secondaryLocations', [])])
        elif kind == 'smartrecruiters':
            jid, title = str(j['id']), j['name']
            location = json.dumps(j.get('location') or {})
            sections = (j.get('jobAd') or {}).get('sections') or {}
            description = ' '.join(plain(section.get('text', '')) for section in sections.values())
            url = j.get('applyUrl') or ''
            posted = j.get('releasedDate', '')
            api = f'https://api.smartrecruiters.com/v1/companies/{token}/postings/{jid}'
            countries = address_countries(j.get('location', {}))
        elif kind == 'workday':
            info = j.get('jobPostingInfo') or {}
            jid, title = str(info.get('jobReqId') or digest(info)), info.get('title', '')
            location = info.get('location', '') + ' ' + '; '.join(str(x) for x in (info.get('additionalLocations') or []))
            description = plain(info.get('jobDescription', ''))
            url = j['_job_url']
            api, posted = j['_detail_api'], info.get('startDate', '')
            countries = address_countries({'country': info.get('country'), 'locations': info.get('additionalLocations', [])})
        elif kind == 'eightfold':
            if j.get('isPrivate'):
                raise ValueError('Eightfold posting is private or unavailable')
            jid, title = str(j['id']), j.get('posting_name') or j['name']
            location = '; '.join(j.get('locations') or [j.get('location', '')])
            description = plain(j.get('job_description', ''))
            url = j.get('canonicalPositionUrl') or j.get('_job_url', '')
            if not url:
                raise ValueError('Eightfold posting has no source-provided job URL')
            board_host, domain = token.split('/', 1)
            api = f'https://{board_host}/api/apply/v2/jobs/{quote(jid, safe="")}?domain={quote(domain, safe="")}'
            timestamp = j.get('t_create') or j.get('t_update')
            posted = datetime.fromtimestamp(float(timestamp), timezone.utc).isoformat() if timestamp else ''
            countries = address_countries(j.get('locations', []))
        else:
            raise ValueError('Unknown ATS')
        return Job(id=f'{kind}:{token}:{jid}', external_id=jid, title=title,
                   location=location, description=description, url=canonical(url), source=kind,
                   board_url=root, detail_api=api, posted_at=posted,
                   country_codes=countries)

    async def ats(self, root: str, warning):
        kind, token = ats_identity(root)
        LOG.info('Reading %s ATS board: %s', kind, root)
        jobs = []
        if kind == 'greenhouse':
            data = await self.data('GET', f'https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true')
            return [self.normalize(root, kind, token, j) for j in data.get('jobs', [])]
        if kind == 'ashby':
            data = await self.data('GET', f'https://api.ashbyhq.com/posting-api/job-board/{token}')
            return [self.normalize(root, kind, token, j) for j in data.get('jobs', []) if j.get('isListed') is not False]
        # Read all list pages; fetch descriptions only for relevant titles.
        offset, seen = 0, set()
        while offset < 10000:
            if kind.startswith('lever'):
                base = 'https://api.eu.lever.co' if kind == 'lever_eu' else 'https://api.lever.co'
                batch = await self.data('GET', f'{base}/v0/postings/{token}', params={'mode': 'json', 'skip': offset, 'limit': 100})
                total = None
            elif kind == 'smartrecruiters':
                data = await self.data('GET', f'https://api.smartrecruiters.com/v1/companies/{token}/postings', params={'offset': offset, 'limit': 100})
                batch, total = data.get('content', []), data.get('totalFound')
            elif kind == 'eightfold':
                board_host, domain = token.split('/', 1)
                base = f'https://{board_host}/api/apply/v2/jobs'
                data = await self.data('GET', base, params={'domain': domain, 'num': 100, 'start': offset})
                batch, total = data.get('positions', []), data.get('count')
            else:  # Workday CXS; no claim of a stable public API contract.
                tenant, site = token.split('/', 1)
                origin = f'https://{host(root)}'
                base = f'{origin}/wday/cxs/{tenant}/{site}'
                data = await self.data('POST', base + '/jobs', json={'appliedFacets': {}, 'limit': 20, 'offset': offset, 'searchText': ''})
                batch, total = data.get('jobPostings', []), data.get('total')
            LOG.info('ATS %s page: offset=%d received=%d total=%s', kind, offset, len(batch), total if total is not None else 'unknown')
            if not batch:
                break
            for j in batch:
                identity = str(j.get('id') or j.get('externalPath') or digest(j))
                if identity in seen:
                    warning('ATS repeated a list page; coverage may be partial')
                    return jobs
                seen.add(identity)
                title = j.get('text') or j.get('name') or j.get('title', '')
                if not relevant_title(title):
                    continue
                if kind == 'eightfold' and j.get('isPrivate'):
                    continue
                if kind == 'eightfold' and local_country_status('; '.join(j.get('locations') or [j.get('location', '')]), []) == 'no':
                    continue  # Country is available on this board's list response.
                try:
                    if kind == 'smartrecruiters':
                        j = await self.data('GET', f'https://api.smartrecruiters.com/v1/companies/{token}/postings/{quote(str(j["id"]), safe="")}')
                    elif kind == 'workday':
                        external = j['externalPath']
                        endpoint = base + '/' + external.lstrip('/')
                        j = await self.data('GET', endpoint)
                        j['_job_url'] = origin + '/en-US/' + site + '/' + external.lstrip('/')
                        j['_detail_api'] = endpoint
                    elif kind == 'eightfold':
                        listed_url = j.get('canonicalPositionUrl', '')
                        j = await self.data('GET', base + '/' + quote(str(j['id']), safe=''), params={'domain': domain})
                        j['_job_url'] = listed_url
                        if not j.get('job_description'):
                            raise ValueError('Eightfold detail returned no job description')
                    jobs.append(self.normalize(root, kind, token, j))
                except Exception as exc:
                    warning('ATS detail failed: ' + retrieval_error(exc))
            offset += len(batch)
            if total is not None and offset >= total:
                break
            if kind.startswith('lever') and len(batch) < 100:
                break
        else:
            warning('ATS pagination safety cap reached')
        return jobs
