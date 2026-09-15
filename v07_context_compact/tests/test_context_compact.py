import copy
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from anthropic.types import TextBlock, ToolUseBlock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent


def response(text):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], usage=None, stop_reason="end_turn")


def summary_text():
    titles = (
        "Primary Request and Intent", "Key Technical Concepts", "Files and Code Sections",
        "Errors and Fixes", "Problem Solving", "All User Messages", "Pending Tasks",
        "Current Work", "Optional Next Step",
    )
    return "\n".join(f"## {i}. {title}\nPreserved {title}." for i, title in enumerate(titles, 1))


def summary_call_contains(call, text):
    for message in call["messages"][:-1]:
        content = message.get("content")
        if isinstance(content, str) and text in content:
            return True
        if isinstance(content, list) and any(text in str(agent._value(block, "text", "")) for block in content):
            return True
    return False


class ScriptedMessages:
    def __init__(self, items):
        self.items = list(items)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class PromptTooLong(Exception):
    status_code = 400


@pytest.fixture
def context_runtime(monkeypatch, tmp_path):
    scripted = ScriptedMessages([])
    monkeypatch.setattr(agent, "client", SimpleNamespace(messages=scripted))
    monkeypatch.setattr(agent, "MODEL", "fake-model")
    monkeypatch.setattr(agent, "ARTIFACT_DIR", tmp_path / "artifacts")
    logger = agent.EventLogger("session-1", tmp_path / "events.jsonl", io.StringIO())
    state = agent.new_runtime_state("session-1")
    state["current_run"] = {"run_id": "run-1", "status": "running", "round": 1}
    state_path = tmp_path / "state.json"
    return scripted, logger, state, state_path


def paired_result(content, tool_id="tool-1"):
    return [
        {"role": "assistant", "content": [{"type": "tool_use", "id": tool_id, "name": "read_file", "input": {"path": "big.txt"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": content}]},
    ]


def test_estimate_counts_system_messages_and_tools():
    base = agent.estimate_context_tokens([])
    assert agent.estimate_context_tokens([{"role": "user", "content": "x" * 300}]) > base
    assert agent.estimate_context_tokens([], system=agent.SYSTEM + "x" * 300) > base
    assert agent.estimate_context_tokens([], tools=agent.TOOLS + [{"name": "x", "description": "y" * 300}]) > base


@pytest.mark.parametrize(("mechanisms", "action"), [
    ([], "none"),
    (["microcompact"], "MicroCompact"),
    (["tool_result_budget", "microcompact", "full_compact"], "ToolResultBudget+MicroCompact+FullCompact"),
])
def test_context_budget_cli_displays_existing_event_data(context_runtime, mechanisms, action):
    _, logger, _, _ = context_runtime
    data = {
        "round": 2,
        "estimated_tokens": 12_700,
        "trigger_tokens": 16_000,
        "target_tokens": 8_000,
        "triggered": bool(mechanisms),
        "reason": "request_preflight",
        "mechanisms": mechanisms,
        "before_tokens": 18_000,
        "after_tokens": 12_700,
        "tool_results_processed": 2,
        "tool_result_before_chars": 20_000,
        "tool_result_after_chars": 200,
        "summary_called": "full_compact" in mechanisms,
    }

    logger.emit("context.budget", "run-1", **data)

    shown = logger.stream.getvalue().strip()
    assert shown.startswith(
        f"[context.budget] estimated_tokens=12700 | trigger_tokens=16000 | action={action}"
    )
    if mechanisms:
        assert "before_tokens=18000 | after_tokens=12700 | target_tokens=8000" in shown
        assert "tool_results_processed=2" in shown
    else:
        assert "before_tokens=" not in shown
    recorded = json.loads(logger.log_path.read_text(encoding="utf-8"))["data"]
    assert recorded == data


def test_small_context_is_a_true_noop_without_checkpoint(context_runtime, monkeypatch):
    _, logger, state, state_path = context_runtime
    messages = [{"role": "user", "content": "small"}]
    state["messages"] = messages
    calls = []
    monkeypatch.setattr(agent, "checkpoint", lambda *args: calls.append(args))
    before = copy.deepcopy(messages)
    result = agent.prepare_context(messages, logger, "run-1", 1, state, state_path)
    assert result["full_compact"] is False
    assert messages == before and calls == []


def test_current_batch_total_limit_compacts_multiple_results(context_runtime):
    _, logger, state, state_path = context_runtime
    assistant = {"role": "assistant", "content": []}
    results = {"role": "user", "content": []}
    for number in range(3):
        tool_id = f"batch-{number}"
        assistant["content"].append({"type": "tool_use", "id": tool_id, "name": "read_file", "input": {"path": str(number)}})
        results["content"].append({"type": "tool_result", "tool_use_id": tool_id, "content": str(number) * 12000})
    messages = [assistant, results]
    state["messages"] = messages
    agent.prepare_context(messages, logger, "run-1", 1, state, state_path)
    active = sum(len(block["content"]) for block in messages[1]["content"])
    assert active <= agent.TOOL_RESULT_BATCH_CHARS
    assert all("[Full Tool Result:" in block["content"] for block in messages[1]["content"])
    agent.validate_llm_message_protocol(messages)


def test_tool_result_budget_persists_then_replaces_payload_and_checkpoints(context_runtime, monkeypatch):
    scripted, logger, state, state_path = context_runtime
    raw = "HEAD" + "x" * 17000 + "TAIL"
    messages = paired_result(raw)
    state["messages"] = messages
    result = agent.prepare_context(messages, logger, "run-1", 1, state, state_path)
    block = messages[1]["content"][0]
    assert result["full_compact"] is False
    assert block["tool_use_id"] == "tool-1"
    assert "HEAD" in block["content"] and "TAIL" in block["content"]
    reference = block["content"].split("reference=", 1)[1].split("]", 1)[0]
    assert Path(reference).read_text() == raw
    assert Path(reference) == agent.ARTIFACT_DIR / "session-1" / "tool-1.txt"
    assert agent.load_state(state_path)["messages"] == messages


def test_deterministic_artifact_reuses_identical_content_and_rejects_conflict(context_runtime):
    _, logger, state, state_path = context_runtime
    first_raw = "a" * 17_001
    first = paired_result(first_raw, "stable-tool")
    state["messages"] = first
    agent.prepare_context(first, logger, "run-1", 1, state, state_path)
    path = agent.ARTIFACT_DIR / "session-1" / "stable-tool.txt"
    assert path.read_text(encoding="utf-8") == first_raw

    identical = paired_result(first_raw, "stable-tool")
    state["messages"] = identical
    agent.prepare_context(identical, logger, "run-2", 1, state, state_path)
    assert list((agent.ARTIFACT_DIR / "session-1").glob("*.txt")) == [path]

    conflicting = paired_result("b" * 17_001, "stable-tool")
    original = copy.deepcopy(conflicting)
    state["messages"] = conflicting
    with pytest.raises(ValueError, match="different content"):
        agent.prepare_context(conflicting, logger, "run-3", 1, state, state_path)
    assert conflicting == original and state["messages"] == original
    assert path.read_text(encoding="utf-8") == first_raw


def test_artifact_filename_encoding_is_deterministic_and_collision_free(context_runtime):
    assert agent._artifact_reference("session-1", "tool/a") == (
        agent.ARTIFACT_DIR / "session-1" / "tool%2Fa.txt"
    )
    assert agent._artifact_reference("session-1", "tool_a") == (
        agent.ARTIFACT_DIR / "session-1" / "tool_a.txt"
    )
    with pytest.raises(ValueError, match="artifact filename"):
        agent._artifact_reference("session-1", "x" * 241)


def test_tool_result_marker_text_does_not_skip_budget_compaction(context_runtime):
    _, logger, state, state_path = context_runtime
    raw = "source contains [Full Tool Result: as ordinary text\n" + "x" * 17_000
    messages = paired_result(raw)
    state["messages"] = messages

    agent.prepare_context(messages, logger, "run-1", 1, state, state_path)

    compacted = messages[1]["content"][0]["content"]
    assert len(compacted) < agent.SINGLE_TOOL_RESULT_CHARS
    assert compacted.count("[Full Tool Result:") == 2
    reference = compacted.rsplit("reference=", 1)[1].split("]", 1)[0]
    assert Path(reference).read_text(encoding="utf-8") == raw


def test_persistence_or_checkpoint_failure_never_leaves_active_reference(context_runtime, monkeypatch):
    _, logger, state, state_path = context_runtime
    messages = paired_result("x" * 17001)
    state["messages"] = messages
    original = copy.deepcopy(messages)
    monkeypatch.setattr(agent, "checkpoint", lambda *args, **kwargs: (_ for _ in ()).throw(agent.StateSaveError("no")))
    with pytest.raises(agent.StateSaveError):
        agent.prepare_context(messages, logger, "run-1", 1, state, state_path)
    assert messages == original
    assert state["messages"] == original


def consumed_result_history(count, result_chars=20):
    messages = [{"role": "user", "content": "start"}]
    for number in range(count):
        messages += paired_result(str(number) * result_chars, f"tool-{number}")
    messages.append({"role": "assistant", "content": [{"type": "text", "text": "consumed"}]})
    return messages


def test_microcompact_is_pressure_driven(context_runtime, monkeypatch):
    _, logger, state, state_path = context_runtime
    messages = consumed_result_history(4)
    state["messages"] = messages
    original = copy.deepcopy(messages)
    monkeypatch.setattr(agent, "estimate_context_tokens", lambda *args, **kwargs: 15_999)

    agent.prepare_context(messages, logger, "run-1", 1, state, state_path)

    assert messages == original
    assert not state_path.exists()
    assert not list(agent.ARTIFACT_DIR.rglob("*.txt"))


def test_microcompact_keeps_three_recent_and_stops_at_ratio_oldest_first(context_runtime, monkeypatch):
    _, logger, state, state_path = context_runtime
    messages = consumed_result_history(6)
    state["messages"] = messages

    def fake_estimate(candidate, **_kwargs):
        compacted = sum(
            (_tool := agent._tool_result_text(block)) is not None
            and _tool.startswith("[Earlier tool result saved at ")
            for _, blocks in agent._result_messages(candidate)
            for block in blocks
        )
        return {0: 16_000, 1: 14_000}.get(compacted, 12_800)

    monkeypatch.setattr(agent, "estimate_context_tokens", fake_estimate)
    result = agent.prepare_context(messages, logger, "run-1", 1, state, state_path)

    contents = [blocks[0]["content"] for _, blocks in agent._result_messages(messages)]
    assert contents[0].startswith("[Earlier tool result saved at ")
    assert contents[1].startswith("[Earlier tool result saved at ")
    assert contents[2] == "2" * 20
    assert contents[3:] == [str(number) * 20 for number in range(3, 6)]
    assert result == {"full_compact": False, "estimated_tokens": 12_800}
    assert len(list(agent.ARTIFACT_DIR.rglob("*.txt"))) == 2
    assert agent.load_state(state_path)["messages"] == messages
    agent.validate_llm_message_protocol(messages)


def test_microcompact_reuses_tool_budget_artifact(context_runtime, monkeypatch):
    _, logger, state, state_path = context_runtime
    raw = "x" * 17_001
    messages = paired_result(raw, "tool-0")
    state["messages"] = messages
    agent.prepare_context(messages, logger, "run-1", 1, state, state_path)
    budgeted = messages[1]["content"][0]["content"]
    reference = budgeted.rsplit("reference=", 1)[1].split("]", 1)[0]
    assert Path(reference).read_text(encoding="utf-8") == raw

    for number in range(1, 4):
        messages += paired_result(str(number) * 20, f"tool-{number}")
    messages.append({"role": "assistant", "content": [{"type": "text", "text": "consumed"}]})
    before_artifacts = set(agent.ARTIFACT_DIR.rglob("*.txt"))

    def fake_estimate(candidate, **_kwargs):
        first = agent._result_messages(candidate)[0][1][0]["content"]
        return 12_800 if first.startswith("[Earlier tool result saved at ") else 16_000

    monkeypatch.setattr(agent, "estimate_context_tokens", fake_estimate)
    agent.prepare_context(messages, logger, "run-1", 2, state, state_path)

    placeholder = agent._result_messages(messages)[0][1][0]["content"]
    assert placeholder == f"[Earlier tool result saved at {reference}]"
    assert set(agent.ARTIFACT_DIR.rglob("*.txt")) == before_artifacts

    messages += paired_result("4" * 20, "tool-4")
    messages.append({"role": "assistant", "content": [{"type": "text", "text": "consumed again"}]})

    def repeated_pressure_estimate(candidate, **_kwargs):
        placeholders = sum(
            (agent._tool_result_text(block) or "").startswith("[Earlier tool result saved at ")
            for _, blocks in agent._result_messages(candidate)
            for block in blocks
        )
        return 12_800 if placeholders >= 2 else 16_000

    monkeypatch.setattr(agent, "estimate_context_tokens", repeated_pressure_estimate)
    agent.prepare_context(messages, logger, "run-1", 3, state, state_path)
    contents = [blocks[0]["content"] for _, blocks in agent._result_messages(messages)]
    assert contents[0] == f"[Earlier tool result saved at {reference}]"
    assert contents[1].startswith("[Earlier tool result saved at ")
    assert len(set(agent.ARTIFACT_DIR.rglob("*.txt"))) == len(before_artifacts) + 1


def test_microcompact_exhaustion_then_enters_full_compact(context_runtime, monkeypatch):
    scripted, logger, state, state_path = context_runtime
    scripted.items[:] = [response(summary_text())]
    messages = consumed_result_history(5) + [{"role": "user", "content": "continue"}]
    state["messages"] = messages

    def fake_estimate(candidate, system=agent.SYSTEM, tools=agent.TOOLS):
        if system == agent.SUMMARY_SYSTEM:
            return 100
        if candidate and isinstance(candidate[0].get("content"), list):
            text = candidate[0]["content"][0].get("text", "")
            if text.startswith("<context_summary>"):
                return 7_999
        return 16_000

    monkeypatch.setattr(agent, "estimate_context_tokens", fake_estimate)
    result = agent.prepare_context(messages, logger, "run-1", 1, state, state_path)

    assert result["full_compact"] is True
    assert len(list(agent.ARTIFACT_DIR.rglob("*.txt"))) == 2
    summarized = scripted.calls[0]["messages"]
    assert sum(
        (agent._tool_result_text(block) or "").startswith("[Earlier tool result saved at ")
        for _, blocks in agent._result_messages(summarized[:-1])
        for block in blocks
    ) == 2
    agent.validate_llm_message_protocol(messages)


def long_history():
    messages = []
    for number in range(4):
        messages.extend((
            {"role": "user", "content": f"request {number} " + "x" * 300},
            {"role": "assistant", "content": [{"type": "text", "text": f"answer {number}"}]},
        ))
    return messages


def history_with_current_request():
    return long_history() + [{"role": "user", "content": "current request"}]


def compacted_raw_rounds(messages):
    return sum(message.get("role") == "assistant" for message in messages[1:])


@pytest.mark.parametrize("estimate, expected", [(15_999, False), (16_000, True)])
def test_full_compact_trigger_boundary_is_exact(context_runtime, monkeypatch, estimate, expected):
    scripted, logger, state, state_path = context_runtime
    if expected:
        scripted.items[:] = [response(summary_text())]
    messages = history_with_current_request()
    state["messages"] = messages

    def fake_estimate(candidate, system=agent.SYSTEM, tools=agent.TOOLS):
        if system == agent.SUMMARY_SYSTEM:
            return 100
        if candidate and isinstance(candidate[0].get("content"), list):
            first = candidate[0]["content"][0]
            if first.get("type") == "text" and first.get("text", "").startswith("<context_summary>"):
                return 7_999
        return estimate

    monkeypatch.setattr(agent, "estimate_context_tokens", fake_estimate)
    result = agent.prepare_context(messages, logger, "run-1", 1, state, state_path)
    assert result["full_compact"] is expected
    assert len(scripted.calls) == int(expected)


def test_full_compact_uses_fixed_summary_and_keeps_two_recent_rounds(context_runtime, monkeypatch):
    scripted, logger, state, state_path = context_runtime
    scripted.items[:] = [response(summary_text())]
    messages = long_history()
    state["messages"] = messages
    monkeypatch.setattr(agent, "CONTEXT_TRIGGER_TOKENS", 1)
    monkeypatch.setattr(agent, "CONTEXT_TARGET_TOKENS", 100_000)
    result = agent.prepare_context(messages, logger, "run-1", 1, state, state_path)
    assert result["full_compact"] is True
    assert messages[0]["content"][0]["text"].startswith("<context_summary>")
    assert messages[-4:] == long_history()[-4:]
    agent.validate_llm_message_protocol(messages)
    assert agent.load_state(state_path)["messages"] == messages
    assert scripted.calls[0]["system"] == agent.SUMMARY_SYSTEM
    assert "tools" not in scripted.calls[0]


def test_full_compact_falls_back_from_two_to_one_raw_round(context_runtime, monkeypatch):
    scripted, logger, state, state_path = context_runtime
    scripted.items[:] = [response(summary_text()), response(summary_text())]
    messages = history_with_current_request()
    state["messages"] = messages
    original_estimate = agent.estimate_context_tokens

    def fake_estimate(candidate, system=agent.SYSTEM, tools=agent.TOOLS):
        if system == agent.SUMMARY_SYSTEM:
            return original_estimate(candidate, system=system, tools=tools)
        if candidate and isinstance(candidate[0].get("content"), list):
            text = candidate[0]["content"][0].get("text", "")
            if text.startswith("<context_summary>"):
                return 8_000 if compacted_raw_rounds(candidate) == 2 else 7_999
        return 16_000

    monkeypatch.setattr(agent, "estimate_context_tokens", fake_estimate)
    result = agent.prepare_context(messages, logger, "run-1", 1, state, state_path)
    assert result["full_compact"] is True
    assert compacted_raw_rounds(messages) == 1
    assert len(scripted.calls) == 2
    assert summary_call_contains(scripted.calls[0], "request 1")
    assert summary_call_contains(scripted.calls[1], "request 2")
    agent.validate_llm_message_protocol(messages)
    assert agent.load_state(state_path)["messages"] == messages


def test_full_compact_falls_back_to_minimum_current_raw_suffix(context_runtime, monkeypatch):
    scripted, logger, state, state_path = context_runtime
    scripted.items[:] = [response(summary_text()), response(summary_text()), response(summary_text())]
    messages = history_with_current_request()
    state["messages"] = messages

    def fake_estimate(candidate, system=agent.SYSTEM, tools=agent.TOOLS):
        if system == agent.SUMMARY_SYSTEM:
            return 100
        if candidate and isinstance(candidate[0].get("content"), list):
            text = candidate[0]["content"][0].get("text", "")
            if text.startswith("<context_summary>"):
                return 7_999 if compacted_raw_rounds(candidate) == 0 else 8_000
        return 16_000

    monkeypatch.setattr(agent, "estimate_context_tokens", fake_estimate)
    agent.prepare_context(messages, logger, "run-1", 1, state, state_path)
    assert messages[-1] == {"role": "user", "content": "current request"}
    assert compacted_raw_rounds(messages) == 0
    assert len(scripted.calls) == 3


def test_all_legal_boundaries_missing_target_rolls_back(context_runtime, monkeypatch):
    scripted, logger, state, state_path = context_runtime
    scripted.items[:] = [response(summary_text()), response(summary_text()), response(summary_text())]
    messages = history_with_current_request()
    original = copy.deepcopy(messages)
    state["messages"] = messages

    def fake_estimate(candidate, system=agent.SYSTEM, tools=agent.TOOLS):
        return 100 if system == agent.SUMMARY_SYSTEM else 16_000

    monkeypatch.setattr(agent, "estimate_context_tokens", fake_estimate)
    with pytest.raises(ValueError, match="any legal boundary"):
        agent.prepare_context(messages, logger, "run-1", 1, state, state_path)
    assert messages == original and state["messages"] == original
    assert not state_path.exists()


def test_oversized_first_user_input_is_explicit_and_not_sent(context_runtime):
    scripted, logger, state, state_path = context_runtime
    messages = [{"role": "user", "content": "x" * 50_000}]
    original = copy.deepcopy(messages)
    state["messages"] = messages
    result = agent.agent_loop(messages, logger, "run-1", runtime_state=state, state_path=state_path)
    assert result["error"] == "CONTEXT_COMPACT_FAILED"
    assert messages == original and state["messages"] == original
    assert scripted.calls == []
    events = [json.loads(line) for line in logger.log_path.read_text().splitlines()]
    failed = [event for event in events if event["event_type"] == "context.failed"]
    assert "oversized current input" in failed[-1]["data"]["message"]


def test_boundary_fallback_preserves_tool_pairing(context_runtime, monkeypatch):
    scripted, logger, state, state_path = context_runtime
    scripted.items[:] = [response(summary_text()), response(summary_text()), response(summary_text())]
    messages = [{"role": "user", "content": "start"}]
    for number in range(4):
        messages += paired_result(f"result-{number}", f"tool-{number}")
    messages.append({"role": "user", "content": "continue"})
    state["messages"] = messages

    def fake_estimate(candidate, system=agent.SYSTEM, tools=agent.TOOLS):
        if system == agent.SUMMARY_SYSTEM:
            return 100
        if candidate and isinstance(candidate[0].get("content"), list):
            text = candidate[0]["content"][0].get("text", "")
            if text.startswith("<context_summary>"):
                return 7_999 if compacted_raw_rounds(candidate) == 0 else 8_000
        return 16_000

    monkeypatch.setattr(agent, "estimate_context_tokens", fake_estimate)
    agent.prepare_context(messages, logger, "run-1", 1, state, state_path)
    agent.validate_llm_message_protocol(messages)
    assert messages[-1]["content"] == "continue"


def test_recent_raw_tail_rolls_into_a_later_summary(context_runtime, monkeypatch):
    scripted, logger, state, state_path = context_runtime
    scripted.items[:] = [response(summary_text()), response(summary_text())]
    messages = history_with_current_request()
    state["messages"] = messages
    monkeypatch.setattr(agent, "CONTEXT_TRIGGER_TOKENS", 1)
    monkeypatch.setattr(agent, "CONTEXT_TARGET_TOKENS", 100_000)
    agent.prepare_context(messages, logger, "run-1", 1, state, state_path)
    assert not summary_call_contains(scripted.calls[0], "request 2")

    messages.extend((
        {"role": "assistant", "content": [{"type": "text", "text": "current answer"}]},
        {"role": "user", "content": "next request"},
        {"role": "assistant", "content": [{"type": "text", "text": "next answer"}]},
        {"role": "user", "content": "continue again"},
    ))
    agent.prepare_context(messages, logger, "run-1", 2, state, state_path)
    assert summary_call_contains(scripted.calls[1], "request 2")
    agent.validate_llm_message_protocol(messages)


def test_full_compact_summary_accepts_real_anthropic_sdk_blocks(context_runtime, monkeypatch):
    scripted, logger, state, state_path = context_runtime
    scripted.items[:] = [response(summary_text())]
    messages = [
        {"role": "user", "content": "inspect"},
        {"role": "assistant", "content": [
            TextBlock(type="text", text="I will inspect the file."),
            ToolUseBlock(type="tool_use", id="tool-sdk", name="read_file", input={"path": "agent.py"}),
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tool-sdk", "content": "file contents"},
        ]},
        {"role": "assistant", "content": [TextBlock(type="text", text="inspection complete")]},
        {"role": "user", "content": "continue"},
    ]
    state["messages"] = messages

    def fake_estimate(candidate, system=agent.SYSTEM, tools=agent.TOOLS):
        if system == agent.SUMMARY_SYSTEM:
            return 100
        if candidate and isinstance(candidate[0].get("content"), list):
            text = agent._value(candidate[0]["content"][0], "text", "")
            if str(text).startswith("<context_summary>"):
                return 7_999
        return 16_000

    monkeypatch.setattr(agent, "estimate_context_tokens", fake_estimate)
    result = agent.prepare_context(messages, logger, "run-1", 1, state, state_path)

    assert result["full_compact"] is True
    summary_messages = scripted.calls[0]["messages"]
    assistant_blocks = summary_messages[1]["content"]
    assert isinstance(assistant_blocks[0], TextBlock)
    assert isinstance(assistant_blocks[1], ToolUseBlock)
    assert summary_messages[2]["content"][0]["tool_use_id"] == "tool-sdk"
    assert summary_messages[-1]["content"].startswith("Summarize the preceding older history")
    agent.validate_llm_message_protocol(summary_messages)
    agent.validate_llm_message_protocol(messages)


def test_ptl_recovery_uses_adaptive_boundary_in_same_round(context_runtime, monkeypatch):
    scripted, logger, state, state_path = context_runtime
    scripted.items[:] = [
        PromptTooLong("prompt too long"),
        response(summary_text()), response(summary_text()), response(summary_text()),
        response("done"),
    ]
    messages = history_with_current_request()
    state["messages"] = messages

    def fake_estimate(candidate, system=agent.SYSTEM, tools=agent.TOOLS):
        if system == agent.SUMMARY_SYSTEM:
            return 100
        if candidate and isinstance(candidate[0].get("content"), list):
            text = candidate[0]["content"][0].get("text", "")
            if text.startswith("<context_summary>"):
                return 7_999 if compacted_raw_rounds(candidate) == 0 else 8_000
        return 100

    monkeypatch.setattr(agent, "estimate_context_tokens", fake_estimate)
    result = agent.agent_loop(messages, logger, "run-1", runtime_state=state, state_path=state_path)
    assert result["status"] == "completed" and result["rounds"] == 1
    assert compacted_raw_rounds(messages) == 1  # only the successful retry response
    assert messages[-2] == {"role": "user", "content": "current request"}
    assert len(scripted.calls) == 5
    events = [json.loads(line) for line in logger.log_path.read_text().splitlines()]
    recovery = [event for event in events if event["event_type"] == "context.ptl_recovery"]
    assert [event["data"]["status"] for event in recovery] == ["started", "retrying", "succeeded"]


def test_full_compact_invalid_summary_rolls_back(context_runtime, monkeypatch):
    scripted, logger, state, state_path = context_runtime
    scripted.items[:] = [response("not structured")]
    messages = long_history()
    original = copy.deepcopy(messages)
    state["messages"] = messages
    monkeypatch.setattr(agent, "CONTEXT_TRIGGER_TOKENS", 1)
    with pytest.raises(ValueError, match="nine-section"):
        agent.prepare_context(messages, logger, "run-1", 1, state, state_path)
    assert messages == original and state["messages"] == original
    assert not state_path.exists()


def test_prompt_too_long_gets_one_same_round_recovery(context_runtime, monkeypatch):
    scripted, logger, state, state_path = context_runtime
    scripted.items[:] = [PromptTooLong("prompt too long"), response(summary_text()), response("done")]
    messages = long_history()
    state["messages"] = messages
    result = agent.agent_loop(messages, logger, "run-1", runtime_state=state, state_path=state_path)
    assert result["status"] == "completed" and result["rounds"] == 1
    assert len(scripted.calls) == 3
    events = [json.loads(line) for line in logger.log_path.read_text().splitlines()]
    recovery = [e for e in events if e["event_type"] == "context.ptl_recovery"]
    assert [e["data"]["status"] for e in recovery] == ["started", "retrying", "succeeded"]
    assert all(e["data"]["round"] == 1 for e in recovery)


def test_repeated_prompt_too_long_fails_without_transient_retry(context_runtime):
    scripted, logger, state, state_path = context_runtime
    scripted.items[:] = [PromptTooLong("prompt too long"), response(summary_text()), PromptTooLong("context length exceeded")]
    messages = long_history()
    state["messages"] = messages
    result = agent.agent_loop(messages, logger, "run-1", runtime_state=state, state_path=state_path)
    assert result["error"] == "PROMPT_TOO_LONG"
    assert result["rounds"] == 1 and len(scripted.calls) == 3
    event_types = [json.loads(line)["event_type"] for line in logger.log_path.read_text().splitlines()]
    assert "llm.retry" not in event_types
