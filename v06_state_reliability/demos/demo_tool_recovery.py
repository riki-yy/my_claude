"""Interrupt after a real tool side effect and resume through the real model API."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent


TARGET = agent.WORKSPACE_ROOT / "state_demo.txt"


def main() -> int:
    agent.configure_model()
    session_id = str(uuid.uuid4())
    state = agent.new_runtime_state(session_id)
    state_path = agent.STATE_DIR / f"{session_id}.json"
    logger = agent.EventLogger(session_id, agent.LOG_DIR / f"{session_id}.jsonl")
    original_checkpoint = agent.checkpoint
    injected = False

    def interrupt_at_uncertain_boundary(state_arg, path_arg, logger_arg, reason):
        nonlocal injected
        if reason == "interruption.set:tool_result_recording" and not injected:
            injected = True
            raise KeyboardInterrupt
        return original_checkpoint(state_arg, path_arg, logger_arg, reason)

    agent.checkpoint = interrupt_at_uncertain_boundary
    try:
        agent.run_once(
            state["messages"],
            "Create state_demo.txt with exactly V06-RECOVERY, then read it to verify.",
            logger,
            runtime_state=state,
            state_path=state_path,
        )
        print("demo did not reach the injected interruption", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        state = agent.load_state(state_path)
        state["current_run"]["status"] = "interrupted"
        original_checkpoint(state, state_path, logger, "run.interrupted")
        logger.emit(
            "run.interrupted",
            state["current_run"]["run_id"],
            round=state["current_run"]["round"],
            interruption_info=state["interruption_info"],
            status="interrupted",
        )
    finally:
        agent.checkpoint = original_checkpoint

    restored = agent.load_state(state_path)
    logger.emit(
        "state.restored",
        restored["current_run"]["run_id"],
        state_path=str(state_path),
        run_status=restored["current_run"]["status"],
        round=restored["current_run"]["round"],
        status="restored",
    )
    result = agent.resume_run(restored, state_path, logger)
    actual = TARGET.read_text(encoding="utf-8") if TARGET.exists() else None
    print(f"demo_state={state_path}")
    print(f"workspace_value={actual!r} result={result and result['status']}")
    return 0 if injected and actual == "V06-RECOVERY" and result and result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
