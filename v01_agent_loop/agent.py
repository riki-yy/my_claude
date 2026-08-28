"""V01: the smallest useful Agent Loop, with one hard-coded echo tool."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


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
SYSTEM = (
    "You are a concise assistant. You have an echo tool. "
    "Use it when the user explicitly asks you to echo or repeat text with the tool."
)
TOOLS = [
    {
        "name": "echo",
        "description": "Return the supplied text unchanged.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }
]
MAX_TOKENS = 8000
SUMMARY_LIMIT = 240

# Configured by configure_model() for the real CLI. Tests replace `client`
# directly, at the same client.messages.create surface used in production.
client: Any = None
MODEL: str | None = None


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
                with self.log_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
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
        detail = data.get("message") or data.get("status") or data.get("tool_name") or ""
        print(f"[{label}] {detail}".rstrip(), file=self.stream)


def _usage(response: Any) -> tuple[int | None, int | None]:
    usage = _value(response, "usage")
    if usage is None:
        return None, None
    return _value(usage, "input_tokens"), _value(usage, "output_tokens")


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


def _execute_tool(name: str, tool_input: dict[str, Any]) -> tuple[str, bool, str | None]:
    if name != "echo":
        return f"Unknown tool: {name}", True, "UNKNOWN_TOOL"
    text = tool_input.get("text")
    if not isinstance(text, str):
        return "echo requires a string parameter named 'text'", True, "INVALID_TOOL_INPUT"
    return text, False, None


def agent_loop(
    messages: list[dict[str, Any]],
    logger: EventLogger,
    run_id: str,
    max_rounds: int = 8,
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
                arg_summary = summarize(tool_input)
                logger.emit("tool.started", run_id, tool_name=name, tool_use_id=tool_id, arguments=arg_summary, message=name)
                tool_started = time.perf_counter()
                result, is_error, error_code = _execute_tool(name, tool_input)
                duration = time.perf_counter() - tool_started
                result_summary = summarize(result)
                event_type = "tool.failed" if is_error else "tool.completed"
                logger.emit(event_type, run_id, tool_name=name, tool_use_id=tool_id, duration_seconds=duration, success=not is_error, result=result_summary, error_code=error_code, error=result_summary["summary"] if is_error else None, message=result_summary["summary"])
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


def run_once(messages: list[dict[str, Any]], user_text: str, logger: EventLogger) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    messages.append({"role": "user", "content": user_text})
    logger.emit("run.started", run_id, task=summarize(user_text), status="started")
    result = agent_loop(messages, logger, run_id)
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
            result = run_once(messages, user_text, logger)
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
