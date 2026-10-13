"""Runtime settings and logging."""
from __future__ import annotations

import contextvars
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

MAX_MATCHES_PER_COMPANY = 7
JD_MAX_REQUIRED_EXPERIENCE = 6  # Inclusive maximum for a role's stated minimum.
LOG = logging.getLogger("job_scout")
LOG.addHandler(logging.NullHandler())
CURRENT_COMPANY = contextvars.ContextVar("job_scout_company", default="run")

class CompanyLogFilter(logging.Filter):
    def filter(self, record):
        record.company = CURRENT_COMPANY.get()
        return True

def setup_logging(output: Path, level: str = 'INFO', log_file: Path | None = None):
    path = log_file if log_file is not None else output / 'agent.log'
    path.parent.mkdir(parents=True, exist_ok=True)
    for handler in list(LOG.handlers):
        LOG.removeHandler(handler)
        handler.close()
    LOG.setLevel(level)
    LOG.propagate = False
    formatter = logging.Formatter('%(asctime)s %(levelname)-7s [%(company)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    for handler in (logging.StreamHandler(), RotatingFileHandler(path, maxBytes=10_000_000, backupCount=3, encoding='utf-8')):
        handler.setFormatter(formatter)
        handler.addFilter(CompanyLogFilter())
        LOG.addHandler(handler)
    LOG.info('Logging initialized: level=%s file=%s', level, path)
    return path
