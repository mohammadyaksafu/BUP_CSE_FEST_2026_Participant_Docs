"""Environment-driven configuration for the GridWise solution."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

PUKU_API_KEY = os.getenv("PUKU_API_KEY", "").strip()
PUKU_MODEL = os.getenv("PUKU_MODEL", "puku-ai-2.8").strip()
PUKU_BASE_URL = os.getenv("PUKU_BASE_URL", "https://api.puku.sh/v1").strip() or "https://api.puku.sh/v1"
PUKU_TIMEOUT_SECONDS = float(os.getenv("PUKU_TIMEOUT_SECONDS", "12"))
PUKU_MAX_RETRIES = int(os.getenv("PUKU_MAX_RETRIES", "2"))

PORT = int(os.getenv("PORT", "8000"))

NUMERIC_TOLERANCE = float(os.getenv("NUMERIC_TOLERANCE", "0.01"))
