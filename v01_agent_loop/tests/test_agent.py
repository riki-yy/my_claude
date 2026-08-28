import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent


def block(block_type, **values):
    return SimpleNamespace(type=block_type, **values)


def response(*blocks, input_tokens=None, output_tokens=None, stop_reason="end_turn"):
    usage = None
    if input_tokens is not None or output_tokens is not None:
        usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    return SimpleNamespace(content=list(blocks), usage=usage, stop_reason=stop_reason)


class ScriptedMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise RuntimeError("Fake Model response queue exhausted")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    scripted = ScriptedMessages([])
    monkeypatch.setattr(agent, "client", SimpleNamespace(messages=scripted))
    monkeypatch.setattr(agent, "MODEL", "fake-model")
    logger = agent.EventLogger("session-1", tmp_path / "session-1.jsonl", io.StringIO())
    return scripted, logger


def set_script(runtime, items):
    scripted, logger = runtime
    scripted.responses[:] = items
    return scripted, logger


def read_events(logger):
    return [json.loads(line) for line in logger.log_path.read_text().splitlines()]


def test_line_editing_configures_reference_libedit_bindings():
    class FakeReadline:
        def __init__(self):
            self.bindings = []

        def parse_and_bind(self, binding):
            self.bindings.append(binding)

    fake = FakeReadline()
    agent._configure_line_editing(fake)
    assert fake.bindings == [
        "set bind-tty-special-chars off",
        "set input-meta on",
        "set output-meta on",
        "set convert-meta off",
    ]


def test_direct_text_response(runtime):
    scripted, logger = set_script(runtime, [response(block("text", text="hello"), input_tokens=4, output_tokens=2)])
    messages = [{"role": "user", "content": "hi"}]
    result = agent.agent_loop(messages, logger, "run-1")
    assert result == {"status": "completed", "final_text": "hello", "rounds": 1, "error": None}
    assert scripted.calls[0]["model"] == "fake-model"
    events = read_events(logger)
    completed = next(e for e in events if e["event_type"] == "llm.completed")
    assert completed["data"]["input_tokens"] == 4
    assert completed["data"]["output_tokens"] == 2
    assert completed["data"]["tool_calls"] == 0


def test_echo_then_final_text_and_message_order(runtime):
    scripted, logger = set_script(runtime, [
        response(block("tool_use", id="tool-1", name="echo", input={"text": "ping"}), stop_reason="tool_use"),
        response(block("text", text="done")),
    ])
    messages = [{"role": "user", "content": "echo ping"}]
    result = agent.agent_loop(messages, logger, "run-1")
    assert result["final_text"] == "done"
    assert messages[1]["role"] == "assistant"
    assert messages[2] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "ping"}]}
    assert scripted.calls[1]["messages"] is messages
    assert [e["event_type"] for e in read_events(logger)] == [
        "llm.started", "llm.completed", "tool.started", "tool.completed", "llm.started", "llm.completed"
    ]


def test_multiple_tool_rounds(runtime):
    _, logger = set_script(runtime, [
        response(block("tool_use", id="1", name="echo", input={"text": "a"})),
        response(block("tool_use", id="2", name="echo", input={"text": "b"})),
        response(block("text", text="finished")),
    ])
    result = agent.agent_loop([], logger, "run-1")
    assert result["rounds"] == 3
    assert result["status"] == "completed"


@pytest.mark.parametrize("name,tool_input,code", [
    ("echo", {}, "INVALID_TOOL_INPUT"),
    ("echo", {"text": 3}, "INVALID_TOOL_INPUT"),
    ("missing", {}, "UNKNOWN_TOOL"),
])
def test_tool_errors_are_returned_to_model(runtime, name, tool_input, code):
    scripted, logger = set_script(runtime, [
        response(block("tool_use", id="bad", name=name, input=tool_input)),
        response(block("text", text="recovered")),
    ])
    messages = []
    result = agent.agent_loop(messages, logger, "run-1")
    tool_result = messages[1]["content"][0]
    assert tool_result["is_error"] is True
    assert result["final_text"] == "recovered"
    failed = next(e for e in read_events(logger) if e["event_type"] == "tool.failed")
    assert failed["data"]["error_code"] == code
    assert len(scripted.calls) == 2


@pytest.mark.parametrize("bad_response", [
    SimpleNamespace(content=None, usage=None, stop_reason=None),
    SimpleNamespace(content=[], usage=None, stop_reason=None),
    response(block("image", source={})),
])
def test_invalid_response_is_protocol_error(runtime, bad_response):
    _, logger = set_script(runtime, [bad_response])
    result = agent.agent_loop([], logger, "run-1")
    assert result["error"] == "PROTOCOL_ERROR"


@pytest.mark.parametrize("tool_block", [
    block("tool_use", name="echo", input={"text": "x"}),
    block("tool_use", id="1", input={"text": "x"}),
    block("tool_use", id="1", name="echo", input="x"),
])
def test_invalid_tool_use_is_protocol_error(runtime, tool_block):
    _, logger = set_script(runtime, [response(tool_block)])
    assert agent.agent_loop([], logger, "run-1")["error"] == "PROTOCOL_ERROR"


def test_model_exception_and_exhausted_queue(runtime):
    _, logger = set_script(runtime, [ConnectionError("offline")])
    assert agent.agent_loop([], logger, "run-1")["error"] == "MODEL_CALL_ERROR"
    scripted, logger2 = set_script(runtime, [])
    logger2.log_path = logger.log_path.parent / "empty.jsonl"
    assert agent.agent_loop([], logger2, "run-2")["error"] == "MODEL_CALL_ERROR"
    assert len(scripted.calls) == 2


def test_max_rounds(runtime):
    _, logger = set_script(runtime, [
        response(block("tool_use", id=str(i), name="echo", input={"text": "again"})) for i in range(3)
    ])
    result = agent.agent_loop([], logger, "run-1", max_rounds=3)
    assert result["error"] == "MAX_ROUNDS_EXCEEDED"
    assert result["rounds"] == 3


def test_session_can_hold_multiple_runs(runtime):
    _, logger = set_script(runtime, [response(block("text", text="one")), response(block("text", text="two"))])
    messages = []
    first = agent.run_once(messages, "first", logger)
    second = agent.run_once(messages, "second", logger)
    assert first["run_id"] != second["run_id"]
    assert [m["content"] for m in messages if m["role"] == "user"] == ["first", "second"]
    assert {e["run_id"] for e in read_events(logger) if e["run_id"]} == {first["run_id"], second["run_id"]}


def test_jsonl_fields_sequence_truncation_and_redaction(runtime, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-supersecret123")
    _, logger = set_script(runtime, [response(block("tool_use", id="1", name="echo", input={"text": "sk-supersecret123 " + "x" * 400})), response(block("text", text="ok"))])
    agent.agent_loop([], logger, "run-1")
    events = read_events(logger)
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    required = {"schema_version", "event_id", "session_id", "run_id", "sequence", "timestamp", "event_type", "data"}
    assert all(required <= set(e) for e in events)
    raw = logger.log_path.read_text()
    assert "sk-supersecret123" not in raw
    tool = next(e for e in events if e["event_type"] == "tool.started")
    assert tool["data"]["arguments"]["truncated"] is True
    assert tool["data"]["arguments"]["original_length"] > agent.SUMMARY_LIMIT


def test_final_result_is_redacted(runtime, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-finalsecret123")
    _, logger = set_script(runtime, [response(block("text", text="sk-finalsecret123"))])
    agent.run_once([], "question", logger)
    assert "sk-finalsecret123" not in logger.log_path.read_text()
    assert "[REDACTED]" in logger.log_path.read_text()


def test_jsonl_write_failure_does_not_change_result(runtime, monkeypatch):
    _, logger = set_script(runtime, [response(block("text", text="still works"))])
    def fail_open(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(Path, "open", fail_open)
    result = agent.agent_loop([], logger, "run-1")
    assert result["final_text"] == "still works"


def test_cli_output_has_status_but_not_messages(runtime):
    _, logger = set_script(runtime, [response(block("text", text="answer"), input_tokens=20, output_tokens=15)])
    agent.run_once([], "question", logger)
    shown = logger.stream.getvalue()
    assert "[LLM] requesting model=fake-model round=1" in shown
    assert "[Thinking]" not in shown
    assert "正在分析任务" not in shown
    assert "[LLM Result] model=fake-model | round=1 | duration=" in shown
    assert "input_tokens=20 | output_tokens=15 | stop_reason=end_turn | tool_calls=0" in shown
    assert "[Final]" in shown
    assert "'role': 'user'" not in shown


def test_llm_result_omits_usage_that_api_did_not_return(runtime):
    _, logger = set_script(runtime, [response(block("text", text="answer"))])
    agent.agent_loop([], logger, "run-1")
    shown = logger.stream.getvalue()
    assert "[LLM Result]" in shown
    assert "input_tokens=" not in shown
    assert "output_tokens=" not in shown


@pytest.mark.parametrize("answer", ["", "exit", "quit"])
def test_cli_normal_exit(monkeypatch, tmp_path, answer):
    monkeypatch.setattr(agent, "configure_model", lambda: None)
    monkeypatch.setattr(agent, "LOG_DIR", tmp_path)
    monkeypatch.setattr(agent, "client", SimpleNamespace(messages=ScriptedMessages([])))
    monkeypatch.setattr(agent, "MODEL", "fake-model")
    output = io.StringIO()
    assert agent.cli(lambda _: answer, output) == 0
    events = [json.loads(line) for line in next(tmp_path.iterdir()).read_text().splitlines()]
    assert [e["event_type"] for e in events] == ["session.started", "session.ended"]


def test_cli_eof_and_keyboard_interrupt(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "configure_model", lambda: None)
    monkeypatch.setattr(agent, "LOG_DIR", tmp_path)
    monkeypatch.setattr(agent, "client", SimpleNamespace(messages=ScriptedMessages([])))
    monkeypatch.setattr(agent, "MODEL", "fake-model")
    for exc in (EOFError(), KeyboardInterrupt()):
        output = io.StringIO()
        def interrupted(_prompt, error=exc):
            raise error
        assert agent.cli(interrupted, output) == 0


def test_missing_configuration_is_clear(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(agent, "ROOT_DIR", tmp_path)
    assert agent.cli(lambda _: "exit") == 2
    error = capsys.readouterr().err
    assert "CONFIG_ERROR" in error
    assert "API_KEY=" not in error
