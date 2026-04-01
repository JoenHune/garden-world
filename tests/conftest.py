"""Shared pytest fixtures and markers for garden-world tests."""
from __future__ import annotations

import pytest

# Mark integration tests that require a live browser + network
integration = pytest.mark.integration
