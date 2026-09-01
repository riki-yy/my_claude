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


def response(
    *blocks,
    input_tokens=None,
    output_tokens=None,
    cache_creation_input_tokens=None,
    cache_read_input_tokens=None,
    stop_reason="end_turn",
):
    usage = None
    if any(value is not None for value in (input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens)):
        usage = SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
        )
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
    monkeypatch.setattr(agent, "WORKSPACE_ROOT", tmp_path)
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


@pytest.mark.parametrize(
    "usage_values,expected_input_tokens",
    [
        ({"input_tokens": 20}, 20),
        ({"input_tokens": 20, "cache_read_input_tokens": 30}, 50),
        ({"input_tokens": 20, "cache_creation_input_tokens": 40}, 60),
    ],
)
def test_input_tokens_include_available_cache_usage(runtime, usage_values, expected_input_tokens):
    fake_response = response(block("text", text="answer"), output_tokens=5, **usage_values)
    if usage_values == {"input_tokens": 20}:
        fake_response.usage = SimpleNamespace(input_tokens=20, output_tokens=5)
    _, logger = set_script(runtime, [fake_response])
    agent.agent_loop([], logger, "run-1")
    completed = next(event for event in read_events(logger) if event["event_type"] == "llm.completed")
    assert completed["data"]["input_tokens"] == expected_input_tokens
    assert completed["data"]["output_tokens"] == 5


def test_cli_and_jsonl_share_total_input_tokens(runtime):
    _, logger = set_script(runtime, [
        response(
            block("text", text="answer"),
            input_tokens=20,
            cache_creation_input_tokens=30,
            cache_read_input_tokens=40,
            output_tokens=5,
        )
    ])
    agent.agent_loop([], logger, "run-1")

    assert "input_tokens=90 | output_tokens=5" in logger.stream.getvalue()
    completed = next(event for event in read_events(logger) if event["event_type"] == "llm.completed")
    assert completed["data"]["input_tokens"] == 90


def test_tool_then_final_text_and_message_order(runtime):
    scripted, logger = set_script(runtime, [
        response(block("tool_use", id="tool-1", name="write_file", input={"path": "note.txt", "content": "ping"}), stop_reason="tool_use"),
        response(block("text", text="done")),
    ])
    messages = [{"role": "user", "content": "write ping"}]
    result = agent.agent_loop(messages, logger, "run-1")
    assert result["final_text"] == "done"
    assert messages[1]["role"] == "assistant"
    assert messages[2] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "Wrote 4 characters to note.txt"}]}
    assert scripted.calls[1]["messages"] is messages
    assert [e["event_type"] for e in read_events(logger)] == [
        "llm.started", "llm.completed", "tool.started", "tool.completed", "llm.started", "llm.completed"
    ]


def test_multiple_tool_rounds(runtime):
    _, logger = set_script(runtime, [
        response(block("tool_use", id="1", name="write_file", input={"path": "a.txt", "content": "a"})),
        response(block("tool_use", id="2", name="read_file", input={"path": "a.txt"})),
        response(block("text", text="finished")),
    ])
    result = agent.agent_loop([], logger, "run-1")
    assert result["rounds"] == 3
    assert result["status"] == "completed"


@pytest.mark.parametrize("name,tool_input,code", [
    ("read_file", {}, "INVALID_TOOL_INPUT"),
    ("read_file", {"path": 3}, "INVALID_TOOL_INPUT"),
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
    block("tool_use", name="read_file", input={"path": "x"}),
    block("tool_use", id="1", input={"path": "x"}),
    block("tool_use", id="1", name="read_file", input="x"),
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
        response(block("tool_use", id=str(i), name="read_file", input={"path": "missing.txt"})) for i in range(3)
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
    _, logger = set_script(runtime, [response(block("tool_use", id="1", name="write_file", input={"path": "secret.txt", "content": "sk-supersecret123 " + "x" * 400})), response(block("text", text="ok"))])
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


def test_tool_call_label_and_name_are_highlighted_only_for_color_tty(tmp_path, monkeypatch):
    class TTYStream(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("NO_COLOR", raising=False)
    stream = TTYStream()
    logger = agent.EventLogger("session-1", tmp_path / "tty.jsonl", stream)

    for index, name in enumerate(agent.TOOL_HANDLERS):
        logger.emit(
            "tool.started",
            "run-1",
            tool_name=name,
            tool_use_id=str(index),
            arguments={"summary": 'path="agent.py"'},
            display=f'{name} path="agent.py"',
        )

    shown = stream.getvalue()
    for name in agent.TOOL_HANDLERS:
        assert f"{agent.TOOL_NAME_COLOR}[Tool Call] {name}{agent.ANSI_RESET} path=\"agent.py\"" in shown
    assert f"{agent.TOOL_NAME_COLOR}path=" not in shown
    assert "\\u001b" not in logger.log_path.read_text()
    assert "\x1b" not in logger.log_path.read_text()


@pytest.mark.parametrize("disable_mode", ["non_tty", "term_dumb", "no_color"])
def test_tool_name_color_falls_back_to_plain_text(tmp_path, monkeypatch, disable_mode):
    class ConfigurableStream(io.StringIO):
        def isatty(self):
            return disable_mode != "non_tty"

    monkeypatch.setenv("TERM", "dumb" if disable_mode == "term_dumb" else "xterm-256color")
    if disable_mode == "no_color":
        monkeypatch.setenv("NO_COLOR", "1")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)
    stream = ConfigurableStream()
    logger = agent.EventLogger("session-1", None, stream)
    logger.emit(
        "tool.started",
        "run-1",
        tool_name="read_file",
        tool_use_id="1",
        arguments={"summary": 'path="agent.py"'},
        display='read_file path="agent.py"',
    )

    assert stream.getvalue() == '[Tool Call] read_file path="agent.py"\n'
    assert "\x1b" not in stream.getvalue()


def test_llm_result_omits_usage_that_api_did_not_return(runtime):
    _, logger = set_script(runtime, [response(block("text", text="answer"))])
    agent.agent_loop([], logger, "run-1")
    shown = logger.stream.getvalue()
    assert "[LLM Result]" in shown
    assert "input_tokens=" not in shown
    assert "output_tokens=" not in shown


def test_long_read_result_is_complete_for_model_but_summarized_for_cli_and_jsonl(runtime):
    content = "first line\n" + "SOURCE_MARKER " * 200 + "\nlast line\n"
    agent.WORKSPACE_ROOT.joinpath("large.py").write_text(content, encoding="utf-8")
    scripted, logger = set_script(runtime, [
        response(block("tool_use", id="read-1", name="read_file", input={"path": "large.py"})),
        response(block("text", text="done")),
    ])
    messages = []
    agent.agent_loop(messages, logger, "run-1")

    assert messages[1]["content"][0]["content"] == content
    assert scripted.calls[1]["messages"] is messages
    shown = logger.stream.getvalue()
    assert '[Tool Call] read_file path="large.py"' in shown
    assert "[Tool Result] success | 3 lines |" in shown
    assert content not in shown
    assert len(next(line for line in shown.splitlines() if line.startswith("[Tool Result]"))) < 220

    completed = next(event for event in read_events(logger) if event["event_type"] == "tool.completed")
    result = completed["data"]["result"]
    assert result["original_length"] == len(content)
    assert result["truncated"] is True
    assert result["lines"] == 3
    assert content not in logger.log_path.read_text()


def test_tool_error_cli_keeps_code_and_short_detail(runtime):
    _, logger = set_script(runtime, [
        response(block("tool_use", id="read-1", name="read_file", input={"path": "missing.txt"})),
        response(block("text", text="recovered")),
    ])
    agent.agent_loop([], logger, "run-1")
    shown = logger.stream.getvalue()
    assert '[Tool Call] read_file path="missing.txt"' in shown
    assert "[Tool Result] error=FILE_NOT_FOUND | file does not exist: missing.txt" in shown
    assert "[Tool Error]" not in shown


def test_search_cli_lists_only_a_bounded_preview(runtime):
    for index in range(8):
        agent.WORKSPACE_ROOT.joinpath(f"file-{index}.txt").write_text("match", encoding="utf-8")
    _, logger = set_script(runtime, [
        response(block("tool_use", id="glob-1", name="glob", input={"pattern": "*.txt"})),
        response(block("text", text="done")),
    ])
    agent.agent_loop([], logger, "run-1")
    shown = logger.stream.getvalue()
    assert '[Tool Call] glob pattern="*.txt"' in shown
    assert "[Tool Result] 8 matches" in shown
    assert "  ... 3 more" in shown
    assert "file-7.txt" not in shown


def test_unknown_tool_uses_bounded_fallback_summaries(runtime):
    long_value = "x" * 1000
    _, logger = set_script(runtime, [
        response(block("tool_use", id="unknown-1", name="future_tool", input={"value": long_value})),
        response(block("text", text="done")),
    ])
    agent.agent_loop([], logger, "run-1")
    shown = logger.stream.getvalue()
    assert "[Tool Call] future_tool value=" in shown
    assert "[Tool Result] error=UNKNOWN_TOOL | unknown tool: future_tool" in shown
    assert long_value not in shown


def test_tools_and_handlers_have_same_names():
    schema_names = {tool["name"] for tool in agent.TOOLS}
    assert schema_names == {"bash", "read_file", "write_file", "edit_file", "glob", "grep"}
    assert schema_names == set(agent.TOOL_HANDLERS)
    assert all(tool["input_schema"]["type"] == "object" for tool in agent.TOOLS)


def test_handler_map_dispatches_without_agent_loop_tool_branches(runtime, monkeypatch):
    called = []

    def custom_handler(tool_input):
        called.append(tool_input)
        return "custom result", False, None

    monkeypatch.setitem(agent.TOOL_HANDLERS, "custom", custom_handler)
    _, logger = set_script(runtime, [
        response(block("tool_use", id="custom-1", name="custom", input={"value": 42})),
        response(block("text", text="complete")),
    ])
    messages = []
    result = agent.agent_loop(messages, logger, "run-1")
    assert result["final_text"] == "complete"
    assert called == [{"value": 42}]
    assert messages[1]["content"][0]["content"] == "custom result"


def test_unexpected_handler_exception_is_recoverable(runtime, monkeypatch):
    def broken_handler(_tool_input):
        raise RuntimeError("broken")

    monkeypatch.setitem(agent.TOOL_HANDLERS, "broken", broken_handler)
    _, logger = set_script(runtime, [
        response(block("tool_use", id="broken-1", name="broken", input={})),
        response(block("text", text="recovered")),
    ])
    messages = []
    result = agent.agent_loop(messages, logger, "run-1")
    assert result["final_text"] == "recovered"
    assert messages[1]["content"][0]["is_error"] is True
    failed = next(event for event in read_events(logger) if event["event_type"] == "tool.failed")
    assert failed["data"]["error_code"] == "TOOL_EXECUTION_ERROR"


def test_write_read_and_create_parent_directories(runtime):
    assert agent.run_write_file({"path": "nested/note.txt", "content": "hello"}) == (
        "Wrote 5 characters to nested/note.txt", False, None
    )
    assert agent.run_read_file({"path": "nested/note.txt"}) == ("hello", False, None)


@pytest.mark.parametrize("raw_path", ["../outside.txt", "/tmp/outside.txt"])
def test_file_tools_reject_paths_outside_workspace(runtime, raw_path):
    for handler, tool_input in [
        (agent.run_read_file, {"path": raw_path}),
        (agent.run_write_file, {"path": raw_path, "content": "no"}),
        (agent.run_edit_file, {"path": raw_path, "old_text": "a", "new_text": "b"}),
    ]:
        _, is_error, code = handler(tool_input)
        assert is_error is True
        assert code == "PATH_OUTSIDE_WORKSPACE"


def test_file_tools_reject_symlink_escape(runtime, tmp_path):
    outside = tmp_path.parent / "outside-v02.txt"
    outside.write_text("secret", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(outside)
    result, is_error, code = agent.run_read_file({"path": "link.txt"})
    assert is_error is True
    assert code == "PATH_OUTSIDE_WORKSPACE"
    assert "secret" not in result


def test_read_missing_file_and_non_text_file(runtime, tmp_path):
    assert agent.run_read_file({"path": "missing.txt"})[2] == "FILE_NOT_FOUND"
    (tmp_path / "binary.bin").write_bytes(b"\xff\xfe")
    assert agent.run_read_file({"path": "binary.bin"})[2] == "FILE_NOT_TEXT"


def test_edit_requires_one_unique_match(runtime, tmp_path):
    path = tmp_path / "edit.txt"
    path.write_text("one two one", encoding="utf-8")
    assert agent.run_edit_file({"path": "edit.txt", "old_text": "missing", "new_text": "x"})[2] == "EDIT_TARGET_NOT_FOUND"
    assert agent.run_edit_file({"path": "edit.txt", "old_text": "one", "new_text": "x"})[2] == "EDIT_TARGET_NOT_UNIQUE"
    assert agent.run_edit_file({"path": "edit.txt", "old_text": "two", "new_text": "2"}) == ("Edited edit.txt", False, None)
    assert path.read_text(encoding="utf-8") == "one 2 one"


def test_glob_matches_and_rejects_escape(runtime, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("print('a')", encoding="utf-8")
    (tmp_path / "src" / "b.txt").write_text("b", encoding="utf-8")
    assert agent.run_glob({"pattern": "**/*.py"}) == ("src/a.py", False, None)
    assert agent.run_glob({"pattern": "../*.py"})[2] == "PATH_OUTSIDE_WORKSPACE"


def test_grep_searches_files_and_handles_errors(runtime, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("alpha\nbeta alpha\n", encoding="utf-8")
    result, is_error, code = agent.run_grep({"pattern": "alpha", "path": "src"})
    assert (is_error, code) == (False, None)
    assert result.splitlines() == ["src/a.py:1:alpha", "src/a.py:2:beta alpha"]
    assert agent.run_grep({"pattern": "[", "path": "src"})[2] == "INVALID_PATTERN"
    assert agent.run_grep({"pattern": "x", "path": "missing"})[2] == "FILE_NOT_FOUND"
    assert agent.run_grep({"pattern": "x", "path": "../"})[2] == "PATH_OUTSIDE_WORKSPACE"


def test_bash_uses_fixed_workspace_and_reports_empty_output(runtime, tmp_path):
    result, is_error, code = agent.run_bash({"command": "pwd"})
    assert (is_error, code) == (False, None)
    assert str(tmp_path) in result
    assert agent.run_bash({"command": ":"}) == ("exit_code=0\n(no output)", False, None)


def test_bash_nonzero_timeout_and_invalid_arguments(runtime):
    result, is_error, code = agent.run_bash({"command": "printf failure >&2; exit 7"})
    assert is_error is True
    assert code == "SHELL_NONZERO_EXIT"
    assert "exit_code=7" in result and "failure" in result
    assert agent.run_bash({"command": "sleep 1", "timeout_seconds": 0.01})[2] == "SHELL_TIMEOUT"
    assert agent.run_bash({"command": "pwd", "timeout_seconds": 31})[2] == "INVALID_TOOL_INPUT"
    assert agent.run_bash({"command": 3})[2] == "INVALID_TOOL_INPUT"


def test_bash_output_is_bounded(runtime, monkeypatch):
    monkeypatch.setattr(agent, "TOOL_OUTPUT_LIMIT", 20)
    result, is_error, code = agent.run_bash({"command": "printf 1234567890123456789012345"})
    assert (is_error, code) == (False, None)
    assert "[output truncated from 25 characters]" in result


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
