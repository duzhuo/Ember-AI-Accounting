"""Shared pytest configuration for unit tests."""

import sys
from pathlib import Path

import pytest

# Add project root to path so imports work
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
