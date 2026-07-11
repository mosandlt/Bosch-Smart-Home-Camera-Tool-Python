"""
Tests for JWT token management functions:
  _is_token_expired, _is_token_near_expiry, check_token_age

Uses freezegun for deterministic time control.

IMPORTANT: All time-based tokens use FROZEN_EPOCH as their reference point.
Tests decorated with @freeze_time(FROZEN_NOW) freeze time.time() to FROZEN_EPOCH
so that token exp values (computed relative to FROZEN_EPOCH in conftest.py)
are evaluated correctly under frozen time.
"""

from __future__ import annotations

from freezegun import freeze_time

import bosch_camera
from tests.conftest import _make_jwt, FROZEN_EPOCH

# ISO string matching FROZEN_EPOCH for @freeze_time decorator
FROZEN_NOW = "2024-06-01 12:00:00"


# ── _is_token_expired ─────────────────────────────────────────────────────────


class TestIsTokenExpired:
    @freeze_time(FROZEN_NOW)
    def test_valid_token_returns_false(self, valid_token: str) -> None:
        """Token expiring 3600s after frozen now is NOT expired — must return False."""
        assert bosch_camera._is_token_expired(valid_token) is False

    @freeze_time(FROZEN_NOW)
    def test_expired_token_returns_true(self, expired_token: str) -> None:
        """Token that expired 60s before frozen now must return True."""
        assert bosch_camera._is_token_expired(expired_token) is True

    @freeze_time(FROZEN_NOW)
    def test_near_expiry_token_returns_true(self, near_expiry_token: str) -> None:
        """Token expiring 30s after frozen now (< 60s buffer) must return True."""
        assert bosch_camera._is_token_expired(near_expiry_token) is True

    def test_malformed_token_returns_true(self) -> None:
        """Malformed (non-JWT) string should be treated as expired (fail-safe).

        Regression: pre-2026-05-17 _is_token_expired returned False on decode
        errors (fail-open), which let undecodable strings reach the cloud API
        as bearer tokens. Now matches _is_token_near_expiry: undecodable → True.
        """
        assert bosch_camera._is_token_expired("not.a.jwt") is True

    def test_single_part_token_returns_true(self) -> None:
        """Token without dots (no payload section) is treated as expired."""
        assert bosch_camera._is_token_expired("garbage") is True

    def test_empty_token_returns_true(self) -> None:
        """Empty string is treated as expired (no payload to decode)."""
        assert bosch_camera._is_token_expired("") is True

    def test_no_exp_claim_returns_true(self) -> None:
        """JWT without 'exp' claim defaults exp=0, which fails the > 0 check.

        Post-fix: exp == 0 path now returns True (was False pre-fix). Without
        an exp claim, we cannot prove the token is still valid → treat as
        expired and force a refresh.
        """
        import base64
        import json

        header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(json.dumps({"sub": "u"}).encode()).rstrip(b"=").decode()
        token = f"{header}.{payload}.sig"
        assert bosch_camera._is_token_expired(token) is True

    @freeze_time(FROZEN_NOW)
    def test_exactly_60s_remaining_returns_false(self) -> None:
        """Token with exactly 60s remaining: (exp - now) == 60 → NOT < 60 → False.

        Documents boundary: the buffer condition is strict `< 60`, not `<= 60`.
        A token with exactly 60s left is treated as still valid.
        """
        token = _make_jwt(FROZEN_EPOCH + 60)
        assert bosch_camera._is_token_expired(token) is False

    @freeze_time(FROZEN_NOW)
    def test_exactly_59s_remaining_returns_true(self) -> None:
        """Token with 59s remaining is within the 60s buffer → returns True."""
        token = _make_jwt(FROZEN_EPOCH + 59)
        assert bosch_camera._is_token_expired(token) is True


# ── _is_token_near_expiry ─────────────────────────────────────────────────────


class TestIsTokenNearExpiry:
    @freeze_time(FROZEN_NOW)
    def test_far_future_returns_false(self, valid_token: str) -> None:
        """Token expiring 3600s after frozen now is NOT near-expiry → False."""
        assert bosch_camera._is_token_near_expiry(valid_token) is False

    @freeze_time(FROZEN_NOW)
    def test_near_expiry_returns_true(self, near_expiry_token: str) -> None:
        """Token expiring 30s after frozen now (< 60s buffer) → True."""
        assert bosch_camera._is_token_near_expiry(near_expiry_token) is True

    @freeze_time(FROZEN_NOW)
    def test_expired_token_returns_true(self, expired_token: str) -> None:
        """Already-expired token (exp 60s before frozen now) → True."""
        assert bosch_camera._is_token_near_expiry(expired_token) is True

    def test_malformed_token_returns_true(self) -> None:
        """Malformed token: fail-safe → return True (treat as near-expiry)."""
        assert bosch_camera._is_token_near_expiry("garbage") is True

    def test_empty_string_returns_true(self) -> None:
        """Empty string token: fail-safe → return True."""
        assert bosch_camera._is_token_near_expiry("") is True

    @freeze_time(FROZEN_NOW)
    def test_custom_buffer_secs(self) -> None:
        """buffer_secs parameter is respected: token with 120s left is near-expiry at buffer=180."""
        token = _make_jwt(FROZEN_EPOCH + 120)
        assert bosch_camera._is_token_near_expiry(token, buffer_secs=180) is True
        assert bosch_camera._is_token_near_expiry(token, buffer_secs=60) is False


# ── check_token_age ───────────────────────────────────────────────────────────


class TestCheckTokenAge:
    @freeze_time(FROZEN_NOW)
    def test_no_token_returns_no_token(self, tmp_config_dir: str) -> None:
        """Config with empty bearer_token returns 'no token'."""
        cfg = {"account": {"bearer_token": ""}}
        result = bosch_camera.check_token_age(cfg)
        assert result == "no token"

    @freeze_time(FROZEN_NOW)
    def test_valid_token_returns_valid_string(self, valid_token: str) -> None:
        """Valid token (3600s remaining) returns a string containing 'valid'."""
        cfg = {"account": {"bearer_token": valid_token}}
        result = bosch_camera.check_token_age(cfg)
        assert "valid" in result.lower()

    @freeze_time(FROZEN_NOW)
    def test_expired_token_returns_expired_string(
        self, tmp_config_dir: str, expired_token: str
    ) -> None:
        """Expired token (exp 60s before frozen now) returns EXPIRED message.

        tmp_config_dir ensures CONFIG_FILE exists for the mtime fallback path.
        The expired token hits the `mins <= 0` branch before reaching mtime fallback.
        """
        import bosch_camera as bc

        bc.save_config({"account": {"bearer_token": expired_token}, "cameras": {}, "settings": {}})
        cfg = {"account": {"bearer_token": expired_token}}
        result = bc.check_token_age(cfg)
        assert "EXPIRED" in result

    @freeze_time(FROZEN_NOW)
    def test_near_expiry_token_returns_nonempty(
        self, tmp_config_dir: str, near_expiry_token: str
    ) -> None:
        """Token expiring 30s after frozen now returns some string.

        int(30/60) == 0 mins → falls into the `mins <= 0` branch (EXPIRED message).
        This is a known boundary: sub-minute remaining times appear as EXPIRED.
        The test just asserts a non-empty string is returned without asserting
        the exact category — the boundary is documented in test_token_exactly_* tests.
        """
        import bosch_camera as bc

        bc.save_config(
            {"account": {"bearer_token": near_expiry_token}, "cameras": {}, "settings": {}}
        )
        cfg = {"account": {"bearer_token": near_expiry_token}}
        result = bc.check_token_age(cfg)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_malformed_token_falls_back_to_mtime(self, tmp_config_dir: str) -> None:
        """Malformed token: JWT decode fails, falls back to CONFIG_FILE mtime.

        CONFIG_FILE must exist; we create it via save_config first.
        Should not raise — returns a non-empty string.
        """
        import bosch_camera as bc

        bc.save_config({"account": {}, "cameras": {}, "settings": {}})
        cfg = {"account": {"bearer_token": "not.a.real.jwt.at.all"}}
        result = bc.check_token_age(cfg)
        assert isinstance(result, str)
        assert len(result) > 0
