"""Inject one 429 at the real client boundary, then delegate to the configured API."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent


class InjectedRateLimitError(Exception):
    status_code = 429
    response = SimpleNamespace(headers={})


class InjectOnce:
    def __init__(self, real_messages):
        self.real_messages = real_messages
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise InjectedRateLimitError("model_concurrency_rate_limit_exceeded")
        return self.real_messages.create(**kwargs)


def main() -> int:
    agent.configure_model()
    session_id = str(uuid.uuid4())
    state = agent.new_runtime_state(session_id)
    state_path = agent.STATE_DIR / f"{session_id}.json"
    logger = agent.EventLogger(session_id, agent.LOG_DIR / f"{session_id}.jsonl")
    injected = InjectOnce(agent.client.messages)
    agent.client = SimpleNamespace(messages=injected)
    result = agent.run_once(
        state["messages"],
        "Reply exactly: V06 retry recovered. Do not call tools.",
        logger,
        runtime_state=state,
        state_path=state_path,
    )
    print(f"demo_state={state_path}")
    print(f"api_calls={injected.calls} result={result['status']} rounds={result['rounds']}")
    return 0 if result["status"] == "completed" and injected.calls == 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
