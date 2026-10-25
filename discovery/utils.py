"""Shared parsing, URL, ranking, and persistence helpers."""
from __future__ import annotations

import csv
import hashlib
import ipaddress
import json
import logging
import re
import socket
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from .config import LOG
from .models import Assessment, Company, Job, Profile

def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()

def company_filenames(companies: list[Company]) -> dict[str, str]:
    """Readable output names, with short suffixes only to prevent collisions."""
    groups = {}
    for company in companies:
        ascii_name = unicodedata.normalize('NFKD', company.company).encode('ascii', 'ignore').decode()
        slug = re.sub(r'[^a-z0-9]+', '_', ascii_name.lower()).strip('_')[:96].rstrip('_')
        slug = slug or 'company'
        if slug in {'con', 'prn', 'aux', 'nul'} or re.fullmatch(r'(com|lpt)[1-9]', slug):
            slug += '_company'
        groups.setdefault(slug, []).append(company)
    filenames = {}
    for slug, group in groups.items():
        for company in group:
            suffix = '_' + digest(company.company)[:12] if len(group) > 1 or slug == 'company' else ''
            filenames[company.company.casefold()] = slug + suffix + '.json'
    return filenames

def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)

class Checkpoints:
    """Stage/batch files: a ranking failure doesn't discard collected jobs."""
    def __init__(self, folder: Path, reuse: bool, ttl: float):
        self.folder, self.reuse, self.ttl = folder, reuse, ttl
        folder.mkdir(parents=True, exist_ok=True)

    def get(self, key: str):
        path = self.folder / (digest(key) + '.json')
        if not self.reuse or not path.exists() or time.time() - path.stat().st_mtime >= self.ttl:
            return None
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return None

    def put(self, key: str, value):
        atomic_json(self.folder / (digest(key) + '.json'), value)

    def discard(self, key: str):
        (self.folder / (digest(key) + '.json')).unlink(missing_ok=True)

def host(url: str) -> str:
    return (urlsplit(url).hostname or '').lower()

def canonical(url: str) -> str:
    p = urlsplit(url)
    path = p.path
    if (p.hostname or '').lower() == 'www.google.com' and path.startswith('/about/careers/applications/'):
        while '/jobs/results/jobs/results' in path:
            path = path.replace('/jobs/results/jobs/results', '/jobs/results')
    query = {k: v for k, v in parse_qs(p.query).items()
             if not k.lower().startswith('utm_') and k not in ('gh_src', 'source', 'ref')}
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), path.rstrip('/'), urlencode(query, doseq=True), ''))

def public_url(url: str):
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('A public HTTP(S) URL without credentials is required')
    if p.port not in (None, 80, 443):
        raise ValueError('Nonstandard port rejected')
    for result in socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80), type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(result[4][0]).is_global:
            raise ValueError('Private or local URL rejected')

def plain(html: str) -> str:
    return BeautifulSoup(html or '', 'html.parser').get_text(' ', strip=True)

def visible_soup(html: str):
    soup = BeautifulSoup(html or '', 'html.parser')
    for tag in soup.select('script, style, nav, footer, header, [hidden], [style*="display:none"], [style*="display: none"]'):
        tag.decompose()
    return soup

ISO_COUNTRIES = set('AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW'.split())

COUNTRY_NAMES = {
    'united states of america': 'US', 'united states': 'US', 'usa': 'US', 'u.s.a.': 'US', 'u.s.': 'US',
    'united kingdom': 'GB', 'great britain': 'GB', 'uk': 'GB', 'england': 'GB', 'scotland': 'GB',
    'canada': 'CA', 'india': 'IN', 'germany': 'DE', 'france': 'FR', 'australia': 'AU',
    'netherlands': 'NL', 'ireland': 'IE', 'spain': 'ES', 'italy': 'IT', 'sweden': 'SE',
    'switzerland': 'CH', 'poland': 'PL', 'portugal': 'PT', 'denmark': 'DK', 'norway': 'NO',
    'finland': 'FI', 'belgium': 'BE', 'austria': 'AT', 'czech republic': 'CZ', 'czechia': 'CZ',
    'romania': 'RO', 'hungary': 'HU', 'greece': 'GR', 'ukraine': 'UA', 'serbia': 'RS',
    'croatia': 'HR', 'bulgaria': 'BG', 'lithuania': 'LT', 'latvia': 'LV', 'estonia': 'EE',
    'singapore': 'SG', 'japan': 'JP', 'china': 'CN', 'south korea': 'KR', 'hong kong': 'HK',
    'taiwan': 'TW', 'philippines': 'PH', 'vietnam': 'VN', 'thailand': 'TH', 'malaysia': 'MY',
    'indonesia': 'ID', 'new zealand': 'NZ', 'mexico': 'MX', 'brazil': 'BR', 'argentina': 'AR',
    'chile': 'CL', 'colombia': 'CO', 'peru': 'PE', 'costa rica': 'CR', 'uruguay': 'UY',
    'israel': 'IL', 'united arab emirates': 'AE', 'uae': 'AE', 'saudi arabia': 'SA',
    'south africa': 'ZA', 'egypt': 'EG', 'kenya': 'KE', 'nigeria': 'NG', 'pakistan': 'PK',
    'bangladesh': 'BD', 'sri lanka': 'LK', 'turkey': 'TR', 'türkiye': 'TR',
}

def country_code(value) -> str:
    if isinstance(value, dict):
        value = value.get('name') or value.get('descriptor') or value.get('value') or value.get('code') or ''
    text = str(value or '').strip()
    upper = text.upper()
    if upper in ISO_COUNTRIES:
        return upper
    if upper == 'USA':
        return 'US'
    if upper == 'UK':
        return 'GB'
    return COUNTRY_NAMES.get(text.lower(), '')

def address_countries(value) -> list[str]:
    """Only inspect structured location/address data, not employer HQ text."""
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in ('country', 'addresscountry'):
                code = country_code(child)
                if code:
                    found.append(code)
            elif isinstance(child, (dict, list)):
                found.extend(address_countries(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(address_countries(child))
    return list(dict.fromkeys(found))

def jsonld_job_countries(metadata: list[str]) -> list[str]:
    found = []
    def walk(value):
        if isinstance(value, dict):
            types = value.get('@type', [])
            if 'JobPosting' in (types if isinstance(types, list) else [types]):
                found.extend(address_countries(value.get('jobLocation', [])))
                found.extend(address_countries(value.get('applicantLocationRequirements', [])))
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    for text in metadata:
        try:
            walk(json.loads(text))
        except (ValueError, TypeError):
            pass
    return list(dict.fromkeys(found))

def local_country_status(location: str, codes: list[str]) -> str:
    """Free deterministic gate. Unknown is never assumed to mean US."""
    known = {country_code(code) for code in codes} - {''}
    text = location.lower()
    for name, code in COUNTRY_NAMES.items():
        if re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text):
            known.add(code)
    if re.search(r'\bUS\b', location):
        known.add('US')
    if 'US' in known:
        return 'yes'
    if known:
        return 'no'
    return 'unknown'

def relevant_title(title: str) -> bool:
    if re.search(r'\b(intern|internship|director|manager|vice president)\b', title, re.I):
        return False
    return bool(re.search(r'forward[ -]deployed|\bfde\b|software|\bsde\b|full[ -]?stack|back[ -]?end|front[ -]?end|implementation engineer|platform engineer|product engineer|ai engineer|agent engineer', title, re.I))

def ats_identity(url: str):
    p = urlsplit(url)
    parts = [x for x in p.path.split('/') if x]
    h = host(url)
    eightfold_domains = {'explore.jobs.netflix.net': 'netflix.com', 'careers.qualcomm.com': 'qualcomm.com'}
    if h in eightfold_domains and p.path.startswith('/careers'):
        return 'eightfold', h + '/' + eightfold_domains[h]
    if h in ('boards.greenhouse.io', 'job-boards.greenhouse.io') and parts:
        token = parse_qs(p.query).get('for', [''])[0] if parts[0] == 'embed' else parts[0]
        return ('greenhouse', token) if token else None
    if h in ('jobs.lever.co', 'jobs.eu.lever.co') and parts:
        return ('lever_eu' if '.eu.' in h else 'lever', parts[0])
    if h == 'jobs.ashbyhq.com' and parts:
        return 'ashby', parts[0]
    if h in ('jobs.smartrecruiters.com', 'careers.smartrecruiters.com', 'www.smartrecruiters.com') and parts:
        if parts[0].lower() not in ('my-applications', 'login', 'sign-in', 'register', 'privacy-policy', 'job-alerts'):
            return 'smartrecruiters', parts[0]
    if h.endswith('.myworkdayjobs.com') and parts:
        if re.fullmatch(r'[a-z]{2}-[a-z]{2}', parts[0], re.I):
            parts = parts[1:]
        if parts:
            return 'workday', h.split('.')[0] + '/' + parts[0]
    return None

def in_scope(url: str, roots: list[str]) -> bool:
    for root in roots:
        identity = ats_identity(root)
        if identity and ats_identity(url) == identity:
            return True
        if not identity and host(url) == host(root):
            return True
    return False

def links(html: str, url: str):
    soup = BeautifulSoup(html, 'html.parser')
    base = soup.find('base', href=True)
    base_url = urljoin(url, base['href']) if base else url
    found = []
    for tag in soup.select('a[href], iframe[src]'):
        href = tag.get('href') or tag.get('src')
        if host(url) == 'www.google.com' and urlsplit(url).path.startswith('/about/careers/applications') and href.startswith('jobs/results'):
            target = urljoin('https://www.google.com/about/careers/applications/', href)
        else:
            target = urljoin(base_url, href)
        if urlsplit(target).scheme in ('http', 'https'):
            found.append((canonical(target), tag.get_text(' ', strip=True), tag.name))
    # Script-embedded URLs normally put the ATS name in the hostname.
    for target in re.findall(r'https://[^\s"<>\\]+', html):
        if ats_identity(target) or host(target).endswith('.icims.com'):
            found.append((canonical(target), 'embedded careers portal', 'iframe'))
    # Corporate pages often publish only an API feed, rather than a board link.
    # Derive the recruiting board from that source-provided tenant identifier.
    feeds = [
        (r'https://boards-api\.greenhouse\.io/v1/boards/([\w-]+)', 'https://job-boards.greenhouse.io/'),
        (r'https://api\.ashbyhq\.com/posting-api/job-board/([\w-]+)', 'https://jobs.ashbyhq.com/'),
        (r'https://api\.lever\.co/v0/postings/([\w-]+)', 'https://jobs.lever.co/'),
        (r'https://api\.eu\.lever\.co/v0/postings/([\w-]+)', 'https://jobs.eu.lever.co/'),
    ]
    for pattern, prefix in feeds:
        for tenant in re.findall(pattern, html):
            found.append((prefix + tenant, 'employer-published recruiting API feed', 'iframe'))
    return list(dict.fromkeys(found))

def crawl_kind(url: str, text: str = '') -> str:
    """Follow recruiting routes, not product pages whose labels mention software."""
    path = urlsplit(url).path.lower()
    if re.search(r'/(login|log-in|signin|sign-in|register|jobalerts|userhome|talentcommunity|authsvc)(/|$)|/(account|profile)/(login|register)', path):
        return ''
    if re.search(r'\.(mp4|webm|mov|mp3|pdf|png|jpg|jpeg|gif|svg|css|js|zip|woff2?)$', path):
        return ''
    if ats_identity(url):
        return 'portal'
    if host(url) == 'jobs.apple.com' and '/en-us/details/' in path:
        return 'detail' if relevant_title(text + ' ' + path) else ''
    if re.search(r'/job_details/\d|/jobs?/\d|/job/[^/]+|/position/[^/]+|/jobdetail/[^/]+|/en-us/details/[^/]+|/search/\d+', path):
        return 'detail'
    recruiting_path = bool(re.search(r'/(careers?|jobs?|positions?|openings?)(/|$)|/jobsearch|/requisition/', path))
    if recruiting_path and relevant_title(text):
        return 'detail'
    if (recruiting_path or re.search(r'/searchjobs|/job-results|^/search$', path)) and not re.search(r'/teams|/life|/how-we-hire|/inclusion|/privacy|/recruitment-scams|/media|/university|/work-at-apple', path):
        return 'listing'
    if re.search(r'(^|\.)jobs\.|(^|\.)careers\.', host(url)) and re.search(r'search|next|page=|offset=|start=', url + ' ' + text, re.I):
        return 'listing'
    return ''

def recruiting_host(url: str, company: dict) -> bool:
    domain = (company.get('official_domain') or '').lower()
    h = host(url)
    return bool(domain and h.endswith('.' + domain) and h.split('.')[0] in ('jobs', 'careers'))

def employer_routes(roots: list[str]) -> list[str]:
    # Explicit routes for these employer domains; never guess ATS tenant names.
    hosts = {host(root) for root in roots}
    routes = []
    if hosts & {'openai.com', 'www.openai.com'}:
        routes.append('https://jobs.ashbyhq.com/openai')
    if hosts & {'nvidia.com', 'www.nvidia.com', 'jobs.nvidia.com'}:
        routes.extend(['https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite',
                       'https://jobs.nvidia.com/careers'])
    if hosts & {'apple.com', 'www.apple.com'}:
        routes.append('https://jobs.apple.com/en-us/search')
    if 'www.metacareers.com' in hosts:
        routes.append('https://www.metacareers.com/jobsearch')
    explicit_routes = {
        'www.ibm.com': 'https://www.ibm.com/careers/search?location=United+States',
        'jobs.bytedance.com': 'https://joinbytedance.com/search',
        'careers.zoom.us': 'https://careers.zoom.us/jobs/search?country_codes%5B%5D=US',
        'www.careers.zoom.us': 'https://careers.zoom.us/jobs/search?country_codes%5B%5D=US',
        'careers.servicenow.com': 'https://careers.smartrecruiters.com/ServiceNow',
        'careers.linkedin.com': 'https://careers.smartrecruiters.com/LinkedIn3',
        'careersatdoordash.com': 'https://job-boards.greenhouse.io/doordashusa',
        'www.pinterestcareers.com': 'https://job-boards.greenhouse.io/pinterest',
        'www.lyft.com': 'https://job-boards.greenhouse.io/lyft',
        'careers.pypl.com': 'https://paypal.wd1.myworkdayjobs.com/jobs',
        'careers.snap.com': 'https://snapchat.wd1.myworkdayjobs.com/snap',
        'jobs.netflix.com': 'https://explore.jobs.netflix.net/careers?domain=netflix.com',
        'www.uber.com': 'https://jobs.uber.com/en/jobs/',
        'careers.amd.com': 'https://careers.amd.com/careers-home/jobs',
        'careers.qualcomm.com': 'https://careers.qualcomm.com/careers',
    }
    routes.extend(route for employer_host, route in explicit_routes.items() if employer_host in hosts)
    return list(dict.fromkeys(routes))

def retrieval_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f'HTTP {exc.response.status_code}'
    return type(exc).__name__ + ': ' + str(exc)[:180]

def explicitly_no_sponsor(quote_text: str) -> bool:
    text = quote_text.lower()
    return bool(re.search(r'sponsor|sponsorship', text) and re.search(
        r'\b(no|not|cannot|unable|without|unavailable|ineligible)\b|can\x27t|won\x27t|do not|doesn\x27t', text))

US_STATES = {'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'DC', 'FL', 'GA', 'HI',
             'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN',
             'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY', 'NC', 'ND', 'OH',
             'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY'}

def us_hint(location: str) -> bool:
    return bool(re.search(r'\b(?:US|USA|United States|New York|Seattle|Atlanta|San Francisco|Boston|Austin)\b', location, re.I)
                or any(re.search(r',\s*' + state + r'\b', location) for state in US_STATES))

def scoring_job(job: Job) -> dict:
    # Preserve every matching fact and the entire JD; transport metadata and
    # duplicate URLs are unnecessary for the scoring model.
    return {k: getattr(job, k) for k in ('id', 'title', 'location', 'description', 'country_codes', 'us_eligible')}

def structured_page_job(metadata: list[str], text: str, url: str, company: str) -> Job | None:
    """Use complete employer-published JobPosting data only when identity is clear.
    Ambiguous/missing metadata still uses the existing model extractor.
    """
    postings = []
    def walk(value):
        if isinstance(value, dict):
            types = value.get('@type', [])
            if 'JobPosting' in (types if isinstance(types, list) else [types]):
                postings.append(value)
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    for value in metadata:
        try:
            walk(json.loads(value))
        except (ValueError, TypeError):
            pass
    if len(postings) != 1:
        return None
    posting = postings[0]
    org = posting.get('hiringOrganization') or {}
    if not isinstance(org, dict):
        return None
    employer = ' '.join(re.findall(r'\w+', str(org.get('name', '')).casefold()))
    expected = ' '.join(re.findall(r'\w+', company.casefold()))
    if not expected or not employer or not re.search(r'\b' + re.escape(expected) + r'\b', employer):
        return None
    title = str(posting.get('title') or '')
    description = plain(posting.get('description') or '')
    if not title or title not in text or len(description) < 200 or not re.search(r'\bapply\b', text, re.I):
        return None
    if posting.get('url') and canonical(str(posting['url'])) != canonical(url):
        return None
    if re.search(r'(job|position|posting).{0,50}(no longer available|has been filled|has expired)', text, re.I):
        return None
    if posting.get('validThrough'):
        try:
            expiry = datetime.fromisoformat(str(posting['validThrough']).replace('Z', '+00:00'))
            if expiry.date() < datetime.now(timezone.utc).date():
                return None
        except ValueError:
            return None
    locations = posting.get('jobLocation') or []
    if not isinstance(locations, list):
        locations = [locations]
    names = []
    for location in locations:
        if not isinstance(location, dict):
            continue
        address = location.get('address') or {}
        if isinstance(address, dict):
            parts = [address.get(k) for k in ('addressLocality', 'addressRegion', 'addressCountry')]
            names.append(', '.join(str(x.get('name', '') if isinstance(x, dict) else x) for x in parts if x))
    if posting.get('jobLocationType'):
        names.append(str(posting['jobLocationType']))
    countries = jsonld_job_countries(metadata)
    # Keep all visible posting text, including requirements outside the JSON-LD
    # description. Add the full structured description if it is not already there.
    body = text if description in text else text + '\n' + description
    return Job(id='page:' + digest(canonical(url)), external_id=canonical(url), title=title,
               location='; '.join(filter(None, names)), description=body, url=canonical(url),
               source='careers_page', board_url=url, posted_at=str(posting.get('datePosted') or ''), country_codes=countries)

def title_seniority(title: str) -> str:
    if re.search(r'\bprincipal\b|\bdistinguished\b', title, re.I):
        return 'principal'
    if re.search(r'\bstaff\b', title, re.I):
        return 'staff'
    if re.search(r'\bsenior\b|\bsr\.?\b|\blead\b', title, re.I):
        return 'senior'
    if re.search(r'\bjunior\b|\bjr\.?\b|\bentry[ -]level\b|\bearly[ -]career\b', title, re.I):
        return 'junior'
    return 'unknown'

def level_priority(role: str, seniority: str, profile: Profile) -> int:
    if not profile.prefer_non_senior_roles:
        return 0
    if seniority in ('staff', 'principal'):
        return 2
    if seniority == 'senior' or role == 'senior_sde':
        return 1
    return 0

def rank_key(a: Assessment, profile: Profile):
    preference = profile.role_preferences.index(a.role)
    within_level = ((preference, -a.fit_score, a.job_id) if profile.ranking_mode == 'role_first'
                    else (-a.fit_score, preference, a.job_id))
    return (level_priority(a.role, a.seniority, profile),) + within_level

def csv_cell(value):
    return "'" + value if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')) else value

def write_csv(path: Path, rows: list[dict], fields: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.csv.tmp')
    with temp.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows({k: csv_cell(v) for k, v in row.items()} for row in rows)
    temp.replace(path)
