"""Centralized prompt definitions for the AI Accounting Voucher system.

Prompts are stored in prompts/*.md files for easy editing by non-developers.
This module loads them at import time and exposes the same variable names.
"""

from pathlib import Path

_PROMPT_DIR = Path(__file__).parent / "prompts"

NL_PARSE_SYSTEM_PROMPT = (_PROMPT_DIR / "intent_recognition.md").read_text(encoding="utf-8").strip()
IMAGE_PARSE_SYSTEM_PROMPT = (_PROMPT_DIR / "image_recognition.md").read_text(encoding="utf-8").strip()
VOUCHER_GENERATION_PROMPT = (_PROMPT_DIR / "voucher_generation.md").read_text(encoding="utf-8").strip()
