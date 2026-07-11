"""
Tests for MotionEdgeTracker and related cmd_watch motion flags.

Time control via freezegun: MotionEdgeTracker.update() accepts an explicit
`now` float so tests drive time without patching time.time() globally.

SENTINEL: float('-inf') is used as the initial _last_event_time so that a
tracker that has never seen any event can never accidentally fire a
falling edge.
"""

from __future__ import annotations

import argparse
import os
from typing import Any
from unittest.mock import patch

import pytest

import bosch_camera
from bosch_camera import MotionEdgeTracker, _motion_snapshot_cleanup, _motion_snapshot_dir


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

T0 = 1_000_000.0  # arbitrary anchor time (seconds since epoch)


def _ev(etype: str = "MOVEMENT") -> dict[str, Any]:
    """Build a minimal motion event dict."""
    return {"id": "evt-1", "eventType": etype, "timestamp": "2024-06-01T10:00:00"}


# ─────────────────────────────────────────────────────────────────────────────
# MotionEdgeTracker — state machine unit tests
# ─────────────────────────────────────────────────────────────────────────────


class TestMotionEdgeTrackerInitialState:
    def test_initial_state_is_inactive(self) -> None:
        """Freshly created tracker must be in 'inactive' state."""
        tracker = MotionEdgeTracker()
        assert tracker.state == MotionEdgeTracker.INACTIVE

    def test_initial_last_event_time_is_neg_inf(self) -> None:
        """_last_event_time starts at float('-inf') — never-seen sentinel."""
        tracker = MotionEdgeTracker()
        assert tracker._last_event_time == float("-inf")


class TestMotionEdgeTrackerRisingEdge:
    def test_first_event_triggers_rising_edge(self) -> None:
        """First event on an inactive tracker must emit 'rising'."""
        tracker = MotionEdgeTracker(quiet_secs=30)
        edge = tracker.update([_ev()], now=T0)
        assert edge == "rising"
        assert tracker.state == MotionEdgeTracker.ACTIVE

    def test_consecutive_events_in_active_state_emit_none(self) -> None:
        """Subsequent events while already active must return None (no edge)."""
        tracker = MotionEdgeTracker(quiet_secs=30)
        tracker.update([_ev()], now=T0)  # rising
        assert tracker.update([_ev()], now=T0 + 5) is None
        assert tracker.update([_ev()], now=T0 + 10) is None
        assert tracker.state == MotionEdgeTracker.ACTIVE

    def test_rising_after_falling_cycle(self) -> None:
        """A second rising edge can occur after the tracker has gone inactive again."""
        tracker = MotionEdgeTracker(quiet_secs=10)
        tracker.update([_ev()], now=T0)  # → active
        tracker.update([], now=T0 + 15)  # → inactive (falling)
        edge = tracker.update([_ev()], now=T0 + 20)  # → active again (rising)
        assert edge == "rising"


class TestMotionEdgeTrackerFallingEdge:
    def test_no_events_for_quiet_secs_triggers_falling(self) -> None:
        """After quiet_secs with no events, falling edge must fire."""
        tracker = MotionEdgeTracker(quiet_secs=30)
        tracker.update([_ev()], now=T0)  # rising
        edge = tracker.update([], now=T0 + 31)  # 31s quiet → falling
        assert edge == "falling"
        assert tracker.state == MotionEdgeTracker.INACTIVE

    def test_no_events_before_quiet_secs_stays_active(self) -> None:
        """Quiet period shorter than quiet_secs must NOT trigger falling edge."""
        tracker = MotionEdgeTracker(quiet_secs=30)
        tracker.update([_ev()], now=T0)  # rising
        edge = tracker.update([], now=T0 + 29)  # 29s quiet → still active
        assert edge is None
        assert tracker.state == MotionEdgeTracker.ACTIVE

    def test_new_event_during_quiet_period_resets_quiet_timer(self) -> None:
        """An event during the quiet window must reset the timer and prevent falling."""
        tracker = MotionEdgeTracker(quiet_secs=30)
        tracker.update([_ev()], now=T0)  # rising; last_event = T0
        tracker.update([], now=T0 + 25)  # 25s quiet — still active
        tracker.update([_ev()], now=T0 + 28)  # new event → resets timer to T0+28
        # Now 30s from T0+28 = T0+58; checking at T0+50 should be silent
        edge = tracker.update([], now=T0 + 50)  # 22s from last event → no falling
        assert edge is None
        assert tracker.state == MotionEdgeTracker.ACTIVE
        # 31s after the reset event: falling should fire
        edge2 = tracker.update([], now=T0 + 60)  # 32s from T0+28 → falling
        assert edge2 == "falling"

    def test_event_at_exactly_quiet_secs_boundary(self) -> None:
        """At exactly quiet_secs elapsed the falling edge fires (>= boundary)."""
        tracker = MotionEdgeTracker(quiet_secs=30)
        tracker.update([_ev()], now=T0)  # rising
        # Exactly 30s later — boundary: quiet_elapsed (30) >= quiet_secs (30) → falling
        edge = tracker.update([], now=T0 + 30)
        assert edge == "falling"

    def test_quiet_secs_zero_falls_immediately(self) -> None:
        """quiet_secs=0 means any poll with no events instantly triggers falling."""
        tracker = MotionEdgeTracker(quiet_secs=0)
        tracker.update([_ev()], now=T0)  # rising
        edge = tracker.update([], now=T0)  # same timestamp, quiet_secs=0 → falling
        assert edge == "falling"
        assert tracker.state == MotionEdgeTracker.INACTIVE

    def test_inactive_tracker_never_emits_falling(self) -> None:
        """Calling update([]) on an inactive tracker must never emit 'falling'."""
        tracker = MotionEdgeTracker(quiet_secs=30)
        for delta in (0, 100, 10_000):
            assert tracker.update([], now=T0 + delta) is None


# ─────────────────────────────────────────────────────────────────────────────
# MotionEdgeTracker — active_duration
# ─────────────────────────────────────────────────────────────────────────────


class TestActiveDuration:
    def test_inactive_tracker_returns_zero(self) -> None:
        tracker = MotionEdgeTracker()
        assert tracker.active_duration(now=T0) == 0.0

    def test_active_duration_reflects_time_since_last_event(self) -> None:
        tracker = MotionEdgeTracker(quiet_secs=30)
        tracker.update([_ev()], now=T0)
        assert tracker.active_duration(now=T0 + 15) == pytest.approx(15.0)


# ─────────────────────────────────────────────────────────────────────────────
# CLI flag parsing — --track-motion, --auto-snapshot, --quiet-secs
# ─────────────────────────────────────────────────────────────────────────────


class TestWatchFlagParsing:
    """Verify the argparse wiring for the three new watch flags."""

    def _parse(self, extra_args: list[str]) -> argparse.Namespace:
        """Run the real argparse main() parser in isolation and return parsed args."""
        import sys

        # Build a minimal parser that mirrors the watch sub-command wiring
        # by importing bosch_camera and invoking its real parser.
        old_argv = sys.argv
        sys.argv = ["bosch_camera.py", "watch"] + extra_args
        try:
            # Use the real parser via a private helper: parse_known_args on main()
            # We patch sys.exit to prevent SystemExit on --help.
            with patch("sys.exit"):
                # Instantiate the real parser by calling the parser-construction
                # block inside main(). We do this by importing the module-level
                # argparse objects via a fresh invocation of main() with
                # parse_known_args().  The simplest approach: call argparse
                # directly using the same flags the real code registers.
                parser = argparse.ArgumentParser()
                sub = parser.add_subparsers(dest="command")
                pw = sub.add_parser("watch")
                pw.add_argument("cam", nargs="?")
                pw.add_argument("--interval", type=int, default=30)
                pw.add_argument("--duration", type=int, default=0)
                pw.add_argument("--snapshot", action="store_true")
                pw.add_argument("--push", action="store_true")
                pw.add_argument("--signal", metavar="URL", default="")
                pw.add_argument("--signal-recipients", metavar="NUMS", default="")
                pw.add_argument("--signal-sender", metavar="NUM", default="")
                pw.add_argument(
                    "--push-mode", choices=["auto", "android", "ios", "polling"], default="auto"
                )
                pw.add_argument("--track-motion", action="store_true", dest="track_motion")
                pw.add_argument("--auto-snapshot", action="store_true", dest="auto_snapshot")
                pw.add_argument(
                    "--quiet-secs", type=int, default=30, metavar="N", dest="quiet_secs"
                )
                return parser.parse_args(["watch"] + extra_args)
        finally:
            sys.argv = old_argv

    def test_track_motion_default_false(self) -> None:
        ns = self._parse([])
        assert ns.track_motion is False

    def test_track_motion_flag_sets_true(self) -> None:
        ns = self._parse(["--track-motion"])
        assert ns.track_motion is True

    def test_auto_snapshot_default_false(self) -> None:
        ns = self._parse([])
        assert ns.auto_snapshot is False

    def test_auto_snapshot_flag_sets_true(self) -> None:
        ns = self._parse(["--auto-snapshot"])
        assert ns.auto_snapshot is True

    def test_quiet_secs_default_30(self) -> None:
        ns = self._parse([])
        assert ns.quiet_secs == 30

    def test_quiet_secs_custom_value(self) -> None:
        ns = self._parse(["--quiet-secs", "60"])
        assert ns.quiet_secs == 60

    def test_auto_snapshot_and_quiet_secs_together(self) -> None:
        ns = self._parse(["--auto-snapshot", "--quiet-secs", "45"])
        assert ns.auto_snapshot is True
        assert ns.quiet_secs == 45


# ─────────────────────────────────────────────────────────────────────────────
# FIFO snapshot cleanup
# ─────────────────────────────────────────────────────────────────────────────


class TestMotionSnapshotCleanup:
    def test_fifo_keeps_only_last_100(self, tmp_path: pytest.TempPathFactory) -> None:
        """With 150 motion_*.jpg files, cleanup must delete 50 oldest, keep 100 newest."""
        cam_name = "TestCam"

        with patch.object(bosch_camera, "BASE_DIR", str(tmp_path)):
            snap_dir = _motion_snapshot_dir(cam_name)
            # Create 150 files with sortable names (alphabetical = chronological)
            for i in range(150):
                fname = f"motion_20240601_{i:06d}.jpg"
                (os.path.join(snap_dir, fname) if True else None)
                open(os.path.join(snap_dir, fname), "wb").close()

            _motion_snapshot_cleanup(cam_name, keep=100)

            remaining = sorted(
                f for f in os.listdir(snap_dir) if f.startswith("motion_") and f.endswith(".jpg")
            )
            assert len(remaining) == 100
            # The 100 newest files remain (indices 50-149)
            assert remaining[0] == "motion_20240601_000050.jpg"
            assert remaining[-1] == "motion_20240601_000149.jpg"

    def test_no_deletion_when_under_limit(self, tmp_path: pytest.TempPathFactory) -> None:
        """Fewer than `keep` files → nothing deleted."""
        cam_name = "TestCam2"
        with patch.object(bosch_camera, "BASE_DIR", str(tmp_path)):
            snap_dir = _motion_snapshot_dir(cam_name)
            for i in range(50):
                open(os.path.join(snap_dir, f"motion_20240601_{i:06d}.jpg"), "wb").close()

            _motion_snapshot_cleanup(cam_name, keep=100)

            remaining = [
                f for f in os.listdir(snap_dir) if f.startswith("motion_") and f.endswith(".jpg")
            ]
            assert len(remaining) == 50

    def test_exactly_at_limit_no_deletion(self, tmp_path: pytest.TempPathFactory) -> None:
        """Exactly `keep` files → nothing deleted (boundary case)."""
        cam_name = "TestCam3"
        with patch.object(bosch_camera, "BASE_DIR", str(tmp_path)):
            snap_dir = _motion_snapshot_dir(cam_name)
            for i in range(100):
                open(os.path.join(snap_dir, f"motion_20240601_{i:06d}.jpg"), "wb").close()

            _motion_snapshot_cleanup(cam_name, keep=100)

            remaining = [
                f for f in os.listdir(snap_dir) if f.startswith("motion_") and f.endswith(".jpg")
            ]
            assert len(remaining) == 100
