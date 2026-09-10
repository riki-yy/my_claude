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


@pytest.fixture(autouse=True)
def isolate_hooks(monkeypatch):
    """Keep per-test hook registrations from leaking into later tests."""
    monkeypatch.setattr(
        agent,
        "HOOKS",
        {event: list(callbacks) for event, callbacks in agent.HOOKS.items()},
    )
    monkeypatch.setattr(agent, "TODO_STATE", [])


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    scripted = ScriptedMessages([])
    monkeypatch.setattr(agent, "client", SimpleNamespace(messages=scripted))
    monkeypatch.setattr(agent, "MODEL", "fake-model")
    monkeypatch.setattr(agent, "WORKSPACE_ROOT", tmp_path)
    # V02 regression tests isolate Tool Runtime behavior. V03 permission tests
    # install the concrete rule sets they need explicitly.
    monkeypatch.setattr(agent, "DENY_RULES", [])
    monkeypatch.setattr(agent, "ASK_RULES", [])
    logger = agent.EventLogger("session-1", tmp_path / "session-1.jsonl", io.StringIO())
    return scripted, logger


def set_script(runtime, items):
    scripted, logger = runtime
    scripted.responses[:] = items
    return scripted, logger


def read_events(logger):
    return [json.loads(line) for line in logger.log_path.read_text().splitlines()]


def test_restored_logger_continues_existing_jsonl_sequence(tmp_path):
    path = tmp_path / "session.jsonl"
    first = agent.EventLogger("session-1", path, io.StringIO())
    first.emit("session.started")
    first.emit("checkpoint.saved")
    restored = agent.EventLogger("session-1", path, io.StringIO())
    restored.emit("state.restored")
    assert [event["sequence"] for event in read_events(restored)] == [1, 2, 3]


def test_register_and_trigger_hooks_preserve_order_and_shared_context(monkeypatch):
    monkeypatch.setattr(agent, "HOOKS", {"PreToolUse": [], "PostToolUse": []})
    calls = []
    context = {"value": 1}

    def first(received):
        calls.append(("first", received))
        received["value"] = 2

    def second(received):
        calls.append(("second", received))

    agent.register_hook("PreToolUse", first)
    agent.register_hook("PreToolUse", second)
    agent.trigger_hooks("PreToolUse", context)

    assert calls == [("first", context), ("second", context)]
    assert context == {"value": 2}


def test_hook_events_are_isolated(monkeypatch):
    monkeypatch.setattr(agent, "HOOKS", {"PreToolUse": [], "PostToolUse": []})
    calls = []
    agent.register_hook("PreToolUse", lambda context: calls.append("before"))
    agent.register_hook("PostToolUse", lambda context: calls.append("after"))

    agent.trigger_hooks("PostToolUse", {})

    assert calls == ["after"]


@pytest.mark.parametrize("operation", [
    lambda: agent.register_hook("missing", lambda context: None),
    lambda: agent.trigger_hooks("missing", {}),
])
def test_unknown_hook_event_is_rejected(operation):
    with pytest.raises(ValueError, match="UNKNOWN_HOOK_EVENT"):
        operation()


def test_register_hook_rejects_non_callable():
    with pytest.raises(TypeError, match="callable"):
        agent.register_hook("PreToolUse", None)


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
        "llm.started", "llm.completed", "tool.started", "permission.allowed",
        "tool.completed", "llm.started", "llm.completed"
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


def test_non_retryable_model_exception_and_exhausted_queue(runtime):
    _, logger = set_script(runtime, [RuntimeError("bad request")])
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
    assert "[LLM Result] model=fake-model | round=1 | attempt=1 | duration=" in shown
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
    assert schema_names == {"bash", "read_file", "write_file", "edit_file", "glob", "grep", "todo_write"}
    assert schema_names == set(agent.TOOL_HANDLERS)
    assert all(tool["input_schema"]["type"] == "object" for tool in agent.TOOLS)


def test_todo_schema_describes_complete_minimal_state():
    schema = next(tool["input_schema"] for tool in agent.TOOLS if tool["name"] == "todo_write")
    assert schema["required"] == ["todos"]
    assert schema["additionalProperties"] is False
    todos = schema["properties"]["todos"]
    assert todos["maxItems"] == 20
    assert todos["items"]["required"] == ["id", "content", "status"]
    assert todos["items"]["additionalProperties"] is False
    assert todos["items"]["properties"]["status"]["enum"] == [
        "pending", "in_progress", "completed"
    ]


def test_system_and_tool_guidance_promote_practical_moderate_todos():
    assert "make todo_write the first tool call before discovery or mutation" in agent.SYSTEM
    assert "even when the user does not explicitly ask for a plan or Todo" in agent.SYSTEM
    assert "not meta-work such as planning or breaking down the task" in agent.SYSTEM
    assert "independently actionable and verifiable stage" in agent.SYSTEM
    assert "use separate items when files or components can progress independently" in agent.SYSTEM
    assert "do not split simple operations into many tiny todos" in agent.SYSTEM

    todo_tool = next(tool for tool in agent.TOOLS if tool["name"] == "todo_write")
    description = todo_tool["description"]
    assert "complex multi-step work" in description
    assert "multi-file implementation and verification tasks" in description
    assert "separate multi-file components when their progress can differ" in description
    assert "omit planning as a meta-task" in description
    assert "avoid tiny operational steps" in description


def test_todo_write_tool_call_summary_shows_count_without_dumping_todos(runtime):
    todos = [
        {"id": "1", "content": "private first content", "status": "in_progress"},
        {"id": "2", "content": "private second content", "status": "pending"},
        {"id": "3", "content": "private third content", "status": "completed"},
    ]
    _, logger = set_script(runtime, [
        response(block("tool_use", id="todo-summary", name="todo_write", input={"todos": todos})),
        response(block("text", text="done")),
    ])

    agent.agent_loop([], logger, "run-1")

    shown = logger.stream.getvalue()
    assert "[Tool Call] todo_write todos=3" in shown
    assert "[Tool Call] todo_write no key arguments" not in shown
    started = next(event for event in read_events(logger) if event["event_type"] == "tool.started")
    assert started["data"]["arguments"]["summary"] == "todos=3"
    assert started["data"]["arguments"]["fields"] == {"todos": 3}
    assert "private first content" not in json.dumps(started, ensure_ascii=False)


def test_todo_write_replaces_complete_state_and_can_clear_it():
    first = [
        {"id": "read", "content": "读取 README", "status": "completed"},
        {"id": "edit", "content": "修改代码", "status": "in_progress"},
        {"id": "test", "content": "运行测试", "status": "pending"},
    ]
    result = agent.run_todo_write({"todos": first})
    assert result == ("✓ 读取 README\n› 修改代码\n○ 运行测试", False, None)
    assert agent.TODO_STATE == first
    assert agent.TODO_STATE is not first
    assert all(stored is not supplied for stored, supplied in zip(agent.TODO_STATE, first))

    replacement = [{"id": "ship", "content": "交付", "status": "pending"}]
    assert agent.run_todo_write({"todos": replacement}) == ("○ 交付", False, None)
    assert agent.TODO_STATE == replacement
    assert agent.run_todo_write({"todos": []}) == ("", False, None)
    assert agent.TODO_STATE == []


@pytest.mark.parametrize("bad_todos", [
    [{"id": "", "content": "valid", "status": "pending"}],
    [{"id": "   ", "content": "valid", "status": "pending"}],
    [{"id": "1", "content": "", "status": "pending"}],
    [{"id": "1", "content": "  ", "status": "pending"}],
    [{"id": "1", "content": "valid", "status": "blocked"}],
    [
        {"id": "same", "content": "one", "status": "pending"},
        {"id": "same", "content": "two", "status": "completed"},
    ],
    [
        {"id": "1", "content": "one", "status": "in_progress"},
        {"id": "2", "content": "two", "status": "in_progress"},
    ],
    [{"id": "1", "content": "valid", "status": "pending", "priority": 1}],
    [{"id": "1", "content": "valid"}],
    ["not-an-object"],
])
def test_todo_write_rejects_invalid_items_atomically(bad_todos):
    old_state = [{"id": "old", "content": "保留原状态", "status": "in_progress"}]
    agent.TODO_STATE = [dict(todo) for todo in old_state]
    result, is_error, error_code = agent.run_todo_write({"todos": bad_todos})
    assert result.startswith("INVALID_TOOL_INPUT: ")
    assert is_error is True
    assert error_code == "INVALID_TOOL_INPUT"
    assert agent.TODO_STATE == old_state


@pytest.mark.parametrize("bad_input", [
    {},
    {"todos": None},
    {"todos": {}, "extra": True},
    {"todos": [], "extra": True},
])
def test_todo_write_rejects_invalid_complete_input_atomically(bad_input):
    old_state = [{"id": "old", "content": "keep", "status": "pending"}]
    agent.TODO_STATE = [dict(todo) for todo in old_state]
    result, is_error, error_code = agent.run_todo_write(bad_input)
    assert result.startswith("INVALID_TOOL_INPUT: ")
    assert is_error is True
    assert error_code == "INVALID_TOOL_INPUT"
    assert agent.TODO_STATE == old_state


def test_todo_write_rejects_more_than_twenty_items_atomically():
    old_state = [{"id": "old", "content": "keep", "status": "pending"}]
    agent.TODO_STATE = [dict(todo) for todo in old_state]
    too_many = [
        {"id": str(index), "content": f"todo {index}", "status": "pending"}
        for index in range(21)
    ]
    result = agent.run_todo_write({"todos": too_many})
    assert result[1:] == (True, "INVALID_TOOL_INPUT")
    assert agent.TODO_STATE == old_state


def test_todo_write_accepts_exactly_twenty_items():
    todos = [
        {"id": str(index), "content": f"todo {index}", "status": "pending"}
        for index in range(20)
    ]
    result, is_error, error_code = agent.run_todo_write({"todos": todos})
    assert is_error is False
    assert error_code is None
    assert result.splitlines() == [f"○ todo {index}" for index in range(20)]
    assert agent.TODO_STATE == todos


def test_render_todos_preserves_original_content_without_summary_rewrite_or_truncation():
    long_content = "第一行：保留空格  and symbols <>\n第二行：" + "细节" * 200
    todos = [
        {"id": "1", "content": long_content, "status": "pending"},
        {"id": "2", "content": "原样处理中", "status": "in_progress"},
        {"id": "3", "content": "原样完成", "status": "completed"},
    ]
    rendered = agent.render_todos(todos)
    assert rendered == f"○ {long_content}\n› 原样处理中\n✓ 原样完成"
    assert long_content in rendered


def test_todo_write_uses_one_complete_render_for_cli_tool_result_and_messages(runtime):
    long_content = "完整内容 " + "不截断" * 100
    todos = [
        {"id": "1", "content": long_content, "status": "in_progress"},
        {"id": "2", "content": "运行测试", "status": "pending"},
    ]
    expected = f"› {long_content}\n○ 运行测试"
    scripted, logger = set_script(runtime, [
        response(block("tool_use", id="todo-1", name="todo_write", input={"todos": todos})),
        response(block("text", text="done")),
    ])
    messages = []
    result = agent.agent_loop(messages, logger, "run-1")

    assert result["status"] == "completed"
    assert agent.TODO_STATE == todos
    assert messages[1] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "todo-1", "content": expected}],
    }
    assert f"[Tool Result] {expected}" in logger.stream.getvalue()
    assert scripted.calls[1]["messages"][1]["content"][0]["content"] == expected
    completed = next(e for e in read_events(logger) if e["event_type"] == "tool.completed")
    assert completed["data"]["display"] != expected
    assert completed["data"]["display"].endswith("...")
    assert completed["data"]["result"]["truncated"] is True
    assert long_content not in logger.log_path.read_text()


def test_other_tool_success_does_not_change_todo_state(runtime):
    original = [{"id": "1", "content": "读取文件", "status": "in_progress"}]
    agent.TODO_STATE = [dict(todo) for todo in original]
    (Path(agent.WORKSPACE_ROOT) / "note.txt").write_text("ok", encoding="utf-8")
    _, logger = set_script(runtime, [
        response(block("tool_use", id="read-1", name="read_file", input={"path": "note.txt"})),
        response(block("text", text="done")),
    ])
    agent.agent_loop([], logger, "run-1")
    assert agent.TODO_STATE == original


def test_invalid_todo_update_returns_error_through_normal_runtime_and_keeps_state(runtime):
    original = [{"id": "old", "content": "原计划", "status": "pending"}]
    agent.TODO_STATE = [dict(todo) for todo in original]
    invalid = [
        {"id": "1", "content": "one", "status": "in_progress"},
        {"id": "2", "content": "two", "status": "in_progress"},
    ]
    _, logger = set_script(runtime, [
        response(block("tool_use", id="todo-bad", name="todo_write", input={"todos": invalid})),
        response(block("text", text="rejected")),
    ])
    messages = []
    result = agent.agent_loop(messages, logger, "run-1")

    assert result["final_text"] == "rejected"
    assert agent.TODO_STATE == original
    tool_result = messages[1]["content"][0]
    assert tool_result["tool_use_id"] == "todo-bad"
    assert tool_result["is_error"] is True
    assert tool_result["content"].startswith("INVALID_TOOL_INPUT: ")
    events = [event["event_type"] for event in read_events(logger)]
    assert events == [
        "llm.started", "llm.completed", "tool.started", "permission.allowed",
        "tool.failed", "llm.started", "llm.completed",
    ]


def test_todo_maintenance_reminder_nags_each_round_after_errors_and_denial(
    runtime, monkeypatch
):
    agent.TODO_STATE = [
        {"id": "build", "content": "实现功能", "status": "in_progress"},
        {"id": "verify", "content": "验证功能", "status": "pending"},
    ]
    monkeypatch.setattr(agent, "ASK_RULES", [{
        "tools": ["write_file"],
        "matcher": lambda _args: True,
        "message": "approval needed",
        "describe": lambda args: f"Modify file: {args.get('path')}",
    }])
    scripted, logger = set_script(runtime, [
        response(
            block("tool_use", id="missing-1", name="read_file", input={"path": "missing.txt"}),
            block("tool_use", id="unknown-1", name="future_tool", input={}),
        ),
        response(block("tool_use", id="missing-2", name="read_file", input={"path": "missing.txt"})),
        response(block(
            "tool_use", id="denied-3", name="write_file",
            input={"path": "denied.txt", "content": "no"},
        )),
        response(block("tool_use", id="missing-4", name="read_file", input={"path": "missing.txt"})),
        response(block("tool_use", id="missing-5", name="read_file", input={"path": "missing.txt"})),
        response(block("text", text="done")),
    ])
    messages = []

    result = agent.agent_loop(messages, logger, "run-1", confirmation_fn=lambda _: "n")

    assert result["status"] == "completed"
    first_round_results = messages[1]["content"]
    second_round_results = messages[3]["content"]
    third_round_results = messages[5]["content"]
    fourth_round_results = messages[7]["content"]
    fifth_round_results = messages[9]["content"]
    assert len(first_round_results) == 2
    assert all(item["type"] == "tool_result" for item in first_round_results)
    assert all(item["type"] == "tool_result" for item in second_round_results)
    for results in (third_round_results, fourth_round_results, fifth_round_results):
        assert results[-1] == {"type": "text", "text": agent.TODO_MAINTENANCE_REMINDER}
        assert all(
            agent.TODO_MAINTENANCE_REMINDER not in item["content"]
            for item in results[:-1]
            if item["type"] == "tool_result"
        )
    assert third_round_results[-2]["is_error"] is True
    assert third_round_results[-2]["content"].startswith("PERMISSION_DENIED:")
    assert not agent.WORKSPACE_ROOT.joinpath("denied.txt").exists()


def test_successful_todo_write_resets_nag_until_three_more_tool_rounds(runtime):
    active = [
        {"id": "build", "content": "实现功能", "status": "in_progress"},
        {"id": "verify", "content": "验证功能", "status": "pending"},
    ]
    agent.TODO_STATE = [dict(todo) for todo in active]
    scripted, logger = set_script(runtime, [
        response(block("tool_use", id="read-1", name="read_file", input={"path": "missing.txt"})),
        response(block("tool_use", id="read-2", name="read_file", input={"path": "missing.txt"})),
        response(block("tool_use", id="read-3", name="read_file", input={"path": "missing.txt"})),
        response(block("tool_use", id="todo-4", name="todo_write", input={"todos": active})),
        response(block("tool_use", id="read-5", name="read_file", input={"path": "missing.txt"})),
        response(block("tool_use", id="read-6", name="read_file", input={"path": "missing.txt"})),
        response(block("tool_use", id="read-7", name="read_file", input={"path": "missing.txt"})),
        response(block("text", text="done")),
    ])
    messages = []

    result = agent.agent_loop(messages, logger, "run-1")

    assert result["status"] == "completed"
    reminder_messages = [
        message
        for message in messages
        if message["role"] == "user" and isinstance(message["content"], list)
        if any(item == {"type": "text", "text": agent.TODO_MAINTENANCE_REMINDER}
               for item in message["content"])
    ]
    assert reminder_messages == [messages[5], messages[13]]
    assert messages[7]["content"][-1]["type"] == "tool_result"
    assert all(item["type"] == "tool_result" for item in messages[9]["content"])
    assert all(item["type"] == "tool_result" for item in messages[11]["content"])


def test_failed_todo_write_does_not_reset_maintenance_nag(runtime):
    active = [
        {"id": "build", "content": "实现功能", "status": "in_progress"},
        {"id": "verify", "content": "验证功能", "status": "pending"},
    ]
    invalid = [
        {"id": "build", "content": "实现功能", "status": "in_progress"},
        {"id": "verify", "content": "验证功能", "status": "in_progress"},
    ]
    agent.TODO_STATE = [dict(todo) for todo in active]
    _, logger = set_script(runtime, [
        response(block("tool_use", id="read-1", name="read_file", input={"path": "missing.txt"})),
        response(block("tool_use", id="read-2", name="read_file", input={"path": "missing.txt"})),
        response(block("tool_use", id="todo-bad-3", name="todo_write", input={"todos": invalid})),
        response(block("tool_use", id="read-4", name="read_file", input={"path": "missing.txt"})),
        response(block("text", text="done")),
    ])
    messages = []

    result = agent.agent_loop(messages, logger, "run-1")

    assert result["status"] == "completed"
    for message in (messages[5], messages[7]):
        assert message["content"][-1] == {
            "type": "text",
            "text": agent.TODO_MAINTENANCE_REMINDER,
        }
    assert messages[5]["content"][-2]["is_error"] is True
    assert agent.TODO_STATE == active


def test_completed_todos_do_not_trigger_maintenance_reminder(runtime):
    agent.TODO_STATE = [{"id": "done", "content": "已完成", "status": "completed"}]
    _, logger = set_script(runtime, [
        response(block("tool_use", id=f"read-{index}", name="read_file", input={"path": "missing.txt"}))
        for index in range(1, 5)
    ] + [response(block("text", text="done"))])
    messages = []

    agent.agent_loop(messages, logger, "run-1")

    reminder_blocks = [
        item
        for message in messages
        if message["role"] == "user" and isinstance(message["content"], list)
        for item in message["content"]
        if item["type"] == "text" and item["text"] == agent.TODO_MAINTENANCE_REMINDER
    ]
    assert reminder_blocks == []


def test_default_permission_rules_use_tools_then_input_matcher(monkeypatch):
    assert agent.check_permission("bash", {"command": "rm -rf /"})["action"] == "deny"
    assert agent.check_permission("bash", {"command": "rm -f demo.txt"})["action"] == "ask"
    assert agent.check_permission("bash", {"command": "pwd"})["action"] == "allow"
    assert agent.check_permission("write_file", {"path": "note.txt"})["action"] == "allow"
    assert agent.check_permission("write_file", {"path": ".env"})["action"] == "ask"
    assert agent.check_permission("edit_file", {"path": "../outside.txt"})["action"] == "deny"
    assert agent.check_permission("read_file", {"path": "note.txt"})["action"] == "allow"

    deny_rule = {
        "tools": ["bash"],
        "matcher": lambda args: agent.contains_destructive_command(args.get("command", "")),
        "message": "Potentially destructive command",
        "describe": lambda args: f"Blocked command: {args.get('command')}",
    }
    ask_rule = {
        "tools": ["write_file", "edit_file"],
        "matcher": lambda args: True,
        "message": "File modification requires approval",
        "describe": lambda args: f"Modify file: {args.get('path')}",
    }
    monkeypatch.setattr(agent, "DENY_RULES", [deny_rule])
    monkeypatch.setattr(agent, "ASK_RULES", [ask_rule])

    assert agent.check_permission("bash", {"command": "rm -rf /"})["action"] == "deny"
    assert agent.check_permission("bash", {"command": "pwd"})["action"] == "allow"
    assert agent.check_permission("write_file", {"path": "note.txt"})["action"] == "ask"
    assert agent.check_permission("read_file", {"path": "note.txt"})["action"] == "allow"


@pytest.mark.parametrize("command", [
    "rm -rf /",
    "sudo apt update",
    "shutdown -h now",
    "reboot",
    "mkfs.ext4 /dev/disk9",
    "dd if=image.iso of=disk.img",
    "printf x > /dev/disk9",
    "git reset --hard HEAD~1",
    "git clean -fd",
])
def test_bash_policy_denies_explicit_catastrophic_commands(command):
    assert agent.check_permission("bash", {"command": command})["action"] == "deny"


@pytest.mark.parametrize("command", [
    "rm -f build.tmp",
    "mv old.txt new.txt",
    "cp source.txt target.txt",
    "mkdir build",
    "touch marker.txt",
    "chmod +x script.sh",
    "pip install requests",
    "python3.12 -m pip uninstall requests",
    "npm install lodash",
    "npm uninstall lodash",
    "git checkout feature",
    "git switch main",
    "git restore app.py",
    "git reset HEAD app.py",
    "git clean -n",
    "git commit -m test",
    "printf hello > output.txt",
])
def test_bash_policy_asks_for_side_effect_commands(command):
    assert agent.check_permission("bash", {"command": command})["action"] == "ask"


@pytest.mark.parametrize("command", [
    "ls -la",
    "pwd",
    "cat README.md",
    "head -20 README.md",
    "tail -20 README.md",
    "find . -name '*.py'",
    "grep -n Permission README.md",
    "git status --short",
    "git diff --check",
    "python3.12 -m pytest -q",
    "ls missing 2>/dev/null",
    'ls missing 2>/dev/null; echo "done"',
    'cat README.md 2>&1; echo "done"',
])
def test_bash_policy_allows_read_only_and_validation_commands(command):
    assert agent.check_permission("bash", {"command": command})["action"] == "allow"


@pytest.mark.parametrize("tool_name", ["write_file", "edit_file"])
@pytest.mark.parametrize("path", ["../outside.txt", "../secrets/outside.txt", "/tmp/outside.txt"])
def test_file_permission_denies_paths_outside_workspace(monkeypatch, tmp_path, tool_name, path):
    monkeypatch.setattr(agent, "WORKSPACE_ROOT", tmp_path)
    result = agent.check_permission(tool_name, {"path": path})
    assert result["action"] == "deny"
    assert result["message"] == "File path is outside the workspace"


@pytest.mark.parametrize("tool_name", ["write_file", "edit_file"])
@pytest.mark.parametrize("path", [
    ".env",
    ".env.local",
    "config/credentials.json",
    "secrets/api.txt",
    "config/secret-key.txt",
])
def test_file_permission_asks_for_sensitive_workspace_paths(monkeypatch, tmp_path, tool_name, path):
    monkeypatch.setattr(agent, "WORKSPACE_ROOT", tmp_path)
    assert agent.check_permission(tool_name, {"path": path})["action"] == "ask"


@pytest.mark.parametrize("tool_name", ["write_file", "edit_file"])
@pytest.mark.parametrize("path", ["README.md", "src/app.py", "demo_workspace/note.txt"])
def test_file_permission_allows_normal_workspace_paths(monkeypatch, tmp_path, tool_name, path):
    monkeypatch.setattr(agent, "WORKSPACE_ROOT", tmp_path)
    assert agent.check_permission(tool_name, {"path": path})["action"] == "allow"


def test_file_permission_denies_symlink_escape(monkeypatch, tmp_path):
    outside = tmp_path.parent / "outside-v03-permission.txt"
    outside.write_text("outside", encoding="utf-8")
    (tmp_path / "link").symlink_to(outside.parent, target_is_directory=True)
    monkeypatch.setattr(agent, "WORKSPACE_ROOT", tmp_path)
    assert agent.check_permission("write_file", {"path": "link/new.txt"})["action"] == "deny"


def test_sensitive_write_rejected_never_executes(runtime, monkeypatch):
    monkeypatch.setattr(agent, "ASK_RULES", [{
        "tools": ["write_file", "edit_file"],
        "matcher": agent.path_is_sensitive,
        "message": "sensitive write requires approval",
        "describe": lambda args: f"Modify sensitive path: {args.get('path')}",
    }])
    _, logger = set_script(runtime, [
        response(block("tool_use", id="sensitive-write", name="write_file", input={"path": "credentials.json", "content": "secret"})),
        response(block("text", text="denied")),
    ])

    agent.agent_loop([], logger, "run-1", confirmation_fn=lambda _: "n")

    assert not agent.WORKSPACE_ROOT.joinpath("credentials.json").exists()


def test_normal_workspace_write_executes_without_confirmation(runtime, monkeypatch):
    monkeypatch.setattr(agent, "DENY_RULES", [{
        "tools": ["write_file", "edit_file"],
        "matcher": agent.path_is_outside_workspace,
        "message": "outside workspace",
        "describe": lambda args: f"Blocked path: {args.get('path')}",
    }])
    monkeypatch.setattr(agent, "ASK_RULES", [{
        "tools": ["write_file", "edit_file"],
        "matcher": agent.path_is_sensitive,
        "message": "sensitive write requires approval",
        "describe": lambda args: f"Modify sensitive path: {args.get('path')}",
    }])
    _, logger = set_script(runtime, [
        response(block("tool_use", id="normal-write", name="write_file", input={"path": "src/note.txt", "content": "ok"})),
        response(block("text", text="done")),
    ])

    def unexpected_confirmation(_):
        raise AssertionError("normal workspace write must not ask")

    agent.agent_loop([], logger, "run-1", confirmation_fn=unexpected_confirmation)

    assert agent.WORKSPACE_ROOT.joinpath("src/note.txt").read_text(encoding="utf-8") == "ok"


def test_bash_deny_wins_when_command_also_matches_ask():
    result = agent.check_permission("bash", {"command": "git reset --hard HEAD~1"})
    assert result["action"] == "deny"
    assert result["message"] == "Potentially destructive command"


def test_rejected_bash_mutation_never_executes(runtime, monkeypatch):
    monkeypatch.setattr(agent, "ASK_RULES", [{
        "tools": ["bash"],
        "matcher": lambda args: agent.contains_side_effect_command(args.get("command", "")),
        "message": "side effect requires approval",
        "describe": lambda args: f"Execute command: {args.get('command')}",
    }])
    _, logger = set_script(runtime, [
        response(block("tool_use", id="bash-ask", name="bash", input={"command": "touch denied.txt"})),
        response(block("text", text="denied")),
    ])

    agent.agent_loop([], logger, "run-1", confirmation_fn=lambda _: "n")

    assert not agent.WORKSPACE_ROOT.joinpath("denied.txt").exists()
    failed = next(event for event in read_events(logger) if event["event_type"] == "tool.failed")
    assert failed["data"]["error_code"] == "PERMISSION_DENIED"


def test_read_only_bash_executes_without_confirmation(runtime, monkeypatch):
    monkeypatch.setattr(agent, "ASK_RULES", [{
        "tools": ["bash"],
        "matcher": lambda args: agent.contains_side_effect_command(args.get("command", "")),
        "message": "side effect requires approval",
        "describe": lambda args: f"Execute command: {args.get('command')}",
    }])
    _, logger = set_script(runtime, [
        response(block("tool_use", id="bash-allow", name="bash", input={"command": "pwd"})),
        response(block("text", text="done")),
    ])

    def unexpected_confirmation(_):
        raise AssertionError("read-only command must not ask")

    agent.agent_loop([], logger, "run-1", confirmation_fn=unexpected_confirmation)

    events = read_events(logger)
    assert any(event["event_type"] == "permission.allowed" for event in events)
    assert any(event["event_type"] == "tool.completed" for event in events)


def test_tool_outside_rule_scope_does_not_call_matcher(monkeypatch):
    calls = []
    rule = {
        "tools": ["bash"],
        "matcher": lambda args: calls.append(args) or True,
        "message": "blocked",
        "describe": lambda args: "blocked",
    }
    monkeypatch.setattr(agent, "DENY_RULES", [rule])
    assert agent.check_permission("read_file", {"path": "x"})["action"] == "allow"
    assert calls == []


def test_deny_has_priority_over_ask_and_never_executes(runtime, monkeypatch):
    called = []
    rule = {
        "tools": ["custom"],
        "matcher": lambda args: True,
        "message": "hard deny",
        "describe": lambda args: "Blocked custom operation",
    }
    monkeypatch.setattr(agent, "DENY_RULES", [rule])
    monkeypatch.setattr(agent, "ASK_RULES", [{**rule, "message": "would ask"}])
    monkeypatch.setitem(agent.TOOL_HANDLERS, "custom", lambda args: called.append(args) or ("ran", False, None))
    scripted, logger = set_script(runtime, [
        response(block("tool_use", id="deny-1", name="custom", input={})),
        response(block("text", text="denied safely")),
    ])
    confirmations = []
    messages = []

    result = agent.agent_loop(messages, logger, "run-1", confirmation_fn=lambda prompt: confirmations.append(prompt) or "y")

    assert result["final_text"] == "denied safely"
    assert called == []
    assert confirmations == []
    assert messages[1]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "deny-1",
        "content": "PERMISSION_DENIED: hard deny\nOperation: Blocked custom operation",
        "is_error": True,
    }
    assert "[Permission Denied]\nBlocked custom operation\nReason:\nhard deny" in logger.stream.getvalue()
    assert [event["event_type"] for event in read_events(logger) if event["event_type"].startswith("permission.")] == ["permission.denied"]
    assert len(scripted.calls) == 2


def test_ask_approved_executes_handler(runtime, monkeypatch):
    called = []
    rule = {
        "tools": ["custom"],
        "matcher": lambda args: True,
        "message": "approval needed",
        "describe": lambda args: f"Run custom: {args['value']}",
    }
    monkeypatch.setattr(agent, "ASK_RULES", [rule])
    monkeypatch.setitem(agent.TOOL_HANDLERS, "custom", lambda args: called.append(args) or ("approved result", False, None))
    _, logger = set_script(runtime, [
        response(block("tool_use", id="ask-1", name="custom", input={"value": 7})),
        response(block("text", text="done")),
    ])
    messages = []

    agent.agent_loop(messages, logger, "run-1", confirmation_fn=lambda _: "y")

    assert called == [{"value": 7}]
    assert messages[1]["content"][0]["content"] == "approved result"
    shown = logger.stream.getvalue()
    assert "[Permission Required]\nRun custom: 7\nReason:\napproval needed" in shown
    assert "Allow? (y/N) " in shown
    assert [event["event_type"] for event in read_events(logger) if event["event_type"].startswith("permission.")] == ["permission.required", "permission.allowed"]


@pytest.mark.parametrize("refusal", ["n", "", "anything", EOFError()])
def test_ask_refused_cancelled_or_eof_never_executes(runtime, monkeypatch, refusal):
    called = []
    rule = {
        "tools": ["custom"],
        "matcher": lambda args: True,
        "message": "approval needed",
        "describe": lambda args: "Run custom operation",
    }
    monkeypatch.setattr(agent, "ASK_RULES", [rule])
    monkeypatch.setitem(agent.TOOL_HANDLERS, "custom", lambda args: called.append(args) or ("ran", False, None))
    _, logger = set_script(runtime, [
        response(block("tool_use", id="ask-2", name="custom", input={})),
        response(block("text", text="handled refusal")),
    ])
    messages = []

    def confirm(_):
        if isinstance(refusal, BaseException):
            raise refusal
        return refusal

    agent.agent_loop(messages, logger, "run-1", confirmation_fn=confirm)

    assert called == []
    assert messages[1]["content"][0]["is_error"] is True
    assert messages[1]["content"][0]["content"] == (
        "PERMISSION_DENIED: User denied permission: approval needed\n"
        "Operation: Run custom operation"
    )
    assert "[Permission Denied]" in logger.stream.getvalue()


def test_rule_failure_fails_closed(monkeypatch):
    def broken_matcher(_args):
        raise RuntimeError("matcher broke")

    monkeypatch.setattr(agent, "ASK_RULES", [{
        "tools": ["custom"],
        "matcher": broken_matcher,
        "message": "unused",
        "describe": lambda args: "unused",
    }])
    result = agent.check_permission("custom", {})
    assert result["action"] == "deny"
    assert result["message"] == "Permission rule evaluation failed: matcher broke"


def test_permission_pipeline_has_no_tool_specific_branches():
    import inspect

    source = inspect.getsource(agent.check_permission)
    assert 'tool_name == "bash"' not in source
    assert 'tool_name == "write_file"' not in source
    assert "tool_name not in rule[\"tools\"]" in source


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


@pytest.mark.parametrize(
    "name,tool_input,confirmation,expected_error",
    [
        ("write_file", {"path": "ok.txt", "content": "ok"}, "y", None),
        ("read_file", {"path": "missing.txt"}, "y", "FILE_NOT_FOUND"),
        ("missing", {}, "y", "UNKNOWN_TOOL"),
    ],
)
def test_post_tool_use_runs_once_for_success_tool_error_and_unknown_tool(
    runtime, name, tool_input, confirmation, expected_error
):
    seen = []
    agent.register_hook("PostToolUse", lambda context: seen.append(dict(context)))
    _, logger = set_script(runtime, [
        response(block("tool_use", id="tool-1", name=name, input=tool_input)),
        response(block("text", text="done")),
    ])

    agent.agent_loop([], logger, "run-1", confirmation_fn=lambda _: confirmation)

    assert len(seen) == 1
    assert seen[0]["tool_name"] == name
    assert seen[0]["tool_use_id"] == "tool-1"
    assert seen[0]["tool_result"][2] == expected_error
    assert seen[0]["duration_seconds"] >= 0


def test_permission_denial_reaches_post_tool_use_once_without_handler(runtime, monkeypatch):
    called = []
    monkeypatch.setattr(agent, "DENY_RULES", [{
        "tools": ["custom"],
        "matcher": lambda args: True,
        "message": "blocked",
        "describe": lambda args: "Blocked custom operation",
    }])
    monkeypatch.setitem(
        agent.TOOL_HANDLERS,
        "custom",
        lambda args: called.append(args) or ("ran", False, None),
    )
    seen = []
    agent.register_hook("PostToolUse", lambda context: seen.append(context["tool_result"]))
    _, logger = set_script(runtime, [
        response(block("tool_use", id="denied", name="custom", input={})),
        response(block("text", text="done")),
    ])

    agent.agent_loop([], logger, "run-1")

    assert called == []
    assert len(seen) == 1
    assert seen[0][2] == "PERMISSION_DENIED"


def test_before_hook_exception_fails_closed_and_never_executes(runtime, monkeypatch):
    called = []
    monkeypatch.setitem(
        agent.TOOL_HANDLERS,
        "custom",
        lambda args: called.append(args) or ("ran", False, None),
    )

    def broken(_context):
        raise RuntimeError("before broke")

    agent.register_hook("PreToolUse", broken)
    _, logger = set_script(runtime, [
        response(block("tool_use", id="broken-before", name="custom", input={})),
        response(block("text", text="recovered")),
    ])
    messages = []

    result = agent.agent_loop(messages, logger, "run-1")

    assert result["final_text"] == "recovered"
    assert called == []
    assert messages[1]["content"][0]["is_error"] is True
    assert messages[1]["content"][0]["content"] == (
        "HOOK_ERROR: PreToolUse hook failed: before broke"
    )
    failed_events = [
        event for event in read_events(logger) if event["event_type"] == "tool.failed"
    ]
    assert len(failed_events) == 1
    assert failed_events[0]["data"]["error_code"] == "HOOK_ERROR"


def test_after_hook_exception_does_not_repeat_handler_or_change_real_result(
    runtime, monkeypatch, capsys
):
    called = []
    monkeypatch.setitem(
        agent.TOOL_HANDLERS,
        "custom",
        lambda args: called.append(args) or ("real result", False, None),
    )

    def broken(_context):
        raise RuntimeError("after broke")

    agent.register_hook("PostToolUse", broken)
    _, logger = set_script(runtime, [
        response(block("tool_use", id="broken-after", name="custom", input={})),
        response(block("text", text="done")),
    ])
    messages = []

    agent.agent_loop(messages, logger, "run-1")

    assert called == [{}]
    assert messages[1]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "broken-after",
        "content": "real result",
    }
    assert "[Hook Warning] PostToolUse hook failed: after broke" in capsys.readouterr().err
    completed_events = [
        event for event in read_events(logger) if event["event_type"] == "tool.completed"
    ]
    assert len(completed_events) == 1


def test_agent_loop_only_declares_hook_lifecycle():
    import inspect

    source = inspect.getsource(agent.agent_loop)
    assert 'trigger_hooks("PreToolUse", hook_context)' in source
    assert 'trigger_hooks("PostToolUse", hook_context)' in source
    assert "check_permission(" not in source
    assert "_authorize_tool(" not in source
    assert "_tool_call_summary(" not in source
    assert "_tool_result_summary(" not in source


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
    assert agent.cli(lambda _: answer, output, state_dir=tmp_path / "state") == 0
    log_file = next(path for path in tmp_path.iterdir() if path.suffix == ".jsonl")
    events = [json.loads(line) for line in log_file.read_text().splitlines()]
    assert [e["event_type"] for e in events] == [
        "session.started", "checkpoint.saved", "session.ended"
    ]


def test_cli_eof_and_keyboard_interrupt(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "configure_model", lambda: None)
    monkeypatch.setattr(agent, "LOG_DIR", tmp_path)
    monkeypatch.setattr(agent, "client", SimpleNamespace(messages=ScriptedMessages([])))
    monkeypatch.setattr(agent, "MODEL", "fake-model")
    for exc in (EOFError(), KeyboardInterrupt()):
        output = io.StringIO()
        def interrupted(_prompt, error=exc):
            raise error
        assert agent.cli(interrupted, output, state_dir=tmp_path / "state") == 0


def test_missing_configuration_is_clear(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(agent, "ROOT_DIR", tmp_path)
    called = []
    state_dir = tmp_path / "state"
    assert agent.cli(lambda prompt: called.append(prompt) or "exit", state_dir=state_dir) == 2
    error = capsys.readouterr().err
    assert "CONFIG_ERROR" in error
    assert "API_KEY=" not in error
    assert called == []
    assert not state_dir.exists()


class FakeAPIError(Exception):
    def __init__(self, message, status_code=429, headers=None):
        super().__init__(message)
        self.status_code = status_code
        self.response = SimpleNamespace(headers=headers or {})


def durable_runtime(runtime, tmp_path):
    _, logger = runtime
    state = agent.new_runtime_state(logger.session_id)
    state_path = tmp_path / "state" / f"{logger.session_id}.json"
    state["messages"] = []
    return state, state_path, logger


def test_state_save_restore_consistency_including_sdk_blocks(tmp_path):
    state = agent.new_runtime_state("session-state")
    state["messages"] = [
        {"role": "user", "content": "remember STATE-42"},
        {"role": "assistant", "content": [block("text", text="remembered")]},
    ]
    state["todos"] = [{"id": "read", "content": "读取 README", "status": "in_progress"}]
    state["current_run"] = {"run_id": "run-state", "status": "running", "round": 7}
    state["interruption_info"] = {"phase": "llm_call"}
    path = tmp_path / "nested" / "state.json"

    agent.save_state(state, path)
    restored = agent.load_state(path)

    assert restored["session_id"] == "session-state"
    assert restored["messages"][1]["content"][0] == {"type": "text", "text": "remembered"}
    assert restored["todos"] == state["todos"]
    assert restored["current_run"] == state["current_run"]
    assert restored["interruption_info"] == {"phase": "llm_call"}
    assert restored["updated_at"]


def test_atomic_write_failure_preserves_previous_state(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    original = agent.new_runtime_state("original")
    agent.save_state(original, path)
    original_bytes = path.read_bytes()
    replacement = agent.new_runtime_state("replacement")

    def fail_replace(_source, _target):
        raise OSError("replace failed")

    monkeypatch.setattr(agent.os, "replace", fail_replace)
    with pytest.raises(agent.StateSaveError, match="replace failed"):
        agent.save_state(replacement, path)

    assert path.read_bytes() == original_bytes
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob(".*.tmp")) == []


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        '{"schema_version": 1}',
        json.dumps({**agent.new_runtime_state("s"), "schema_version": 999}),
        json.dumps({**agent.new_runtime_state("s"), "current_run": {"run_id": "r", "status": "unknown", "round": 1}}),
        json.dumps({**agent.new_runtime_state("s"), "messages": [{"role": "bad", "content": "x"}]}),
    ],
)
def test_corrupted_truncated_incompatible_or_invalid_state_fails(raw, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(raw)
    before = path.read_bytes()
    with pytest.raises(agent.StateLoadError):
        agent.load_state(path)
    assert path.read_bytes() == before


def test_explicit_resume_load_failure_precedes_model_configuration(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "bad.json"
    path.write_text("{truncated")
    before = path.read_bytes()
    called = []
    monkeypatch.setattr(agent, "ROOT_DIR", tmp_path / "missing-config")
    monkeypatch.setattr(agent, "LOG_DIR", tmp_path / "logs")
    output = io.StringIO()

    code = agent.cli(lambda prompt: called.append(prompt) or "y", output, resume_path=path)

    assert code == 3
    assert "[State Load Error]" in output.getvalue()
    assert "CONFIG_ERROR" not in capsys.readouterr().err
    assert called == []
    assert path.read_bytes() == before
    events = [json.loads(line) for line in (tmp_path / "logs" / "state-load-failures.jsonl").read_text().splitlines()]
    assert events[-1]["event_type"] == "state.load_failed"


def test_valid_resume_state_then_missing_configuration_returns_config_error(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "valid.json"
    state = agent.new_runtime_state("resume-session")
    agent.save_state(state, path)
    before = path.read_bytes()
    called = []
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(agent, "ROOT_DIR", tmp_path / "missing-config")
    monkeypatch.setattr(agent, "LOG_DIR", log_dir)

    code = agent.cli(
        lambda prompt: called.append(prompt) or "exit",
        io.StringIO(),
        resume_path=path,
    )

    assert code == 2
    assert "CONFIG_ERROR" in capsys.readouterr().err
    assert called == []
    assert path.read_bytes() == before
    assert not (log_dir / "state-load-failures.jsonl").exists()


def test_retry_constants_and_backoff_hard_cap(monkeypatch):
    assert (agent.MAX_RETRIES, agent.BASE_DELAY, agent.MAX_DELAY, agent.JITTER_RATIO) == (4, 1.0, 16.0, 0.25)
    monkeypatch.setattr(agent.random, "uniform", lambda low, high: high)
    assert [agent.retry_delay(number) for number in range(1, 5)] == [1.25, 2.5, 5.0, 10.0]
    assert agent.retry_delay(5) == 16.0
    assert agent.retry_delay(1, retry_after=99) == 16.0


def test_retry_after_parses_seconds_and_http_date(monkeypatch):
    assert agent._retry_after_seconds(FakeAPIError("busy", headers={"Retry-After": "3.5"})) == 3.5
    assert agent._retry_after_seconds(FakeAPIError("busy", headers={"Retry-After": "invalid"})) is None


def test_transient_retry_stays_in_same_round_and_logs_attempts(runtime, monkeypatch):
    scripted, logger = set_script(runtime, [
        FakeAPIError("model_concurrency_rate_limit_exceeded", headers={}),
        FakeAPIError("temporary", status_code=503, headers={}),
        response(block("text", text="recovered")),
    ])
    monkeypatch.setattr(agent.random, "uniform", lambda low, high: 0.0)
    delays = []

    result = agent.agent_loop([], logger, "run-retry", sleep_fn=delays.append)

    assert result == {"status": "completed", "final_text": "recovered", "rounds": 1, "error": None}
    assert len(scripted.calls) == 3
    assert delays == [1.0, 2.0]
    events = read_events(logger)
    started = [event for event in events if event["event_type"] == "llm.started"]
    assert [(e["data"]["round"], e["data"]["attempt"]) for e in started] == [(1, 1), (1, 2), (1, 3)]
    retries = [event for event in events if event["event_type"] == "llm.retry"]
    assert [event["data"]["attempt"] for event in retries] == [2, 3]
    assert all(event["data"]["delay_source"] == "backoff" for event in retries)


def test_retry_after_is_preferred_but_capped(runtime):
    _, logger = set_script(runtime, [
        FakeAPIError("busy", headers={"Retry-After": "99"}),
        response(block("text", text="ok")),
    ])
    delays = []
    result = agent.agent_loop([], logger, "run-retry-after", sleep_fn=delays.append)
    assert result["status"] == "completed"
    assert delays == [16.0]
    retry = next(event for event in read_events(logger) if event["event_type"] == "llm.retry")
    assert retry["data"]["delay_source"] == "retry_after"
    assert retry["data"]["delay_seconds"] == 16.0


def test_retry_exhaustion_fails_current_run_after_five_attempts(runtime, tmp_path):
    scripted, logger = set_script(runtime, [FakeAPIError("busy") for _ in range(5)])
    state, state_path, _ = durable_runtime(runtime, tmp_path)
    delays = []

    result = agent.run_once(
        state["messages"],
        "do work",
        logger,
        runtime_state=state,
        state_path=state_path,
        sleep_fn=delays.append,
    )

    assert len(scripted.calls) == 5
    assert len(delays) == 4
    assert result["error"] == "RETRY_EXHAUSTED"
    assert state["current_run"]["status"] == "failed"
    assert agent.load_state(state_path)["current_run"]["status"] == "failed"
    assert any(event["event_type"] == "llm.retry_exhausted" for event in read_events(logger))


def test_tool_and_user_errors_never_trigger_api_retry(runtime):
    scripted, logger = set_script(runtime, [
        response(block("tool_use", id="missing", name="read_file", input={"path": "missing.txt"})),
        response(block("text", text="reported")),
    ])
    result = agent.agent_loop([], logger, "run-tool-error", sleep_fn=lambda _: pytest.fail("retry sleep"))
    assert result["status"] == "completed"
    assert len(scripted.calls) == 2
    assert not any(event["event_type"] == "llm.retry" for event in read_events(logger))


@pytest.mark.parametrize("status", agent.RUN_STATUSES)
def test_all_current_run_status_values_round_trip(status, tmp_path):
    state = agent.new_runtime_state("session")
    state["current_run"] = {"run_id": "run", "status": status, "round": 3}
    path = tmp_path / f"{status}.json"
    agent.save_state(state, path)
    assert agent.load_state(path)["current_run"]["status"] == status


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_completed_and_failed_runs_do_not_auto_resume(status, runtime, tmp_path):
    scripted, logger = runtime
    state = agent.new_runtime_state("session-1")
    state["current_run"] = {"run_id": "old", "status": status, "round": 2}
    assert agent.resume_run(state, tmp_path / "state.json", logger) is None
    assert scripted.calls == []
    assert state["current_run"]["status"] == status


@pytest.mark.parametrize("status", ["running", "interrupted"])
def test_running_and_interrupted_restore_same_run_and_round(status, runtime, tmp_path):
    scripted, logger = set_script(runtime, [response(block("text", text="continued"))])
    state = agent.new_runtime_state("session-1")
    state["messages"] = [{"role": "user", "content": "original task"}]
    state["current_run"] = {"run_id": "same-run", "status": status, "round": 6}
    state["interruption_info"] = {"phase": "llm_call"}
    path = tmp_path / "state.json"
    agent.save_state(state, path)

    result = agent.resume_run(state, path, logger, sleep_fn=lambda _: None)

    assert result["run_id"] == "same-run"
    assert result["rounds"] == 6
    assert scripted.calls[0]["messages"] is state["messages"]
    started = next(event for event in read_events(logger) if event["event_type"] == "llm.started")
    assert started["data"]["round"] == 6


def test_keyboard_interrupt_persists_interrupted_run_and_phase(tmp_path, monkeypatch):
    class InterruptingMessages:
        def create(self, **_kwargs):
            raise KeyboardInterrupt

    monkeypatch.setattr(agent, "configure_model", lambda: None)
    monkeypatch.setattr(agent, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(agent, "client", SimpleNamespace(messages=InterruptingMessages()))
    monkeypatch.setattr(agent, "MODEL", "fake-model")
    inputs = iter(["do work"])
    output = io.StringIO()

    assert agent.cli(lambda _: next(inputs), output, state_dir=tmp_path / "state") == 0

    state_path = next((tmp_path / "state").glob("*.json"))
    restored = agent.load_state(state_path)
    assert restored["current_run"]["status"] == "interrupted"
    assert restored["current_run"]["round"] == 1
    assert restored["interruption_info"] == {"phase": "llm_call"}
    events = [json.loads(line) for line in next((tmp_path / "logs").glob("*.jsonl")).read_text().splitlines()]
    assert any(event["event_type"] == "run.interrupted" for event in events)


def recovery_state(phase, messages=None):
    state = agent.new_runtime_state("session-recovery")
    state["messages"] = list(messages or [{"role": "user", "content": "original task"}])
    state["current_run"] = {"run_id": "run-recovery", "status": "interrupted", "round": 4}
    state["interruption_info"] = {"phase": phase}
    if phase != "llm_call":
        state["interruption_info"].update({"tool_name": "write_file", "tool_use_id": "pending-1"})
    return state


def test_llm_call_recovery_uses_only_independent_common_text_block():
    state = recovery_state("llm_call")

    agent.prepare_recovery_messages(state)

    recovery = state["messages"][-1]
    assert recovery == {
        "role": "user",
        "content": [{"type": "text", "text": agent.COMMON_RECOVERY_CONTEXT}],
    }
    text = recovery["content"][0]["text"]
    assert "Inspect only" not in text
    assert "reconcile" not in text.lower()
    assert "permission" not in text.lower()


@pytest.mark.parametrize("phase", ["tool_execution", "tool_result_recording"])
def test_uncertain_tool_recovery_appends_targeted_context_without_breaking_tool_results(phase):
    recorded_results = [
        {"type": "tool_result", "tool_use_id": "recorded-1", "content": "already recorded"}
    ]
    state = recovery_state(
        phase,
        [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "recorded-1", "name": "read_file", "input": {"path": "done.txt"}}]},
            {"role": "user", "content": recorded_results},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "pending-1", "name": "write_file", "input": {"path": "effect.txt", "content": "once"}}]},
        ],
    )

    agent.prepare_recovery_messages(state)

    assert state["messages"][1]["content"] == recorded_results
    assert all(block["type"] == "tool_result" for block in state["messages"][1]["content"])
    assert state["messages"][-1] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "pending-1",
                "content": agent.INTERRUPTED_TOOL_OUTCOME_UNKNOWN,
                "is_error": True,
            },
            {"type": "text", "text": agent.COMMON_RECOVERY_CONTEXT},
            {"type": "text", "text": agent.UNCERTAIN_TOOL_RECOVERY_CONTEXT},
        ],
    }
    assert any(
        block.get("type") == "tool_use" and block.get("id") == "pending-1"
        for message in state["messages"]
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if isinstance(block, dict)
    )


def test_permission_wait_recovery_appends_permission_specific_text_block():
    state = recovery_state("permission_wait")

    agent.prepare_recovery_messages(state)

    assert state["messages"][-1] == {
        "role": "user",
        "content": [
            {"type": "text", "text": agent.COMMON_RECOVERY_CONTEXT},
            {"type": "text", "text": agent.PERMISSION_RECOVERY_CONTEXT},
        ],
    }
    assert "permission decision" in state["messages"][-1]["content"][1]["text"]
    assert "Inspect only" not in state["messages"][-1]["content"][1]["text"]


def test_uncertain_tool_side_effect_is_not_replayed_on_resume(runtime, tmp_path, monkeypatch):
    scripted, logger = set_script(runtime, [
        response(block("tool_use", id="write-1", name="write_file", input={"path": "effect.txt", "content": "once"})),
        response(block("text", text="inspected and continued")),
    ])
    state, state_path, _ = durable_runtime(runtime, tmp_path)
    original_checkpoint = agent.checkpoint

    def interrupt_before_result_record(state_arg, path_arg, logger_arg, reason):
        if reason == "interruption.set:tool_result_recording":
            raise KeyboardInterrupt
        return original_checkpoint(state_arg, path_arg, logger_arg, reason)

    monkeypatch.setattr(agent, "checkpoint", interrupt_before_result_record)
    with pytest.raises(KeyboardInterrupt):
        agent.run_once(state["messages"], "write once", logger, runtime_state=state, state_path=state_path)
    assert (tmp_path / "effect.txt").read_text() == "once"
    persisted = agent.load_state(state_path)
    persisted["current_run"]["status"] = "interrupted"
    original_checkpoint(persisted, state_path, logger, "run.interrupted")

    monkeypatch.setattr(agent, "checkpoint", original_checkpoint)
    result = agent.resume_run(persisted, state_path, logger, sleep_fn=lambda _: None)

    assert result["status"] == "completed"
    assert len(scripted.calls) == 2
    assert (tmp_path / "effect.txt").read_text() == "once"
    recovery = persisted["messages"][-2]["content"]
    assert recovery == [
        {
            "type": "tool_result",
            "tool_use_id": "write-1",
            "content": agent.INTERRUPTED_TOOL_OUTCOME_UNKNOWN,
            "is_error": True,
        },
        {"type": "text", "text": agent.COMMON_RECOVERY_CONTEXT},
        {"type": "text", "text": agent.UNCERTAIN_TOOL_RECOVERY_CONTEXT},
    ]
    assert any(
        isinstance(message.get("content"), list)
        and any(_value.get("id") == "write-1" for _value in message["content"] if isinstance(_value, dict))
        for message in scripted.calls[1]["messages"]
    )


def test_multi_tool_interruption_preserves_completed_result_and_closes_batch_safely(
    runtime, tmp_path, monkeypatch
):
    scripted, logger = set_script(runtime, [
        response(
            block("tool_use", id="tool-A", name="write_file", input={"path": "a.txt", "content": "A"}),
            block("tool_use", id="tool-B", name="write_file", input={"path": "b.txt", "content": "B"}),
            block("tool_use", id="tool-C", name="write_file", input={"path": "c.txt", "content": "C"}),
        ),
        response(block("text", text="reconciled and continued")),
    ])
    state, state_path, _ = durable_runtime(runtime, tmp_path)
    original_checkpoint = agent.checkpoint

    def interrupt_before_b_result(state_arg, path_arg, logger_arg, reason):
        info = state_arg.get("interruption_info") or {}
        if reason == "interruption.set:tool_result_recording" and info.get("tool_use_id") == "tool-B":
            raise KeyboardInterrupt
        return original_checkpoint(state_arg, path_arg, logger_arg, reason)

    monkeypatch.setattr(agent, "checkpoint", interrupt_before_b_result)
    with pytest.raises(KeyboardInterrupt):
        agent.run_once(state["messages"], "write A, B, and C", logger, runtime_state=state, state_path=state_path)

    assert (tmp_path / "a.txt").read_text() == "A"
    assert (tmp_path / "b.txt").read_text() == "B"
    assert not (tmp_path / "c.txt").exists()
    persisted = agent.load_state(state_path)
    partial_results = persisted["messages"][-1]["content"]
    assert [item["tool_use_id"] for item in partial_results] == ["tool-A"]
    persisted["current_run"]["status"] = "interrupted"
    original_checkpoint(persisted, state_path, logger, "run.interrupted")

    monkeypatch.setattr(agent, "checkpoint", original_checkpoint)
    result = agent.resume_run(persisted, state_path, logger, sleep_fn=lambda _: None)

    assert result["status"] == "completed"
    assert len(scripted.calls) == 2
    assert (tmp_path / "a.txt").read_text() == "A"
    assert (tmp_path / "b.txt").read_text() == "B"
    assert not (tmp_path / "c.txt").exists()
    resumed_messages = scripted.calls[1]["messages"]
    assistant = resumed_messages[-3]
    recovery = resumed_messages[-2]
    assert [item["id"] for item in assistant["content"]] == ["tool-A", "tool-B", "tool-C"]
    assert recovery["content"][0]["tool_use_id"] == "tool-A"
    assert recovery["content"][0]["content"].startswith("Wrote 1 characters")
    assert recovery["content"][1] == {
        "type": "tool_result",
        "tool_use_id": "tool-B",
        "content": agent.INTERRUPTED_TOOL_OUTCOME_UNKNOWN,
        "is_error": True,
    }
    assert recovery["content"][2] == {
        "type": "tool_result",
        "tool_use_id": "tool-C",
        "content": agent.TOOL_NOT_EXECUTED_AFTER_INTERRUPTION,
        "is_error": True,
    }
    assert recovery["content"][3:] == [
        {"type": "text", "text": agent.COMMON_RECOVERY_CONTEXT},
        {"type": "text", "text": agent.UNCERTAIN_TOOL_RECOVERY_CONTEXT},
    ]
    agent.validate_llm_message_protocol(resumed_messages)


def test_checkpoint_after_completed_tool_points_to_next_pending_tool(
    runtime, tmp_path, monkeypatch
):
    scripted, logger = set_script(runtime, [
        response(
            block("tool_use", id="tool-A", name="write_file", input={"path": "a.txt", "content": "A"}),
            block("tool_use", id="tool-B", name="write_file", input={"path": "b.txt", "content": "B"}),
            block("tool_use", id="tool-C", name="write_file", input={"path": "c.txt", "content": "C"}),
        ),
        response(block("text", text="continued safely")),
    ])
    state, state_path, _ = durable_runtime(runtime, tmp_path)
    original_checkpoint = agent.checkpoint

    def interrupt_after_a_is_durable(state_arg, path_arg, logger_arg, reason):
        result_ids = [
            item.get("tool_use_id")
            for item in state_arg["messages"][-1].get("content", [])
            if isinstance(item, dict) and item.get("type") == "tool_result"
        ]
        saved = original_checkpoint(state_arg, path_arg, logger_arg, reason)
        if reason == "tool_result.recorded" and result_ids == ["tool-A"]:
            raise KeyboardInterrupt
        return saved

    monkeypatch.setattr(agent, "checkpoint", interrupt_after_a_is_durable)
    with pytest.raises(KeyboardInterrupt):
        agent.run_once(
            state["messages"],
            "write A, B, and C",
            logger,
            runtime_state=state,
            state_path=state_path,
        )

    persisted = agent.load_state(state_path)
    assert [item["tool_use_id"] for item in persisted["messages"][-1]["content"]] == ["tool-A"]
    assert persisted["interruption_info"] == {
        "phase": "permission_wait",
        "tool_name": "write_file",
        "tool_use_id": "tool-B",
    }
    assert (tmp_path / "a.txt").read_text() == "A"
    assert not (tmp_path / "b.txt").exists()
    assert not (tmp_path / "c.txt").exists()

    persisted["current_run"]["status"] = "interrupted"
    original_checkpoint(persisted, state_path, logger, "run.interrupted")
    monkeypatch.setattr(agent, "checkpoint", original_checkpoint)
    result = agent.resume_run(persisted, state_path, logger, sleep_fn=lambda _: None)

    assert result["status"] == "completed"
    assert len(scripted.calls) == 2
    assert (tmp_path / "a.txt").read_text() == "A"
    assert not (tmp_path / "b.txt").exists()
    assert not (tmp_path / "c.txt").exists()


def test_last_tool_result_checkpoint_advances_round_before_interruption(
    runtime, tmp_path, monkeypatch
):
    scripted, logger = set_script(runtime, [
        response(block("tool_use", id="tool-A", name="write_file", input={"path": "a.txt", "content": "A"})),
        response(block("text", text="continued in round two")),
    ])
    state, state_path, _ = durable_runtime(runtime, tmp_path)
    original_checkpoint = agent.checkpoint

    def interrupt_after_batch_is_durable(state_arg, path_arg, logger_arg, reason):
        saved = original_checkpoint(state_arg, path_arg, logger_arg, reason)
        if reason == "tool_result.recorded":
            raise KeyboardInterrupt
        return saved

    monkeypatch.setattr(agent, "checkpoint", interrupt_after_batch_is_durable)
    with pytest.raises(KeyboardInterrupt):
        agent.run_once(
            state["messages"],
            "write A",
            logger,
            runtime_state=state,
            state_path=state_path,
        )

    persisted = agent.load_state(state_path)
    assert persisted["current_run"]["round"] == 2
    assert persisted["interruption_info"] is None
    assert persisted["messages"][-1]["content"][0]["tool_use_id"] == "tool-A"

    persisted["current_run"]["status"] = "interrupted"
    original_checkpoint(persisted, state_path, logger, "run.interrupted")
    monkeypatch.setattr(agent, "checkpoint", original_checkpoint)
    result = agent.resume_run(persisted, state_path, logger, sleep_fn=lambda _: None)

    assert result["status"] == "completed"
    started = [event for event in read_events(logger) if event["event_type"] == "llm.started"]
    assert [event["data"]["round"] for event in started] == [1, 2]
    assert (tmp_path / "a.txt").read_text() == "A"


def test_completed_response_is_durable_before_control_returns(
    runtime, tmp_path, monkeypatch
):
    scripted, logger = set_script(runtime, [response(block("text", text="finished"))])
    state, state_path, _ = durable_runtime(runtime, tmp_path)
    original_checkpoint = agent.checkpoint

    def interrupt_after_completion_is_durable(state_arg, path_arg, logger_arg, reason):
        saved = original_checkpoint(state_arg, path_arg, logger_arg, reason)
        if reason == "run.completed":
            raise KeyboardInterrupt
        return saved

    monkeypatch.setattr(agent, "checkpoint", interrupt_after_completion_is_durable)
    with pytest.raises(KeyboardInterrupt):
        agent.run_once(
            state["messages"],
            "finish now",
            logger,
            runtime_state=state,
            state_path=state_path,
        )

    persisted = agent.load_state(state_path)
    assert persisted["current_run"] == {
        "run_id": state["current_run"]["run_id"],
        "status": "completed",
        "round": 1,
    }
    assert persisted["interruption_info"] is None
    assert persisted["messages"][-1]["content"][0]["text"] == "finished"

    monkeypatch.setattr(agent, "checkpoint", original_checkpoint)
    assert agent.resume_run(persisted, state_path, logger) is None
    assert len(scripted.calls) == 1


def test_max_round_boundary_after_tool_batch_cannot_repeat_last_round(
    runtime, tmp_path, monkeypatch
):
    scripted, logger = set_script(runtime, [
        response(block("tool_use", id="tool-A", name="write_file", input={"path": "a.txt", "content": "A"})),
    ])
    state, state_path, _ = durable_runtime(runtime, tmp_path)
    state["current_run"] = {"run_id": "run-max", "status": "running", "round": 1}
    agent.checkpoint(state, state_path, logger, "run.started")
    original_checkpoint = agent.checkpoint

    def interrupt_after_batch_is_durable(state_arg, path_arg, logger_arg, reason):
        saved = original_checkpoint(state_arg, path_arg, logger_arg, reason)
        if reason == "tool_result.recorded":
            raise KeyboardInterrupt
        return saved

    monkeypatch.setattr(agent, "checkpoint", interrupt_after_batch_is_durable)
    with pytest.raises(KeyboardInterrupt):
        agent.agent_loop(
            state["messages"],
            logger,
            "run-max",
            max_rounds=1,
            runtime_state=state,
            state_path=state_path,
        )

    persisted = agent.load_state(state_path)
    assert persisted["current_run"] == {"run_id": "run-max", "status": "running", "round": 2}
    agent.prepare_recovery_messages(persisted)
    monkeypatch.setattr(agent, "checkpoint", original_checkpoint)
    result = agent.agent_loop(
        persisted["messages"],
        logger,
        "run-max",
        max_rounds=1,
        start_round=persisted["current_run"]["round"],
        runtime_state=persisted,
        state_path=state_path,
    )

    assert result["error"] == "MAX_ROUNDS_EXCEEDED"
    assert len(scripted.calls) == 1
    assert persisted["current_run"]["status"] == "failed"
    assert agent.load_state(state_path)["current_run"]["status"] == "failed"


def test_every_successful_checkpoint_in_multi_tool_run_is_loadable(
    runtime, tmp_path, monkeypatch
):
    set_script(runtime, [
        response(
            block("tool_use", id="tool-A", name="write_file", input={"path": "a.txt", "content": "A"}),
            block("tool_use", id="tool-B", name="write_file", input={"path": "b.txt", "content": "B"}),
        ),
        response(block("text", text="done")),
    ])
    state, state_path, logger = durable_runtime(runtime, tmp_path)
    original_checkpoint = agent.checkpoint
    reasons = []

    def validate_after_checkpoint(state_arg, path_arg, logger_arg, reason):
        result = original_checkpoint(state_arg, path_arg, logger_arg, reason)
        agent.load_state(path_arg)
        reasons.append(reason)
        return result

    monkeypatch.setattr(agent, "checkpoint", validate_after_checkpoint)
    result = agent.run_once(
        state["messages"],
        "write two files",
        logger,
        runtime_state=state,
        state_path=state_path,
    )

    assert result["status"] == "completed"
    assert reasons.count("tool_result.recorded") == 2
    assert reasons[-1] == "run.completed"


@pytest.mark.parametrize(
    "messages",
    [
        [{"role": "assistant", "content": [{"type": "tool_use", "id": "A", "name": "read_file", "input": {}}]}],
        [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "A", "name": "read_file", "input": {}},
                {"type": "tool_use", "id": "B", "name": "read_file", "input": {}},
            ]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "A", "content": "done"}]},
        ],
        [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "A", "name": "read_file", "input": {}}]},
            {"role": "user", "content": [
                {"type": "text", "text": "before"},
                {"type": "tool_result", "tool_use_id": "A", "content": "done"},
            ]},
        ],
        [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "A", "name": "read_file", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "other", "content": "done"}]},
        ],
    ],
)
def test_pairing_preflight_rejects_incomplete_or_invalid_messages_without_api_call(
    messages, runtime
):
    scripted, logger = runtime

    result = agent.agent_loop(messages, logger, "run-invalid-pairing")

    assert result["error"] == "PROTOCOL_ERROR"
    assert scripted.calls == []


def test_pairing_preflight_allows_complete_multi_tool_results_followed_by_text(runtime):
    scripted, logger = set_script(runtime, [response(block("text", text="continued"))])
    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "A", "name": "read_file", "input": {}},
            {"type": "tool_use", "id": "B", "name": "read_file", "input": {}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "A", "content": "a"},
            {"type": "tool_result", "tool_use_id": "B", "content": "b"},
            {"type": "text", "text": "<reminder>continue</reminder>"},
        ]},
    ]

    result = agent.agent_loop(messages, logger, "run-valid-pairing")

    assert result["status"] == "completed"
    assert len(scripted.calls) == 1


def test_recovery_marks_all_tools_not_executed_when_no_tool_started():
    state = recovery_state(
        "llm_call",
        [{"role": "assistant", "content": [
            {"type": "tool_use", "id": "A", "name": "write_file", "input": {}},
            {"type": "tool_use", "id": "B", "name": "write_file", "input": {}},
        ]}],
    )
    state["interruption_info"] = None

    agent.prepare_recovery_messages(state)

    content = state["messages"][-1]["content"]
    assert [item["tool_use_id"] for item in content[:2]] == ["A", "B"]
    assert all(item["content"] == agent.TOOL_NOT_EXECUTED_AFTER_INTERRUPTION for item in content[:2])
    assert content[2:] == [{"type": "text", "text": agent.COMMON_RECOVERY_CONTEXT}]
    agent.validate_llm_message_protocol(state["messages"])


def test_durable_result_wins_over_tool_result_recording_phase():
    state = recovery_state(
        "tool_result_recording",
        [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "A", "name": "write_file", "input": {}},
                {"type": "tool_use", "id": "B", "name": "write_file", "input": {}},
                {"type": "tool_use", "id": "C", "name": "write_file", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "A", "content": "a"},
                {"type": "tool_result", "tool_use_id": "B", "content": "b"},
            ]},
        ],
    )
    state["interruption_info"]["tool_use_id"] = "B"

    agent.prepare_recovery_messages(state)

    content = state["messages"][-1]["content"]
    assert content[0]["content"] == "a"
    assert content[1]["content"] == "b"
    assert content[2] == {
        "type": "tool_result",
        "tool_use_id": "C",
        "content": agent.TOOL_NOT_EXECUTED_AFTER_INTERRUPTION,
        "is_error": True,
    }
    assert content[3:] == [{"type": "text", "text": agent.COMMON_RECOVERY_CONTEXT}]
    agent.validate_llm_message_protocol(state["messages"])


def test_complete_tool_results_with_reminder_survive_llm_call_recovery():
    state = recovery_state(
        "llm_call",
        [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "A", "name": "read_file", "input": {}},
                {"type": "tool_use", "id": "B", "name": "read_file", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "A", "content": "a"},
                {"type": "tool_result", "tool_use_id": "B", "content": "b"},
                {"type": "text", "text": agent.TODO_MAINTENANCE_REMINDER},
            ]},
        ],
    )

    agent.validate_runtime_state(state)
    agent.prepare_recovery_messages(state)

    content = state["messages"][-1]["content"]
    assert [block["tool_use_id"] for block in content[:2]] == ["A", "B"]
    assert content[2] == {"type": "text", "text": agent.TODO_MAINTENANCE_REMINDER}
    assert content[3] == {"type": "text", "text": agent.COMMON_RECOVERY_CONTEXT}
    agent.validate_llm_message_protocol(state["messages"])


def test_multi_tool_recovery_can_be_resumed_again_during_llm_call():
    state = recovery_state(
        "tool_execution",
        [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "A", "name": "write_file", "input": {}},
                {"type": "tool_use", "id": "B", "name": "write_file", "input": {}},
                {"type": "tool_use", "id": "C", "name": "write_file", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "A", "content": "a"},
            ]},
        ],
    )
    state["interruption_info"]["tool_use_id"] = "B"

    agent.prepare_recovery_messages(state)
    first_content = list(state["messages"][-1]["content"])
    state["interruption_info"] = {"phase": "llm_call"}
    agent.validate_runtime_state(state)
    agent.prepare_recovery_messages(state)

    content = state["messages"][-1]["content"]
    assert content[: len(first_content)] == first_content
    assert [block["tool_use_id"] for block in content[:3]] == ["A", "B", "C"]
    assert sum(block.get("is_error", False) for block in content[:3]) == 2
    assert content[-1] == {"type": "text", "text": agent.COMMON_RECOVERY_CONTEXT}
    agent.validate_llm_message_protocol(state["messages"])


@pytest.mark.parametrize(
    "messages,status,interruption",
    [
        (
            [
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "A", "name": "read_file", "input": {}},
                    {"type": "tool_use", "id": "B", "name": "read_file", "input": {}},
                ]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "B", "content": "wrong"},
                ]},
            ],
            "running",
            {"phase": "tool_execution", "tool_name": "read_file", "tool_use_id": "A"},
        ),
        (
            [
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "A", "name": "read_file", "input": {}},
                ]},
                {"role": "user", "content": [
                    {"type": "text", "text": "too early"},
                    {"type": "tool_result", "tool_use_id": "A", "content": "a"},
                ]},
            ],
            "running",
            {"phase": "llm_call"},
        ),
        (
            [
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "A", "name": "read_file", "input": {}},
                    {"type": "tool_use", "id": "B", "name": "read_file", "input": {}},
                ]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "A", "content": "a"},
                    {"type": "text", "text": "partial cannot have text"},
                ]},
            ],
            "interrupted",
            {"phase": "tool_execution", "tool_name": "read_file", "tool_use_id": "B"},
        ),
        (
            [
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "A", "name": "read_file", "input": {}},
                    {"type": "tool_use", "id": "B", "name": "read_file", "input": {}},
                ]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "A", "content": "a"},
                ]},
            ],
            "completed",
            None,
        ),
    ],
)
def test_persisted_message_validation_rejects_invalid_pairings(
    messages, status, interruption
):
    state = agent.new_runtime_state("invalid-pairing")
    state["messages"] = messages
    state["current_run"] = {"run_id": "run", "status": status, "round": 2}
    state["interruption_info"] = interruption

    with pytest.raises(agent.StateLoadError):
        agent.validate_runtime_state(state)


def test_cli_invalid_persisted_pairing_uses_state_load_failure_path(
    tmp_path, monkeypatch, capsys
):
    state = agent.new_runtime_state("invalid-pairing")
    state["current_run"] = {"run_id": "run", "status": "running", "round": 2}
    state["interruption_info"] = {
        "phase": "tool_execution",
        "tool_name": "read_file",
        "tool_use_id": "A",
    }
    state["messages"] = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "A", "name": "read_file", "input": {}},
            {"type": "tool_use", "id": "B", "name": "read_file", "input": {}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "B", "content": "wrong"},
        ]},
    ]
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    before = path.read_bytes()
    called = []
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(agent, "ROOT_DIR", tmp_path / "missing-config")
    monkeypatch.setattr(agent, "LOG_DIR", log_dir)
    output = io.StringIO()

    code = agent.cli(
        lambda prompt: called.append(prompt) or "y",
        output,
        resume_path=path,
    )

    assert code == 3
    assert "[State Load Error]" in output.getvalue()
    assert "CONFIG_ERROR" not in capsys.readouterr().err
    assert called == []
    assert path.read_bytes() == before
    events = [
        json.loads(line)
        for line in (log_dir / "state-load-failures.jsonl").read_text().splitlines()
    ]
    assert events[-1]["event_type"] == "state.load_failed"
    assert not any(event["event_type"] == "state.restored" for event in events)


def test_cli_resume_preparation_state_load_error_is_defensively_reported(
    tmp_path, monkeypatch, capsys
):
    state = recovery_state("llm_call")
    path = tmp_path / "valid.json"
    agent.save_state(state, path)
    before = path.read_bytes()
    called = []
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(agent, "ROOT_DIR", tmp_path / "missing-config")
    monkeypatch.setattr(agent, "LOG_DIR", log_dir)

    def fail_preparation(_state):
        raise agent.StateLoadError("defensive preparation failure")

    monkeypatch.setattr(agent, "prepare_recovery_messages", fail_preparation)
    output = io.StringIO()

    code = agent.cli(
        lambda prompt: called.append(prompt) or "y",
        output,
        resume_path=path,
    )

    assert code == 3
    assert "[State Load Error]" in output.getvalue()
    assert "CONFIG_ERROR" not in capsys.readouterr().err
    assert called == []
    assert path.read_bytes() == before
    events = [
        json.loads(line)
        for line in (log_dir / "state-load-failures.jsonl").read_text().splitlines()
    ]
    assert events[-1]["event_type"] == "state.load_failed"
    assert not any(event["event_type"] == "state.restored" for event in events)
