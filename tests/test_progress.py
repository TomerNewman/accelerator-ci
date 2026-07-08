"""Tests for accelerator_ci.shared.progress."""

from __future__ import annotations

import json
import logging

import pytest

from accelerator_ci.shared.progress import ProgressTracker


def test_step_numbering(caplog):
    caplog.set_level(logging.INFO)

    p = ProgressTracker("deploy", ["Setup host", "Run kcli", "Wait"])
    p.start()
    p.step(1)
    p.step(2)
    p.step(3)
    p.done()

    messages = [r.message for r in caplog.records]
    assert any("[deploy] Starting (3 steps)" in m for m in messages)
    assert any("Step 1/3: Setup host" in m for m in messages)
    assert any("Step 2/3: Run kcli" in m for m in messages)
    assert any("Step 3/3: Wait" in m for m in messages)
    assert any("[deploy] Completed (3 steps" in m for m in messages)


def test_fail_logs_error(caplog):
    caplog.set_level(logging.ERROR)

    p = ProgressTracker("deploy", ["Step A", "Step B"])
    p.start()
    p.step(1)
    p.step(2)
    p.fail("something broke")

    errors = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "Failed at step 2/2" in errors[0]
    assert "something broke" in errors[0]


def test_json_output_emits_events(capsys, monkeypatch):
    import accelerator_ci.shared.progress as mod
    clock = [0.0]

    def fake_monotonic():
        val = clock[0]
        clock[0] += 10.0
        return val

    monkeypatch.setattr(mod.time, "monotonic", fake_monotonic)

    p = ProgressTracker("ops", ["Pre-check", "Install"], json_output=True)
    p.start()
    p.step(1)
    p.step(2)
    p.done()

    lines = capsys.readouterr().out.strip().split("\n")
    events = [json.loads(line) for line in lines]

    event_types = [e["event"] for e in events]
    assert event_types[0] == "workflow_start"
    assert event_types[-1] == "workflow_done"
    assert "step_start" in event_types
    assert "step_done" in event_types

    start_event = events[0]
    assert start_event["workflow"] == "ops"
    assert start_event["total_steps"] == 2


def test_json_not_emitted_by_default(capsys):
    p = ProgressTracker("test", ["A"])
    p.start()
    p.step(1)
    p.done()

    assert capsys.readouterr().out == ""


def test_json_fail_event(capsys, monkeypatch):
    import accelerator_ci.shared.progress as mod
    monkeypatch.setattr(mod.time, "monotonic", lambda: 0.0)

    p = ProgressTracker("deploy", ["One"], json_output=True)
    p.start()
    p.step(1)
    p.fail("timeout")

    lines = capsys.readouterr().out.strip().split("\n")
    events = [json.loads(line) for line in lines]

    fail_events = [e for e in events if e["event"] == "workflow_failed"]
    assert len(fail_events) == 1
    assert fail_events[0]["error"] == "timeout"
    assert fail_events[0]["step"] == 1


def test_step_index_out_of_range():
    p = ProgressTracker("w", ["A", "B"])
    with pytest.raises(ValueError, match="out of range"):
        p.step(0)
    with pytest.raises(ValueError, match="out of range"):
        p.step(3)


def test_fail_before_any_step(caplog):
    caplog.set_level(logging.ERROR)

    p = ProgressTracker("w", ["A"])
    p.start()
    p.fail("early crash")

    errors = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "not started" in errors[0]


def test_step_done_includes_duration(capsys, monkeypatch):
    import accelerator_ci.shared.progress as mod
    call_count = [0]

    def advancing_clock():
        val = call_count[0] * 5.0
        call_count[0] += 1
        return val

    monkeypatch.setattr(mod.time, "monotonic", advancing_clock)

    p = ProgressTracker("w", ["A", "B"], json_output=True)
    p.start()
    p.step(1)
    p.step(2)
    p.done()

    lines = capsys.readouterr().out.strip().split("\n")
    events = [json.loads(line) for line in lines]

    step_dones = [e for e in events if e["event"] == "step_done"]
    assert len(step_dones) == 2
    for sd in step_dones:
        assert "duration_s" in sd
        assert sd["duration_s"] >= 0
