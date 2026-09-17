"""Paths and environment. One place so nothing guesses where data lives."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

APPS_CSV = ROOT / "apps.csv"
DATA = ROOT / "data"
RAW = DATA / "raw"
PASS1 = DATA / "pass1"
VERIFIED = DATA / "verified"
URLS = DATA / "urls"
HUMAN_SAMPLE = DATA / "human_sample.json"
ACCURACY = DATA / "accuracy.json"
MISSES = DATA / "misses.json"
CACHE = ROOT / "cache"
SITE = ROOT / "site"

COMPOSIO_API_KEY = os.environ.get("COMPOSIO_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# Which provider serves the extraction call. Composio gives us search, scrape,
# browser, and the toolkit registry, but not a model, so this is a separate
# choice. groq is the default because it is what we are paying for.
LLM_PROVIDER = os.environ.get("SCOUT_LLM", "groq")

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

DEFAULT_MODELS = {
    "groq": "openai/gpt-oss-20b",
    "anthropic": "claude-sonnet-5",
    "claude-cli": "",  # empty means let the CLI pick
}

EXTRACT_MODEL = os.environ.get("SCOUT_MODEL") or DEFAULT_MODELS.get(LLM_PROVIDER, "")

# How many pages we are willing to pull per app. CLAUDE.md says 3 to 6.
MAX_PAGES_PER_APP = 6


def ensure_dirs():
    for d in (RAW, PASS1, VERIFIED, URLS, CACHE, SITE):
        d.mkdir(parents=True, exist_ok=True)


def load_dotenv():
    """Tiny .env reader so nobody has to export vars by hand. Real values in
    the environment always win over the file."""
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    global COMPOSIO_API_KEY, GROQ_API_KEY, ANTHROPIC_API_KEY
    global LLM_PROVIDER, EXTRACT_MODEL
    COMPOSIO_API_KEY = os.environ.get("COMPOSIO_API_KEY")
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
    LLM_PROVIDER = os.environ.get("SCOUT_LLM") or "groq"
    EXTRACT_MODEL = os.environ.get("SCOUT_MODEL") or DEFAULT_MODELS.get(LLM_PROVIDER, "")
