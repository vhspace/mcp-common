from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir():
    return FIXTURES


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()
