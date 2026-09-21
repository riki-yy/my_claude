import copy
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent
import memory_pipeline
from memory_pipeline import MemoryPipeline
from memory_store import MemoryStore, MemoryStoreError


def response(text):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=None,
        stop_reason="end_turn",
    )


def tool_response(tool_id="tool-1"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", id=tool_id, name="read_file", input={"path": "x.txt"})],
        usage=None,
        stop_reason="tool_use",
    )


class ScriptedMessages:
    def __init__(self, items):
        self.items = list(items)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.items:
            raise RuntimeError("Fake Model response queue exhausted")
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Logger:
    def __init__(self):
        self.events = []

    def emit(self, event, run_id=None, **data):
        self.events.append((event, run_id, data))


def candidate(memory_id="project-tests", body="Use python3.12 -m pytest.", **changes):
    item = {
        "id": memory_id,
        "type": "project",
        "title": "Project test command",
        "summary": "The project uses pytest through Python 3.12.",
        "body": body,
    }
    item.update(changes)
    return item


@pytest.fixture
def store(tmp_path):
    result = MemoryStore(tmp_path / "memory")
    result.ensure()
    return result


def make_pipeline(store, scripted, logger=None, estimate=None):
    return MemoryPipeline(
        store,
        scripted.create,
        "fake-model",
        estimate or agent.estimate_context_tokens,
        logger or Logger(),
    )


def test_store_commits_body_before_index_and_reads_only_committed(store):
    assert store.store(candidate()) == "added"
    assert store.read_index(validate_bodies=True) == [{
        "name": "project-tests",
        "description": "The project uses pytest through Python 3.12.",
        "type": "project",
    }]
    assert store.read("project-tests")["body"] == "Use python3.12 -m pytest."
    raw = (store.root / "project-tests.md").read_text(encoding="utf-8")
    assert raw == (
        "---\n"
        "name: project-tests\n"
        'description: "The project uses pytest through Python 3.12."\n'
        "type: project\n"
        "---\n\n"
        "Use python3.12 -m pytest.\n"
    )
    assert "created_at:" not in raw and "updated_at:" not in raw
    assert "Use python3.12" not in store.index_text()
    assert store.index_text() == (
        "# Memory Index\n\n"
        "- name: project-tests\n"
        "  description: The project uses pytest through Python 3.12.\n"
        "  type: project\n"
    )
    assert not any(field in store.index_text() for field in ("id:", "title:", "summary:", "created_at:", "updated_at:"))
    assert store.store(candidate()) == "skipped"


@pytest.mark.parametrize(
    "raw,match",
    [
        (
            '# Memory Index\n\n- {"id":"legacy","title":"Legacy","summary":"Legacy","type":"project"}\n',
            "only name, description, and type",
        ),
        (
            "# Memory Index\n\n- name: bad-type\n  description: Bad type\n  type: current_task\n",
            "type is not supported",
        ),
        (
            "# Memory Index\n\n- name: duplicate\n  description: One\n  type: project\n\n- name: duplicate\n  description: Two\n  type: project\n",
            "duplicate name",
        ),
        (
            "# Memory Index\n\n- name: extra\n  description: Extra field\n  type: project\n  title: forbidden\n",
            "only name, description, and type",
        ),
    ],
)
def test_index_rejects_legacy_invalid_or_extra_fields(store, raw, match):
    store.index_path.write_text(raw, encoding="utf-8")
    with pytest.raises(MemoryStoreError, match=match):
        store.read_index()


@pytest.mark.parametrize(
    "raw,match",
    [
        (
            "name: no-opening-delimiter\ndescription: Missing delimiter\ntype: project\n---\n\nBody\n",
            "YAML frontmatter",
        ),
        (
            "---\nname: missing-description\ntype: project\n---\n\nBody\n",
            "only name, description, and type",
        ),
        (
            "---\nname: duplicate\nname: duplicate-again\ndescription: Duplicate name\ntype: project\n---\n\nBody\n",
            "duplicate field: name",
        ),
        (
            "---\nname: unknown-field\ndescription: Unknown field\ntype: project\nextra: no\n---\n\nBody\n",
            "only name, description, and type",
        ),
        (
            "---\nname: bad-type\ndescription: Bad type\ntype: current_task\n---\n\nBody\n",
            "type is not supported",
        ),
        (
            "---\nname: empty-body\ndescription: Empty body\ntype: project\n---\n\n\n",
            "YAML frontmatter|non-empty",
        ),
    ],
)
def test_memory_file_rejects_invalid_frontmatter_or_body(store, raw, match):
    path = store.root / "invalid.md"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(MemoryStoreError, match=match):
        store.read("invalid")


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("name", "different-name"),
        ("description", "Different description"),
        ("type", "reference"),
    ],
)
def test_index_body_frontmatter_mismatch_is_rejected(store, field, replacement):
    store.store(candidate())
    path = store.root / "project-tests.md"
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.startswith(field + ":"):
            lines[index] = f"{field}: {replacement}"
            break
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(MemoryStoreError, match="metadata mismatch|filename and embedded name differ"):
        store.read_index(validate_bodies=True)


@pytest.mark.parametrize(
    "change,match",
    [
        ({"id": "../escape"}, "safe stable"),
        ({"type": "current_task"}, "not supported"),
        ({"body": ""}, "non-empty"),
        ({"body": "x" * 12_001}, "size limit"),
    ],
)
def test_candidate_validation_rejects_invalid_fields(store, change, match):
    with pytest.raises(MemoryStoreError, match=match):
        store.store(candidate(**change))


def test_index_failure_rolls_back_new_body_and_temp_then_retry_commits(store, monkeypatch):
    real = store._atomic_write
    failed = False

    def fail_index(path, content):
        nonlocal failed
        if path == store.index_path and not failed:
            failed = True
            (store.root / ".tmp-MEMORY.md-injected").write_text("partial", encoding="utf-8")
            (store.root / ".tmp-unrelated.md-injected").write_text("unrelated", encoding="utf-8")
            raise OSError("index unavailable")
        return real(path, content)

    monkeypatch.setattr(store, "_atomic_write", fail_index)
    with pytest.raises(MemoryStoreError, match="candidate body rolled back"):
        store.store(candidate())
    assert not (store.root / "project-tests.md").exists()
    assert not (store.root / ".tmp-MEMORY.md-injected").exists()
    assert (store.root / ".tmp-unrelated.md-injected").exists()
    assert store.read_index() == []

    monkeypatch.setattr(store, "_atomic_write", real)
    assert store.store(candidate()) == "added"
    assert len(store.committed()) == 1


def test_index_failure_preserves_previously_committed_memories(store, monkeypatch):
    assert store.store(candidate("first")) == "added"
    before_index = store.index_path.read_text(encoding="utf-8")
    before_body = (store.root / "first.md").read_text(encoding="utf-8")
    real = store._atomic_write

    def fail_second_index(path, content):
        if path == store.index_path and "- name: second" in content:
            raise OSError("index unavailable")
        return real(path, content)

    monkeypatch.setattr(store, "_atomic_write", fail_second_index)
    with pytest.raises(MemoryStoreError, match="candidate body rolled back"):
        store.store(candidate("second", body="Second durable fact", summary="Second durable fact."))

    assert store.index_path.read_text(encoding="utf-8") == before_index
    assert (store.root / "first.md").read_text(encoding="utf-8") == before_body
    assert not (store.root / "second.md").exists()
    assert [item["name"] for item in store.read_index(validate_bodies=True)] == ["first"]


def test_index_failure_after_replace_restores_old_index_before_body_rollback(store, monkeypatch):
    assert store.store(candidate("first")) == "added"
    real = store._atomic_write
    failed = False

    def fail_after_index_replace(path, content):
        nonlocal failed
        result = real(path, content)
        if path == store.index_path and "- name: second" in content and not failed:
            failed = True
            raise OSError("directory fsync failed after replace")
        return result

    monkeypatch.setattr(store, "_atomic_write", fail_after_index_replace)
    with pytest.raises(MemoryStoreError, match="candidate body rolled back"):
        store.store(candidate("second", body="Second durable fact", summary="Second durable fact."))

    assert [item["name"] for item in store.read_index(validate_bodies=True)] == ["first"]
    assert (store.root / "first.md").exists()
    assert not (store.root / "second.md").exists()


def test_cleanup_distinguishes_temporary_orphan_and_committed(store):
    store.store(candidate())
    (store.root / ".tmp-dead").write_text("partial", encoding="utf-8")
    orphan = candidate("orphan")
    (store.root / "orphan.md").write_text(store._render_body(store.validate_candidate(orphan)), encoding="utf-8")
    assert store.cleanup_uncommitted() == {"temporary": 1, "orphan": 1}
    assert (store.root / "project-tests.md").exists()


def test_recall_llm_selection_injects_validated_body_only_temporarily(store):
    store.store(candidate())
    scripted = ScriptedMessages([response('{"ids":["project-tests"]}')])
    pipeline = make_pipeline(store, scripted)
    result = pipeline.recall("What test command does this project use?", "run-1")
    assert result.selected_ids == ["project-tests"]
    assert "Use python3.12 -m pytest." in result.system_context
    assert scripted.calls[0]["system"] == memory_pipeline.SELECTOR_SYSTEM


def test_selector_invalid_output_uses_deterministic_fallback_and_caps_five(store):
    for number in range(7):
        store.store(candidate(
            f"project-test-{number}",
            body=f"Test command {number}",
            title=f"Project test command {number}",
            summary=f"Project test command variant {number}.",
        ))
    scripted = ScriptedMessages([response('{"ids":["unknown","unknown"]}')])
    pipeline = make_pipeline(store, scripted)
    result = pipeline.recall("project test command", "run-1")
    assert result.selector_path == "fallback"
    assert len(result.selected_ids) == 5
    assert result.selected_ids == sorted(result.selected_ids)


def test_missing_or_corrupt_selected_body_is_not_injected(store):
    store.store(candidate())
    (store.root / "project-tests.md").write_text("corrupt", encoding="utf-8")
    scripted = ScriptedMessages([response('{"ids":["project-tests"]}')])
    logger = Logger()
    result = make_pipeline(store, scripted, logger).recall("test command", "run-1")
    assert result.system_context == "" and result.selected_ids == []
    assert any(event == "memory.recall.item_failed" for event, _run, _data in logger.events)


def test_extract_uses_full_history_exact_scope_prompt_and_empty_advances(store):
    messages = [
        {"role": "user", "content": "old context"},
        {"role": "assistant", "content": [{"type": "text", "text": "old reply"}]},
        {"role": "user", "content": "new durable fact"},
        {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
    ]
    scripted = ScriptedMessages([response('{"memories":[]}')])
    result = make_pipeline(store, scripted).extract_and_store(messages, 1, "run-1")
    call = scripted.calls[0]
    assert call["messages"][:-1] == messages
    prompt = call["messages"][-1]["content"]
    assert "most recent ~2 model-visible messages" in prompt
    assert "You MUST only use content from the last ~2 messages" in prompt
    assert result == {"candidate_count": 0, "outcomes": [], "cursor": 3}


def test_extract_over_budget_fails_without_model_call_or_store(store):
    scripted = ScriptedMessages([])
    pipeline = make_pipeline(store, scripted, estimate=lambda *args, **kwargs: memory_pipeline.EXTRACT_SAFE_INPUT_TOKENS)
    with pytest.raises(MemoryStoreError, match="safe input budget"):
        pipeline.extract_and_store([{"role": "user", "content": "x"}], None, "run-1")
    assert scripted.calls == [] and store.read_index() == []


def test_partial_store_failure_keeps_committed_item_and_cursor_cannot_advance(store, monkeypatch):
    output = {"memories": [candidate("first"), candidate("second", body="Second fact")]}
    scripted = ScriptedMessages([response(json.dumps(output))])
    real = store.store

    def fail_second(item):
        if item["id"] == "second":
            raise MemoryStoreError("forced failure")
        return real(item)

    monkeypatch.setattr(store, "store", fail_second)
    with pytest.raises(MemoryStoreError, match="forced failure"):
        make_pipeline(store, scripted).extract_and_store([{"role": "user", "content": "facts"}], None, "run-1")
    assert [item["name"] for item in store.read_index()] == ["first"]


def test_store_failure_keeps_runtime_cursor_unchanged(store, monkeypatch):
    scripted = ScriptedMessages([response(json.dumps({"memories": [candidate()]}))])
    pipeline = make_pipeline(store, scripted)
    monkeypatch.setattr(agent, "_memory_pipeline", lambda _logger: pipeline)
    state = agent.new_runtime_state("session-1")
    state["messages"] = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
    ]
    state["last_memory_message_index"] = 0
    monkeypatch.setattr(store, "store", lambda _candidate: (_ for _ in ()).throw(MemoryStoreError("forced failure")))
    logger = Logger()

    agent._memory_after_completed_run(state["messages"], state, None, logger, "run-1")

    assert state["last_memory_message_index"] == 0
    assert any(event == "memory.extract.failed" and data["cursor_status"] == "unchanged" for event, _run, data in logger.events)


def test_partial_batch_index_failure_keeps_first_commit_and_retry_completes(store, monkeypatch):
    output = {"memories": [
        candidate("first"),
        candidate("second", body="Second durable fact", summary="Second durable fact."),
    ]}
    real = store._atomic_write
    failed = False

    def fail_second_index(path, content):
        nonlocal failed
        if path == store.index_path and "- name: second" in content and not failed:
            failed = True
            raise OSError("index unavailable")
        return real(path, content)

    monkeypatch.setattr(store, "_atomic_write", fail_second_index)
    with pytest.raises(MemoryStoreError, match="candidate body rolled back"):
        make_pipeline(store, ScriptedMessages([response(json.dumps(output))])).extract_and_store(
            [{"role": "user", "content": "facts"}], None, "run-1"
        )
    assert [item["name"] for item in store.read_index(validate_bodies=True)] == ["first"]
    assert not (store.root / "second.md").exists()

    monkeypatch.setattr(store, "_atomic_write", real)
    result = make_pipeline(store, ScriptedMessages([response(json.dumps(output))])).extract_and_store(
        [{"role": "user", "content": "facts retried"}], None, "run-2"
    )
    assert result["outcomes"] == ["skipped", "added"]
    assert [item["name"] for item in store.read_index(validate_bodies=True)] == ["first", "second"]


def test_partial_store_retry_skips_committed_candidate_and_finishes_remaining(store):
    assert store.store(candidate("first")) == "added"
    output = {"memories": [candidate("first"), candidate("second", body="Second fact", summary="A second durable fact.")]}
    scripted = ScriptedMessages([response(json.dumps(output))])
    result = make_pipeline(store, scripted).extract_and_store(
        [{"role": "user", "content": "facts retried"}], None, "run-2"
    )
    assert result["outcomes"] == ["skipped", "added"]
    assert [item["name"] for item in store.read_index()] == ["first", "second"]


def test_run_once_recalls_once_across_tool_rounds_and_extracts_once(monkeypatch, tmp_path):
    memory_dir = tmp_path / "memory"
    seeded = MemoryStore(memory_dir)
    seeded.ensure()
    seeded.store(candidate())
    scripted = ScriptedMessages([
        response('{"ids":["project-tests"]}'),
        tool_response(),
        response("finished"),
        response('{"memories":[]}'),
    ])
    monkeypatch.setattr(agent, "client", SimpleNamespace(messages=scripted))
    monkeypatch.setattr(agent, "MODEL", "fake-model")
    monkeypatch.setattr(agent, "MEMORY_DIR", memory_dir)
    monkeypatch.setattr(agent, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(agent, "DENY_RULES", [])
    monkeypatch.setattr(agent, "ASK_RULES", [])
    (tmp_path / "x.txt").write_text("x", encoding="utf-8")
    logger = agent.EventLogger("session-1", tmp_path / "events.jsonl", io.StringIO())
    state = agent.new_runtime_state("session-1")
    state_path = tmp_path / "state.json"

    result = agent.run_once(state["messages"], "run tests", logger, runtime_state=state, state_path=state_path)

    assert result["status"] == "completed"
    assert [call["system"] for call in scripted.calls].count(memory_pipeline.SELECTOR_SYSTEM) == 1
    main_calls = [call for call in scripted.calls if call["system"].startswith(agent.SYSTEM)]
    assert len(main_calls) == 2
    assert all("Use python3.12 -m pytest." in call["system"] for call in main_calls)
    assert all("PersistentMemory" not in json.dumps(agent._jsonable(message)) for message in state["messages"])
    assert state["last_memory_message_index"] == len(state["messages"]) - 1


def test_failed_run_does_not_extract(monkeypatch, tmp_path):
    scripted = ScriptedMessages([RuntimeError("fatal")])
    monkeypatch.setattr(agent, "client", SimpleNamespace(messages=scripted))
    monkeypatch.setattr(agent, "MODEL", "fake-model")
    monkeypatch.setattr(agent, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(agent, "MAX_RETRIES", 0)
    logger = agent.EventLogger("session-1", tmp_path / "events.jsonl", io.StringIO())
    state = agent.new_runtime_state("session-1")
    result = agent.run_once(state["messages"], "fail", logger, runtime_state=state, state_path=tmp_path / "state.json", sleep_fn=lambda _: None)
    assert result["status"] == "failed"
    assert not any(call.get("system") == memory_pipeline.EXTRACT_SYSTEM for call in scripted.calls)
    assert state["last_memory_message_index"] is None


def test_full_compact_resets_cursor_in_same_checkpoint_and_rollback_restores(monkeypatch, tmp_path):
    logger = agent.EventLogger("session-1", tmp_path / "events.jsonl", io.StringIO())
    messages = [{"role": "user", "content": "old"}, {"role": "assistant", "content": [{"type": "text", "text": "reply"}]}, {"role": "user", "content": "recent"}]
    state = agent.new_runtime_state("session-1")
    state["messages"] = messages
    state["last_memory_message_index"] = 1
    state["current_run"] = {"run_id": "run-1", "status": "running", "round": 1}
    monkeypatch.setattr(agent, "_full_compact", lambda *args, **kwargs: [{"role": "user", "content": "summary"}])
    monkeypatch.setattr(agent, "estimate_context_tokens", lambda *args, **kwargs: 1)
    saved = []
    monkeypatch.setattr(agent, "checkpoint", lambda state, *_args: saved.append(copy.deepcopy(state)))
    agent.prepare_context(messages, logger, "run-1", 1, state, tmp_path / "state.json", force_full=True)
    assert state["last_memory_message_index"] is None
    assert saved[-1]["messages"] == messages and saved[-1]["last_memory_message_index"] is None

    state["last_memory_message_index"] = 0
    messages[:] = [{"role": "user", "content": "again"}, {"role": "assistant", "content": [{"type": "text", "text": "x"}]}]
    original = copy.deepcopy(messages)
    monkeypatch.setattr(agent, "checkpoint", lambda *_args: (_ for _ in ()).throw(agent.StateSaveError("no")))
    with pytest.raises(agent.StateSaveError):
        agent.prepare_context(messages, logger, "run-1", 1, state, tmp_path / "state.json", force_full=True)
    assert messages == original and state["last_memory_message_index"] == 0


def test_consolidate_snapshot_and_failure_rolls_back_to_post_store_state(store, monkeypatch):
    store.store(candidate("first"))
    store.store(candidate("second", body="Second durable fact"))
    before = store.committed()
    real_atomic = store._atomic_write

    def fail_replacement(path, content):
        if path == store.index_path:
            raise OSError("replace failed")
        return real_atomic(path, content)

    monkeypatch.setattr(store, "_atomic_write", fail_replacement)
    with pytest.raises(OSError, match="replace failed"):
        store.consolidate([candidate("merged", body="Merged durable facts")])
    monkeypatch.setattr(store, "_atomic_write", real_atomic)
    assert store.committed() == before
    assert any(store.snapshots_dir.iterdir())
