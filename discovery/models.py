"""Validated inputs, model outputs, and graph state."""
from __future__ import annotations

from typing import Literal, TypedDict
from pydantic import BaseModel, Field

Role = Literal['sde', 'senior_sde', 'fde', 'other']

class Profile(BaseModel):
    resume_path: str | None = None
    resume_text: str = ''
    role_preferences: list[Role] = Field(default_factory=lambda: ['sde', 'fde', 'senior_sde'])
    ranking_mode: Literal['fit_first', 'role_first'] = 'fit_first'
    min_fit_score: int = Field(default=65, ge=0, le=100)
    extra_information: str = ''
    prefer_non_senior_roles: bool = True
    experience_years_override: float | None = Field(default=4.5, ge=0, le=60)

class Candidate(BaseModel):
    skills: list[str]
    experience_summary: str
    experience_years: float = Field(ge=0)
    production_systems: list[str]
    ai_agent_experience: list[str]
    customer_implementation_experience: list[str]
    accomplishments: list[str]
    education: list[str]
    evidence: list[str]

class Company(BaseModel):
    company: str
    careers_url: str = ''
    official_domain: str = ''

class CareerChoice(BaseModel):
    url: str = ''
    reason: str = ''

class PageDetail(BaseModel):
    is_single_job: bool
    employer_matches: bool
    appears_open: bool
    title: str = ''
    location: str = ''
    date_posted: str = ''

class Job(BaseModel):
    id: str  # Source + tenant + external ID, not an unqualified ATS numeric ID.
    external_id: str
    title: str
    location: str
    description: str
    url: str
    source: str
    board_url: str
    detail_api: str = ''
    posted_at: str = ''
    country_codes: list[str] = Field(default_factory=list)
    us_eligible: Literal['yes', 'no', 'unknown'] = 'unknown'

class Assessment(BaseModel):
    job_id: str
    role: Role
    seniority: Literal['junior', 'mid', 'senior', 'staff', 'principal', 'unknown'] = 'unknown'
    fit_score: int = Field(ge=0, le=100)
    us_eligible: Literal['yes', 'no', 'unknown']
    sponsorship_not_available: bool
    sponsorship_quote: str = ''
    hard_block: bool
    hard_block_quote: str = ''
    reason: str
    matched_skills: list[str]
    gaps: list[str]
    required_experience: str = ''
    min_required_experience_years: float | None = Field(default=None, ge=0)
    experience_requirement_quote: str = ''

class BatchAssessment(BaseModel):
    assessments: list[Assessment]

class LocationDecision(BaseModel):
    job_id: str
    us_eligible: Literal['yes', 'no', 'unknown']
    quote: str = ''

class Locations(BaseModel):
    decisions: list[LocationDecision]

class State(TypedDict, total=False):
    company: dict
    roots: list[str]
    jobs: list[dict]
    assessments: list[dict]
    rows: list[dict]
    warnings: list[str]
    metrics: dict
    error: str
    status: str
