"""V05: the verified V04 runtime with an in-memory Plan/Todo tool."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


def _configure_line_editing(readline_module: Any) -> None:
    """Enable the small set of libedit bindings used by the reference project."""
    readline_module.parse_and_bind("set bind-tty-special-chars off")
    readline_module.parse_and_bind("set input-meta on")
    readline_module.parse_and_bind("set output-meta on")
    readline_module.parse_and_bind("set convert-meta off")


try:
    import readline

    _configure_line_editing(readline)
except ImportError:
    # Python builds without readline still run, but do not get interactive editing.
    pass

from anthropic import Anthropic
from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = Path(__file__).resolve().parent / "logs"
WORKSPACE_ROOT = Path(__file__).resolve().parent
SYSTEM = (
    "You are a concise coding agent. Use the available tools to inspect and modify "
    "the workspace and to run commands when the task requires it. Paths are relative "
    "to the workspace. Complete the user's stated task directly and stop exploring once "
    "you have enough evidence to answer. Do not inspect parent directories, Git state, "
    "environment files, or runtime source unless the user explicitly asks. When the user "
    "names files to create, create them directly instead of repeatedly listing directories. "
    "Use glob, grep, and read_file for discovery; use write_file and edit_file for file "
    "changes; reserve bash for commands and tests. Run "
    "Python tests with 'python3.12 -m pytest', then give the final answer as soon as the "
    "requested result is verified. If a tool result contains PERMISSION_DENIED, do not "
    "try another tool or workaround unless the user explicitly requested an alternative; "
    "explain the denial and finish the current task. Use todo_write when the user asks for "
    "a plan or when a multi-step task benefits from explicit progress tracking. Every "
    "todo_write call must contain the complete current todo list, with at most one item "
    "in_progress. Explicitly call todo_write again to mark completed work; tool results do "
    "not update todos automatically. Report what you changed and what you verified."
)
TOOLS = [
    {
        "name": "bash",
        "description": "Run a shell command in the fixed workspace directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_seconds": {"type": "number", "minimum": 0.1, "maximum": 30},
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file inside the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Create or replace a UTF-8 text file inside the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace one unique exact text occurrence in a workspace file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
    {
        "name": "glob",
        "description": "Find workspace paths matching a relative glob pattern.",
        "input_schema": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}},
            "required": ["pattern"],
        },
    },
    {
        "name": "grep",
        "description": "Search UTF-8 workspace files with a regular expression.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string", "default": "."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "todo_write",
        "description": (
            "Replace the complete in-memory todo list. Send every current item on every "
            "call. Use pending, in_progress, or completed, with at most one in_progress item."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "maxItems": 20,
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "minLength": 1},
                            "content": {"type": "string", "minLength": 1},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                            },
                        },
                        "required": ["id", "content", "status"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["todos"],
            "additionalProperties": False,
        },
    },
]
MAX_TOKENS = 8000
MAX_ROUNDS = 20
SUMMARY_LIMIT = 240
CLI_PREVIEW_LIMIT = 80
CLI_LIST_LIMIT = 5
TOOL_NAME_COLOR = "\033[93m"
ANSI_RESET = "\033[0m"
SHELL_TIMEOUT_SECONDS = 10.0
MAX_SHELL_TIMEOUT_SECONDS = 30.0
TOOL_OUTPUT_LIMIT = 12_000
MAX_SEARCH_RESULTS = 200
MAX_TODOS = 20
TODO_STATUSES = ("pending", "in_progress", "completed")
TODO_SYMBOLS = {"pending": "○", "in_progress": "›", "completed": "✓"}

# V05 state is deliberately process-local. Persistence belongs to V06.
TODO_STATE: list[dict[str, str]] = []

# Configured by configure_model() for the real CLI. Tests replace `client`
# directly, at the same client.messages.create surface used in production.
client: Any = None
MODEL: str | None = None

HOOK_EVENTS = ("PreToolUse", "PostToolUse")
HOOKS: dict[str, list[Callable[[dict[str, Any]], None]]] = {
    event: [] for event in HOOK_EVENTS
}


def register_hook(event: str, callback: Callable[[dict[str, Any]], None]) -> None:
    """Append one callback to a supported tool lifecycle event."""
    if event not in HOOKS:
        raise ValueError(f"UNKNOWN_HOOK_EVENT: {event}")
    if not callable(callback):
        raise TypeError("hook callback must be callable")
    HOOKS[event].append(callback)


def trigger_hooks(event: str, context: dict[str, Any]) -> None:
    """Call an event's callbacks in registration order with one shared context."""
    if event not in HOOKS:
        raise ValueError(f"UNKNOWN_HOOK_EVENT: {event}")
    for callback in tuple(HOOKS[event]):
        callback(context)


def configure_model() -> None:
    """Load the shared root .env and create the reference-style SDK client."""
    global client, MODEL

    env_path = ROOT_DIR / ".env"
    if not env_path.exists():
        raise RuntimeError(
            f"CONFIG_ERROR: missing {env_path}. Copy .env.example to .env first."
        )
    load_dotenv(env_path, override=True)
    missing = [name for name in ("ANTHROPIC_API_KEY", "MODEL_ID") if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"CONFIG_ERROR: missing environment variable(s): {', '.join(missing)}")
    if os.getenv("ANTHROPIC_BASE_URL"):
        os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

    client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
    MODEL = os.environ["MODEL_ID"]


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _as_message_content(content: Any) -> Any:
    """Keep SDK blocks intact; dict blocks from tests remain dicts."""
    if not isinstance(content, list):
        raise ValueError("response.content must be a list")
    return content


SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|cookie)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
]


def redact(text: str) -> str:
    result = text
    for pattern in SECRET_PATTERNS:
        if pattern.pattern.startswith("\\bsk-"):
            result = pattern.sub("[REDACTED]", result)
        else:
            result = pattern.sub(r"\1[REDACTED]", result)
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        result = result.replace(api_key, "[REDACTED]")
    return result


def summarize(value: Any, limit: int = SUMMARY_LIMIT) -> dict[str, Any]:
    if isinstance(value, str):
        raw = value
    else:
        try:
            raw = json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
        except Exception:
            raw = repr(value)
    safe = redact(raw)
    return {
        "summary": safe[:limit],
        "original_length": len(raw),
        "truncated": len(safe) > limit,
    }


def _supports_color(stream: Any) -> bool:
    isatty = getattr(stream, "isatty", None)
    try:
        return callable(isatty) and isatty() and os.getenv("TERM") != "dumb" and "NO_COLOR" not in os.environ
    except (OSError, ValueError):
        return False


def _highlight_tool_call(name: str, stream: Any) -> str:
    label_and_name = f"[Tool Call] {name}"
    if not _supports_color(stream):
        return label_and_name
    return f"{TOOL_NAME_COLOR}{label_and_name}{ANSI_RESET}"


def _quoted(value: Any, limit: int = CLI_PREVIEW_LIMIT) -> str:
    text = redact(str(value)).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    if len(text) > limit:
        text = text[:limit] + "..."
    return f'"{text}"'


def _tool_call_summary(name: str, tool_input: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if name == "todo_write" and isinstance(tool_input.get("todos"), list):
        todo_count = len(tool_input["todos"])
        summary = f"todos={todo_count}"
        raw_length = len(json.dumps(tool_input, ensure_ascii=False, default=str, sort_keys=True))
        return f"{name} {summary}", {
            "summary": summary,
            "fields": {"todos": todo_count},
            "original_length": raw_length,
            "truncated": True,
        }

    fields_by_tool = {
        "bash": ("command", "timeout_seconds"),
        "read_file": ("path",),
        "write_file": ("path",),
        "edit_file": ("path",),
        "glob": ("pattern",),
        "grep": ("pattern", "path"),
    }
    keys = fields_by_tool.get(name, tuple(sorted(tool_input)))
    safe_arguments: dict[str, Any] = {}
    display_fields: list[str] = []
    for key in keys:
        if key not in tool_input:
            continue
        value = tool_input[key]
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe_arguments[key] = redact(str(value))[:SUMMARY_LIMIT]
            display_fields.append(f"{key}={_quoted(value)}" if isinstance(value, str) else f"{key}={value}")
    raw_length = len(json.dumps(tool_input, ensure_ascii=False, default=str, sort_keys=True))
    summary = " ".join(display_fields) or "no key arguments"
    return f"{name} {summary}", {
        "summary": summary,
        "fields": safe_arguments,
        "original_length": raw_length,
        "truncated": any(isinstance(tool_input.get(key), str) and len(tool_input[key]) > SUMMARY_LIMIT for key in keys)
        or any(key not in keys for key in tool_input),
    }


def _preview(text: str) -> str:
    compact = " ".join(text.split())
    return compact[:CLI_PREVIEW_LIMIT] + ("..." if len(compact) > CLI_PREVIEW_LIMIT else "")


def _tool_result_summary(
    name: str,
    tool_input: dict[str, Any],
    result: str,
    is_error: bool,
    error_code: str | None,
    duration: float,
) -> tuple[str, dict[str, Any]]:
    original_length = len(result)
    if is_error:
        prefix = f"{error_code}: " if error_code else ""
        detail = result[len(prefix):] if result.startswith(prefix) else result
        display = f"error={error_code or 'TOOL_ERROR'} | {_preview(redact(detail))}"
        fields = {"status": "error", "error_code": error_code, "error": _preview(redact(detail))}
        truncated = len(" ".join(detail.split())) > CLI_PREVIEW_LIMIT
    elif name == "read_file":
        lines = len(result.splitlines())
        size_kb = len(result.encode("utf-8")) / 1024
        display = f"success | {lines} lines | {size_kb:.1f} KB | preview={_quoted(_preview(result))}"
        fields = {"status": "success", "lines": lines, "bytes": len(result.encode("utf-8")), "preview": _preview(redact(result))}
        truncated = len(" ".join(result.split())) > CLI_PREVIEW_LIMIT
    elif name == "write_file":
        chars = len(tool_input.get("content", ""))
        display = f"success | wrote {chars} chars"
        fields = {"status": "success", "characters_written": chars}
        truncated = False
    elif name == "edit_file":
        display = "success | replaced 1 occurrence"
        fields = {"status": "success", "occurrences_replaced": 1}
        truncated = False
    elif name in {"glob", "grep"}:
        items = [] if result == "No matches" else [line for line in result.splitlines() if not line.startswith("[results truncated")]
        display = f"{len(items)} matches"
        if items:
            display += "\n" + "\n".join(f"  {redact(item)[:SUMMARY_LIMIT]}" for item in items[:CLI_LIST_LIMIT])
            if len(items) > CLI_LIST_LIMIT:
                display += f"\n  ... {len(items) - CLI_LIST_LIMIT} more"
        fields = {"status": "success", "matches": len(items), "preview": [redact(item)[:SUMMARY_LIMIT] for item in items[:CLI_LIST_LIMIT]]}
        truncated = len(items) > CLI_LIST_LIMIT or any(len(item) > SUMMARY_LIMIT for item in items[:CLI_LIST_LIMIT])
    elif name == "bash":
        first_line, _, output = result.partition("\n")
        exit_code = first_line.removeprefix("exit_code=") if first_line.startswith("exit_code=") else "unknown"
        display = f"exit_code={exit_code} | duration={duration:.2f}s | {_preview(redact(output or result))}"
        fields = {"status": "success", "exit_code": int(exit_code) if exit_code.isdigit() else exit_code, "duration_seconds": duration, "preview": _preview(redact(output or result))}
        truncated = len(" ".join((output or result).split())) > CLI_PREVIEW_LIMIT
    elif name == "todo_write":
        # The rendered TodoList is the tool result itself. Keep this exact display for
        # the CLI while retaining bounded JSONL fields under the existing log policy.
        display = result
        safe_result = redact(result)
        fields = {"status": "success", "preview": safe_result[:SUMMARY_LIMIT]}
        truncated = len(safe_result) > SUMMARY_LIMIT
    else:
        display = f"success | {_preview(redact(result))}"
        fields = {"status": "success", "preview": _preview(redact(result))}
        truncated = len(" ".join(result.split())) > CLI_PREVIEW_LIMIT
    return display, {
        **fields,
        "original_length": original_length,
        "truncated": truncated,
    }


class EventLogger:
    """One running session's CLI and JSONL output, without an event framework."""

    def __init__(self, session_id: str, log_path: Path | None, stream: Any = None):
        self.session_id = session_id
        self.log_path = log_path
        self.stream = stream or sys.stdout
        self.sequence = 0

    def emit(self, event_type: str, run_id: str | None = None, **data: Any) -> dict[str, Any]:
        self.sequence += 1
        event = {
            "schema_version": 1,
            "event_id": str(uuid.uuid4()),
            "session_id": self.session_id,
            "run_id": run_id,
            "sequence": self.sequence,
            "timestamp": datetime.now(UTC).isoformat(),
            "event_type": event_type,
            "data": data,
        }
        self._print(event)
        if self.log_path is not None:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                log_event = event
                if (
                    event_type == "tool.completed"
                    and data.get("tool_name") == "todo_write"
                    and isinstance(data.get("result"), dict)
                ):
                    # CLI and the model receive the complete render. JSONL keeps the
                    # inherited bounded-summary policy instead of storing long content.
                    log_event = {**event, "data": dict(event["data"])}
                    preview = str(data["result"].get("preview", ""))
                    if data["result"].get("truncated"):
                        preview += "..."
                    log_event["data"]["display"] = preview
                with self.log_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(log_event, ensure_ascii=False, default=str) + "\n")
            except (OSError, TypeError, ValueError) as exc:
                print(f"[Observability Warning] JSONL write failed: {redact(str(exc))}", file=sys.stderr)
        return event

    def _print(self, event: dict[str, Any]) -> None:
        labels = {
            "session.started": "Session",
            "session.ended": "Session",
            "run.started": "Run",
            "run.completed": "Final",
            "run.failed": "Error",
            "llm.started": "LLM",
            "llm.completed": "LLM Result",
            "llm.failed": "LLM Error",
            "permission.allowed": "Permission Allowed",
            "permission.required": "Permission Required",
            "permission.denied": "Permission Denied",
            "tool.started": "Tool Call",
            "tool.completed": "Tool Result",
            "tool.failed": "Tool Error",
        }
        label = labels.get(event["event_type"], event["event_type"])
        data = event["data"]
        if event["event_type"] == "llm.started":
            detail = f"requesting model={data['model']} round={data['round']}"
            print(f"[{label}] {detail}", file=self.stream)
            return
        if event["event_type"] == "llm.completed":
            fields = [
                f"model={data['model']}",
                f"round={data['round']}",
                f"duration={data['latency_seconds']:.2f}s",
            ]
            for key in ("input_tokens", "output_tokens", "stop_reason", "tool_calls"):
                if data.get(key) is not None:
                    fields.append(f"{key}={data[key]}")
            print(f"[{label}] {' | '.join(fields)}", file=self.stream)
            return
        if event["event_type"] == "tool.started":
            tool_call = _highlight_tool_call(data["tool_name"], self.stream)
            arguments = data["arguments"]["summary"]
            suffix = f" {arguments}" if arguments else ""
            print(f"{tool_call}{suffix}", file=self.stream)
            return
        if event["event_type"] == "permission.allowed":
            return
        if event["event_type"] in {"permission.required", "permission.denied"}:
            description = data["description"]["summary"]
            print(f"[{label}]", file=self.stream)
            print(description, file=self.stream)
            print("Reason:", file=self.stream)
            print(data["message"], file=self.stream)
            return
        if event["event_type"] in {"tool.completed", "tool.failed"}:
            print(f"[Tool Result] {data['display']}", file=self.stream)
            return
        detail = data.get("message") or data.get("status") or data.get("tool_name") or ""
        print(f"[{label}] {detail}".rstrip(), file=self.stream)


def _usage(response: Any) -> tuple[int | None, int | None]:
    usage = _value(response, "usage")
    if usage is None:
        return None, None
    input_tokens = sum(
        _value(usage, field, 0) or 0
        for field in (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    )
    return input_tokens, _value(usage, "output_tokens")


def _text_from(content: list[Any]) -> str:
    return "\n".join(
        str(_value(block, "text", ""))
        for block in content
        if _value(block, "type") == "text" and _value(block, "text") is not None
    ).strip()


def _validate_tool_use(block: Any) -> tuple[str, str, dict[str, Any]]:
    tool_id = _value(block, "id")
    name = _value(block, "name")
    tool_input = _value(block, "input")
    if not isinstance(tool_id, str) or not tool_id:
        raise ValueError("tool_use is missing a valid id")
    if not isinstance(name, str) or not name:
        raise ValueError("tool_use is missing a valid name")
    if not isinstance(tool_input, dict):
        raise ValueError("tool_use input must be an object")
    return tool_id, name, tool_input


ToolResult = tuple[str, bool, str | None]


def _error(code: str, message: str) -> ToolResult:
    return f"{code}: {message}", True, code


def _required_string(tool_input: dict[str, Any], name: str) -> tuple[str | None, ToolResult | None]:
    value = tool_input.get(name)
    if not isinstance(value, str) or not value:
        return None, _error("INVALID_TOOL_INPUT", f"'{name}' must be a non-empty string")
    return value, None


def _workspace_path(raw_path: str) -> tuple[Path | None, ToolResult | None]:
    path = Path(raw_path)
    if path.is_absolute():
        return None, _error("PATH_OUTSIDE_WORKSPACE", "absolute paths are not allowed")
    root = WORKSPACE_ROOT.resolve()
    candidate = (root / path).resolve()
    if candidate != root and root not in candidate.parents:
        return None, _error("PATH_OUTSIDE_WORKSPACE", f"path escapes workspace: {raw_path}")
    return candidate, None


def _relative(path: Path) -> str:
    return path.relative_to(WORKSPACE_ROOT.resolve()).as_posix() or "."


def run_read_file(tool_input: dict[str, Any]) -> ToolResult:
    raw_path, error = _required_string(tool_input, "path")
    if error:
        return error
    path, error = _workspace_path(raw_path)
    if error:
        return error
    if not path.exists():
        return _error("FILE_NOT_FOUND", f"file does not exist: {raw_path}")
    if not path.is_file():
        return _error("NOT_A_FILE", f"path is not a file: {raw_path}")
    try:
        return path.read_text(encoding="utf-8"), False, None
    except UnicodeDecodeError:
        return _error("FILE_NOT_TEXT", f"file is not valid UTF-8 text: {raw_path}")
    except OSError as exc:
        return _error("FILE_READ_ERROR", str(exc))


def run_write_file(tool_input: dict[str, Any]) -> ToolResult:
    raw_path, error = _required_string(tool_input, "path")
    if error:
        return error
    content = tool_input.get("content")
    if not isinstance(content, str):
        return _error("INVALID_TOOL_INPUT", "'content' must be a string")
    path, error = _workspace_path(raw_path)
    if error:
        return error
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return _error("FILE_WRITE_ERROR", str(exc))
    return f"Wrote {len(content)} characters to {_relative(path)}", False, None


def run_edit_file(tool_input: dict[str, Any]) -> ToolResult:
    raw_path, error = _required_string(tool_input, "path")
    if error:
        return error
    old_text, error = _required_string(tool_input, "old_text")
    if error:
        return error
    new_text = tool_input.get("new_text")
    if not isinstance(new_text, str):
        return _error("INVALID_TOOL_INPUT", "'new_text' must be a string")
    path, error = _workspace_path(raw_path)
    if error:
        return error
    if not path.exists():
        return _error("FILE_NOT_FOUND", f"file does not exist: {raw_path}")
    if not path.is_file():
        return _error("NOT_A_FILE", f"path is not a file: {raw_path}")
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return _error("FILE_NOT_TEXT", f"file is not valid UTF-8 text: {raw_path}")
    except OSError as exc:
        return _error("FILE_READ_ERROR", str(exc))
    occurrences = content.count(old_text)
    if occurrences == 0:
        return _error("EDIT_TARGET_NOT_FOUND", "old_text was not found")
    if occurrences != 1:
        return _error("EDIT_TARGET_NOT_UNIQUE", f"old_text matched {occurrences} times")
    try:
        path.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
    except OSError as exc:
        return _error("FILE_WRITE_ERROR", str(exc))
    return f"Edited {_relative(path)}", False, None


def run_glob(tool_input: dict[str, Any]) -> ToolResult:
    pattern, error = _required_string(tool_input, "pattern")
    if error:
        return error
    pattern_path = Path(pattern)
    if pattern_path.is_absolute() or ".." in pattern_path.parts:
        return _error("PATH_OUTSIDE_WORKSPACE", f"glob pattern escapes workspace: {pattern}")
    try:
        matches = sorted(
            _relative(path.resolve())
            for path in WORKSPACE_ROOT.glob(pattern)
            if path.resolve() == WORKSPACE_ROOT.resolve()
            or WORKSPACE_ROOT.resolve() in path.resolve().parents
        )
    except (OSError, ValueError) as exc:
        return _error("GLOB_ERROR", str(exc))
    shown = matches[:MAX_SEARCH_RESULTS]
    if not shown:
        return "No matches", False, None
    output = "\n".join(shown)
    if len(matches) > len(shown):
        output += f"\n[results truncated: showing {len(shown)} of {len(matches)}]"
    return output, False, None


def run_grep(tool_input: dict[str, Any]) -> ToolResult:
    pattern, error = _required_string(tool_input, "pattern")
    if error:
        return error
    raw_path = tool_input.get("path", ".")
    if not isinstance(raw_path, str) or not raw_path:
        return _error("INVALID_TOOL_INPUT", "'path' must be a non-empty string")
    target, error = _workspace_path(raw_path)
    if error:
        return error
    if not target.exists():
        return _error("FILE_NOT_FOUND", f"path does not exist: {raw_path}")
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return _error("INVALID_PATTERN", str(exc))
    files = [target] if target.is_file() else sorted(path for path in target.rglob("*") if path.is_file())
    matches: list[str] = []
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(lines, 1):
            if regex.search(line):
                matches.append(f"{_relative(path)}:{line_number}:{line}")
                if len(matches) >= MAX_SEARCH_RESULTS:
                    return "\n".join(matches) + f"\n[results truncated at {MAX_SEARCH_RESULTS} matches]", False, None
    return ("\n".join(matches) if matches else "No matches"), False, None


def run_bash(tool_input: dict[str, Any]) -> ToolResult:
    command, error = _required_string(tool_input, "command")
    if error:
        return error
    timeout = tool_input.get("timeout_seconds", SHELL_TIMEOUT_SECONDS)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= MAX_SHELL_TIMEOUT_SECONDS:
        return _error("INVALID_TOOL_INPUT", f"'timeout_seconds' must be greater than 0 and at most {MAX_SHELL_TIMEOUT_SECONDS:g}")
    try:
        completed = subprocess.run(
            command,
            cwd=WORKSPACE_ROOT,
            shell=True,
            text=True,
            capture_output=True,
            timeout=float(timeout),
        )
    except subprocess.TimeoutExpired as exc:
        partial = "".join(part for part in (exc.stdout or "", exc.stderr or "") if isinstance(part, str))
        detail = f"command exceeded {float(timeout):g}s"
        if partial:
            detail += f"; partial output: {partial[:TOOL_OUTPUT_LIMIT]}"
        return _error("SHELL_TIMEOUT", detail)
    except OSError as exc:
        return _error("SHELL_EXECUTION_ERROR", str(exc))
    output = completed.stdout + completed.stderr
    if not output:
        output = "(no output)"
    if len(output) > TOOL_OUTPUT_LIMIT:
        output = output[:TOOL_OUTPUT_LIMIT] + f"\n[output truncated from {len(output)} characters]"
    if completed.returncode != 0:
        return _error("SHELL_NONZERO_EXIT", f"exit_code={completed.returncode}\n{output}")
    return f"exit_code=0\n{output}", False, None


def render_todos(todos: list[dict[str, str]]) -> str:
    """Render every original Todo content unchanged, adding only its status symbol."""
    return "\n".join(f"{TODO_SYMBOLS[todo['status']]} {todo['content']}" for todo in todos)


def run_todo_write(tool_input: dict[str, Any]) -> ToolResult:
    """Validate a complete TodoList, then atomically replace the in-memory state."""
    global TODO_STATE

    if set(tool_input) != {"todos"}:
        return _error("INVALID_TOOL_INPUT", "input must contain only 'todos'")
    todos = tool_input.get("todos")
    if not isinstance(todos, list):
        return _error("INVALID_TOOL_INPUT", "'todos' must be an array")
    if len(todos) > MAX_TODOS:
        return _error("INVALID_TOOL_INPUT", f"'todos' must contain at most {MAX_TODOS} items")

    validated: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    in_progress_count = 0
    required_fields = {"id", "content", "status"}
    for index, todo in enumerate(todos):
        if not isinstance(todo, dict):
            return _error("INVALID_TOOL_INPUT", f"todos[{index}] must be an object")
        if set(todo) != required_fields:
            return _error(
                "INVALID_TOOL_INPUT",
                f"todos[{index}] must contain only id, content, and status",
            )
        todo_id = todo["id"]
        content = todo["content"]
        status = todo["status"]
        if not isinstance(todo_id, str) or not todo_id.strip():
            return _error("INVALID_TOOL_INPUT", f"todos[{index}].id must be a non-empty string")
        if todo_id in seen_ids:
            return _error("INVALID_TOOL_INPUT", f"duplicate todo id: {todo_id}")
        if not isinstance(content, str) or not content.strip():
            return _error("INVALID_TOOL_INPUT", f"todos[{index}].content must be a non-empty string")
        if status not in TODO_STATUSES:
            return _error(
                "INVALID_TOOL_INPUT",
                f"todos[{index}].status must be pending, in_progress, or completed",
            )
        if status == "in_progress":
            in_progress_count += 1
            if in_progress_count > 1:
                return _error("INVALID_TOOL_INPUT", "at most one todo may be in_progress")
        seen_ids.add(todo_id)
        validated.append({"id": todo_id, "content": content, "status": status})

    TODO_STATE = validated
    return render_todos(TODO_STATE), False, None


TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], ToolResult]] = {
    "bash": run_bash,
    "read_file": run_read_file,
    "write_file": run_write_file,
    "edit_file": run_edit_file,
    "glob": run_glob,
    "grep": run_grep,
    "todo_write": run_todo_write,
}


def contains_destructive_command(command: str) -> bool:
    """Match the small explicit set of system-level or catastrophic commands."""
    patterns = (
        r"(?:^|[;&|]\s*)rm\b(?=[^;&|]*(?:-[^\s]*r[^\s]*f|-[^\s]*f[^\s]*r))[^;&|]*\s/(?:\s|$)",
        r"(?:^|[;&|]\s*)sudo(?:\s|$)",
        r"(?:^|[;&|]\s*)(?:shutdown|reboot)(?:\s|$)",
        r"(?:^|[;&|]\s*)mkfs(?:\.[A-Za-z0-9_-]+)?(?:\s|$)",
        r"(?:^|[;&|]\s*)dd\b[^;&|]*\bif\s*=",
        r">>?\s*/dev/(?!null(?=\s|$|[;&|]))",
        r"(?:^|[;&|]\s*)git\s+reset\s+--hard(?:\s|$)",
        r"(?:^|[;&|]\s*)git\s+clean(?:\s+[^;&|]*)?\s-[^\s;&|]*f",
    )
    return any(re.search(pattern, command) for pattern in patterns)


def contains_side_effect_command(command: str) -> bool:
    """Match representative file, repository, and environment mutations."""
    patterns = (
        r"(?:^|[;&|]\s*)(?:rm|mv|cp|mkdir|touch|chmod)(?:\s|$)",
        r"(?:^|[;&|]\s*)(?:python(?:3(?:\.\d+)?)?\s+-m\s+)?pip(?:3(?:\.\d+)?)?\s+(?:install|uninstall)(?:\s|$)",
        r"(?:^|[;&|]\s*)npm\s+(?:install|uninstall)(?:\s|$)",
        r"(?:^|[;&|]\s*)git\s+(?:checkout|switch|restore|reset|clean|commit)(?:\s|$)",
    )
    if any(re.search(pattern, command) for pattern in patterns):
        return True
    without_safe_redirections = re.sub(
        r"(?:\d*)>>?\s*(?:/dev/null(?=\s|$|[;&|])|&\d+)", "", command
    )
    return re.search(r"(?:\d*)>>?", without_safe_redirections) is not None


def path_is_outside_workspace(tool_input: dict[str, Any]) -> bool:
    """Match valid-looking file paths that resolve outside the workspace."""
    raw_path = tool_input.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return False
    path = Path(raw_path)
    if path.is_absolute():
        return True
    root = WORKSPACE_ROOT.resolve()
    candidate = (root / path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return True
    return False


def path_is_sensitive(tool_input: dict[str, Any]) -> bool:
    """Match the small explicit set of sensitive workspace path names."""
    raw_path = tool_input.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return False
    for part in Path(raw_path).parts:
        normalized = part.lower()
        if normalized == ".env" or normalized.startswith(".env."):
            return True
        if re.fullmatch(r"(?:credentials?|secrets?)(?:[._-].+)?", normalized):
            return True
    return False


DENY_RULES: list[dict[str, Any]] = [
    {
        "tools": ["bash"],
        "matcher": lambda args: contains_destructive_command(
            args.get("command", "")
        ),
        "message": "Potentially destructive command",
        "describe": lambda args: f"Blocked command: {args.get('command')}",
    },
    {
        "tools": ["write_file", "edit_file"],
        "matcher": path_is_outside_workspace,
        "message": "File path is outside the workspace",
        "describe": lambda args: f"Blocked path: {args.get('path')}",
    },
]

ASK_RULES: list[dict[str, Any]] = [
    {
        "tools": ["write_file", "edit_file"],
        "matcher": path_is_sensitive,
        "message": "Sensitive file modification requires approval",
        "describe": lambda args: f"Modify sensitive path: {args.get('path')}",
    },
    {
        "tools": ["bash"],
        "matcher": lambda args: contains_side_effect_command(
            args.get("command", "")
        ),
        "message": "Command may modify files, repository state, or the development environment",
        "describe": lambda args: f"Execute command: {args.get('command')}",
    },
]


def _permission_result(
    action: str,
    tool_name: str,
    tool_input: dict[str, Any],
    rule: dict[str, Any] | None = None,
    message: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    if rule is not None:
        message = str(rule["message"])
        description = str(rule["describe"](tool_input))
    return {
        "action": action,
        "tool_name": tool_name,
        "message": message,
        "description": description,
    }


def check_permission(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Evaluate every tool call using deny-first, ask-second rule collections."""
    for action, rules in (("deny", DENY_RULES), ("ask", ASK_RULES)):
        for rule in rules:
            if tool_name not in rule["tools"]:
                continue
            try:
                if rule["matcher"](tool_input):
                    return _permission_result(action, tool_name, tool_input, rule=rule)
            except Exception as exc:
                return _permission_result(
                    "deny",
                    tool_name,
                    tool_input,
                    message=f"Permission rule evaluation failed: {redact(str(exc))}",
                    description=f"Blocked tool call: {tool_name}",
                )
    return _permission_result("allow", tool_name, tool_input)


def _authorize_tool(
    permission: dict[str, Any],
    logger: EventLogger,
    run_id: str,
    tool_id: str,
    confirmation_fn: Callable[[str], str],
) -> bool:
    action = permission["action"]
    event_data = {
        "action": action,
        "tool_name": permission["tool_name"],
        "tool_use_id": tool_id,
        "description": summarize(permission.get("description") or ""),
        "message": redact(permission.get("message") or ""),
    }
    if action == "allow":
        logger.emit("permission.allowed", run_id, **event_data)
        return True
    if action == "deny":
        logger.emit("permission.denied", run_id, **event_data)
        return False

    logger.emit("permission.required", run_id, **event_data)
    print("Allow? (y/N) ", end="", file=logger.stream, flush=True)
    try:
        answer = confirmation_fn("")
    except (EOFError, KeyboardInterrupt):
        answer = ""
    allowed = answer.strip().lower() in {"y", "yes"}
    if not allowed:
        permission["message"] = f"User denied permission: {permission['message']}"
        event_data["message"] = permission["message"]
    logger.emit(
        "permission.allowed" if allowed else "permission.denied",
        run_id,
        **{**event_data, "action": "allow" if allowed else "deny"},
    )
    return allowed


def _execute_tool(name: str, tool_input: dict[str, Any]) -> ToolResult:
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return _error("UNKNOWN_TOOL", f"unknown tool: {name}")
    try:
        return handler(tool_input)
    except Exception as exc:
        return _error("TOOL_EXECUTION_ERROR", redact(str(exc)))


def _observe_pre_tool_use(context: dict[str, Any]) -> None:
    """Preserve V03 Tool Call observability at the PreToolUse boundary."""
    name = context["tool_name"]
    tool_input = context["tool_input"]
    call_display, arg_summary = _tool_call_summary(name, tool_input)
    context["logger"].emit(
        "tool.started",
        context["run_id"],
        tool_name=name,
        tool_use_id=context["tool_use_id"],
        arguments=arg_summary,
        display=call_display,
    )


def _authorize_pre_tool_use(context: dict[str, Any]) -> None:
    """Apply the unchanged V03 Permission Pipeline through PreToolUse."""
    permission = check_permission(context["tool_name"], context["tool_input"])
    context["permission"] = permission
    if _authorize_tool(
        permission,
        context["logger"],
        context["run_id"],
        context["tool_use_id"],
        context["confirmation_fn"],
    ):
        return
    context["tool_result"] = _error(
        "PERMISSION_DENIED",
        f"{permission['message'] or 'User denied permission'}\n"
        f"Operation: {permission['description'] or context['tool_name']}",
    )


def _observe_post_tool_use(context: dict[str, Any]) -> None:
    """Preserve V03 Tool Result observability at the PostToolUse boundary."""
    result, is_error, error_code = context["tool_result"]
    result_display, result_summary = _tool_result_summary(
        context["tool_name"],
        context["tool_input"],
        result,
        is_error,
        error_code,
        context["duration_seconds"],
    )
    event_type = "tool.failed" if is_error else "tool.completed"
    context["logger"].emit(
        event_type,
        context["run_id"],
        tool_name=context["tool_name"],
        tool_use_id=context["tool_use_id"],
        duration_seconds=context["duration_seconds"],
        success=not is_error,
        result=result_summary,
        error_code=error_code,
        error=result_summary.get("error") if is_error else None,
        display=result_display,
    )


# Registration order intentionally preserves V03's CLI/JSONL event order.

register_hook("PreToolUse", _observe_pre_tool_use) # 作用：把这次工具调用打点记录（tool.started 事件，含参数摘要、脱敏）。它只做记录
register_hook("PreToolUse", _authorize_pre_tool_use)
register_hook("PostToolUse", _observe_post_tool_use)# 它只负责在生命周期终点把结果数据打包并 emit 出去。不包含显示到命令行


def agent_loop(
    messages: list[dict[str, Any]],
    logger: EventLogger,
    run_id: str,
    max_rounds: int = MAX_ROUNDS,
    confirmation_fn: Callable[[str], str] = input,
) -> dict[str, Any]:
    """Call the model until it produces no tool_use block or the run fails."""
    if client is None or MODEL is None:
        return {"status": "failed", "final_text": "", "rounds": 0, "error": "CONFIG_ERROR"}

    for round_number in range(1, max_rounds + 1):
        logger.emit("llm.started", run_id, model=MODEL, round=round_number, attempt=0)
        started = time.perf_counter()
        try:
            response = client.messages.create(
                model=MODEL,
                system=SYSTEM,
                messages=messages,
                tools=TOOLS,
                max_tokens=MAX_TOKENS,
            )
        except Exception as exc:
            latency = time.perf_counter() - started
            error = redact(str(exc))
            logger.emit("llm.failed", run_id, model=MODEL, round=round_number, attempt=0, latency_seconds=latency, error_type=type(exc).__name__, error=error, message=error)
            return {"status": "failed", "final_text": "", "rounds": round_number, "error": "MODEL_CALL_ERROR"}

        latency = time.perf_counter() - started
        try:
            content = _as_message_content(_value(response, "content"))
            if not content:
                raise ValueError("response.content is empty")
            input_tokens, output_tokens = _usage(response)
            tool_call_count = sum(1 for block in content if _value(block, "type") == "tool_use")
            logger.emit(
                "llm.completed",
                run_id,
                model=MODEL,
                round=round_number,
                attempt=0,
                latency_seconds=latency,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                stop_reason=_value(response, "stop_reason"),
                tool_calls=tool_call_count,
                **summarize(_text_from(content)),
            )
            messages.append({"role": "assistant", "content": content})
            tool_blocks = [block for block in content if _value(block, "type") == "tool_use"]
            if not tool_blocks:
                final_text = _text_from(content)
                if not final_text:
                    raise ValueError("response has neither tool_use nor visible text")
                return {"status": "completed", "final_text": final_text, "rounds": round_number, "error": None}

            tool_results = []
            for block in tool_blocks:
                tool_id, name, tool_input = _validate_tool_use(block)
                tool_started = time.perf_counter()
                hook_context = {
                    "logger": logger,
                    "run_id": run_id,
                    "tool_use_id": tool_id,
                    "tool_name": name,
                    "tool_input": tool_input,
                    "confirmation_fn": confirmation_fn,
                }
                try:
                    trigger_hooks("PreToolUse", hook_context)
                except Exception as exc:
                    hook_context["tool_result"] = _error(
                        "HOOK_ERROR", f"PreToolUse hook failed: {redact(str(exc))}"
                    )
                if "tool_result" not in hook_context:
                    hook_context["tool_result"] = _execute_tool(name, tool_input)
                hook_context["duration_seconds"] = time.perf_counter() - tool_started
                try:
                    trigger_hooks("PostToolUse", hook_context)
                except Exception as exc:
                    print(
                        f"[Hook Warning] PostToolUse hook failed: {redact(str(exc))}",
                        file=sys.stderr,
                    )
                result, is_error, _error_code = hook_context["tool_result"]
                tool_result = {"type": "tool_result", "tool_use_id": tool_id, "content": result}
                if is_error:
                    tool_result["is_error"] = True
                tool_results.append(tool_result)
            messages.append({"role": "user", "content": tool_results})
        except (TypeError, ValueError) as exc:
            error = redact(str(exc))
            logger.emit("llm.failed", run_id, model=MODEL, round=round_number, attempt=0, latency_seconds=latency, error_type="PROTOCOL_ERROR", error=error, message=error)
            return {"status": "failed", "final_text": "", "rounds": round_number, "error": "PROTOCOL_ERROR"}

    return {"status": "failed", "final_text": "", "rounds": max_rounds, "error": "MAX_ROUNDS_EXCEEDED"}


def run_once(
    messages: list[dict[str, Any]],
    user_text: str,
    logger: EventLogger,
    confirmation_fn: Callable[[str], str] = input,
) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    messages.append({"role": "user", "content": user_text})
    logger.emit("run.started", run_id, task=summarize(user_text), status="started")
    result = agent_loop(messages, logger, run_id, confirmation_fn=confirmation_fn)
    event_type = "run.completed" if result["status"] == "completed" else "run.failed"
    final_summary = summarize(result["final_text"])
    logger.emit(event_type, run_id, status=result["status"], rounds=result["rounds"], error=result["error"], final_result=final_summary, message=final_summary["summary"] or result["error"])
    return {**result, "run_id": run_id}


def cli(input_fn: Any = input, output: Any = None) -> int:
    output = output or sys.stdout
    try:
        configure_model()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    session_id = str(uuid.uuid4())
    logger = EventLogger(session_id, LOG_DIR / f"{session_id}.jsonl", output)
    messages: list[dict[str, Any]] = []
    logger.emit("session.started", status="started")
    exit_status = "completed"
    try:
        while True:
            try:
                user_text = input_fn("You> ")
            except EOFError:
                break
            if not user_text.strip() or user_text.strip().lower() in {"exit", "quit"}:
                break
            result = run_once(messages, user_text, logger, confirmation_fn=input_fn)
            if result["final_text"]:
                print(f"Assistant> {result['final_text']}", file=output)
    except KeyboardInterrupt:
        exit_status = "interrupted"
        print("\nSession interrupted.", file=output)
    finally:
        logger.emit("session.ended", status=exit_status)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
