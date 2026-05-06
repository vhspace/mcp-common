"""Shared test fixtures for agent-memory."""

import pytest


@pytest.fixture
def sample_episode_body() -> str:
    return (
        "GPU node b65c909e-41 experienced NVLink CRC errors. "
        "Root cause was firmware version 24.04. "
        "The issue was resolved by upgrading to firmware 24.07."
    )


@pytest.fixture
def sample_episode_name() -> str:
    return "nvlink-crc-fix-test"
