import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture():
    def load(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")

    return load
