"""
Shared pytest fixtures for bosch_camera tests.

IMPORTANT — freezegun + fixtures:
  All token fixtures use a FIXED anchor time (FROZEN_EPOCH) so that tokens
  created in conftest.py have exp values relative to that same frozen moment.
  Tests that use these fixtures must be decorated with @freeze_time(FROZEN_NOW)
  or use the FROZEN_EPOCH constant directly to compute relative exp values.

  Reason: pytest fixtures run BEFORE @freeze_time takes effect, so if fixtures
  used real time.time() the exp values would be relative to 2026-wall-clock
  while the frozen test time is 2024-06-01 → exp always far in the future →
  expired/near-expiry tokens appear valid under frozen time.
"""

from __future__ import annotations

import base64
import json
from typing import Iterator

import pytest
import responses as responses_lib

# Fixed epoch shared by conftest fixtures and test modules.
# All token exp values are computed relative to this timestamp.
FROZEN_EPOCH: int = 1_717_243_200   # 2024-06-01 12:00:00 UTC


def _make_jwt(exp: int) -> str:
    """Build an unsigned JWT with the given `exp` timestamp.

    Structure: base64url(header).base64url({"exp": exp}).fakesig
    The signature part is intentionally a dummy — bosch_camera only decodes
    the payload, never verifies the signature.
    """
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    payload_bytes = json.dumps({"exp": exp, "sub": "test"}).encode()
    payload = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()
    return f"{header}.{payload}.fakesig"


@pytest.fixture()
def tmp_config_dir(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> str:
    """Redirect BASE_DIR and CONFIG_FILE to a tmp directory.

    Returns the tmp directory path (str) for convenience.
    Note: load_config() and save_config() read CONFIG_FILE at module level.
    We monkeypatch the module globals so tests stay isolated from the real
    bosch_config.json sitting next to the script.
    """
    import bosch_camera
    config_path = str(tmp_path / "bosch_config.json")
    monkeypatch.setattr(bosch_camera, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(bosch_camera, "CONFIG_FILE", config_path)
    return str(tmp_path)


@pytest.fixture()
def valid_token() -> str:
    """Return an unsigned JWT that expires 3600s after FROZEN_EPOCH."""
    return _make_jwt(FROZEN_EPOCH + 3600)


@pytest.fixture()
def expired_token() -> str:
    """Return an unsigned JWT that expired 60s before FROZEN_EPOCH."""
    return _make_jwt(FROZEN_EPOCH - 60)


@pytest.fixture()
def near_expiry_token() -> str:
    """Return an unsigned JWT that expires 30s after FROZEN_EPOCH (within 60s buffer)."""
    return _make_jwt(FROZEN_EPOCH + 30)


@pytest.fixture()
def mock_session() -> Iterator[responses_lib.RequestsMock]:
    """Activate the `responses` mock so no real HTTP calls can escape in tests."""
    with responses_lib.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        yield rsps
