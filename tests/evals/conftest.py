"""Pytest configuration for agent evaluations."""

import sys
from pathlib import Path

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture(scope="session")
def project_root():
    """Return the project root directory."""
    return Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def eval_data_dir():
    """Return the eval data directory."""
    return Path(__file__).parent / "data"
