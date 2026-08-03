import json
import os

import pytest

CORPUS = os.path.join(os.path.dirname(__file__), "..", "corpus")
OUT = os.path.abspath(os.path.join(CORPUS, "out"))


@pytest.fixture(scope="session")
def manifest() -> dict:
    with open(os.path.join(CORPUS, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


def sample_path(name: str) -> str:
    return os.path.join(OUT, name)


def require_sample(name: str, manifest: dict) -> str:
    """Path to a built sample; skips the test if an optional one is absent."""
    path = sample_path(name)
    if not os.path.exists(path):
        if manifest["samples"].get(name, {}).get("optional"):
            pytest.skip(f"optional sample {name} not built")
        pytest.fail(f"required sample {name} missing — run `make -C corpus` "
                    f"(or `python corpus/build.py`)")
    return path
