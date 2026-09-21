"""Synchronous Recall, Extract, Store, and low-frequency Consolidate for V08."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from memory_store import MemoryStore, MemoryStoreError


MAX_RECALL = 5
SELECTOR_MAX_TOKENS = 800
EXTRACT_MAX_TOKENS = 3000
EXTRACT_SAFE_INPUT_TOKENS = 96_000
CONSOLIDATE_COUNT_THRESHOLD = 50
CONSOLIDATE_INDEX_CHARS_THRESHOLD = 24_000

SELECTOR_SYSTEM = """Select persistent memories that are certainly useful for the current request.
Return JSON only: {"ids":["id"]}. Select zero to five IDs from the supplied index.
Do not invent IDs, do not repeat IDs, and prefer an empty list when relevance is uncertain."""

EXTRACT_SYSTEM = """Extract only durable cross-session knowledge. Return JSON only as
{"memories":[{"id":"safe-stable-id","type":"user|feedback|project|reference","title":"short title","summary":"short index summary","body":"durable fact"}]}.
Save stable user preferences, confirmed feedback, project conventions or architecture facts,
and reusable references. Ignore current task progress, Todos, one-off paths, full conversation,
large source text, and unconfirmed guesses. Return an empty memories list when nothing qualifies."""

CONSOLIDATE_SYSTEM = """Consolidate the supplied committed memory store using only deduplication,
merging, and pruning. Return JSON only in the same {"memories":[...]} schema. Preserve durable
facts and stable IDs where possible. Do not invent information."""


def _value(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _response_text(response: Any) -> str:
    content = _value(response, "content")
    if not isinstance(content, list):
        raise ValueError("model response content must be a list")
    return "".join(str(_value(block, "text", "")) for block in content if _value(block, "type") == "text")


def _json_response(response: Any) -> dict[str, Any]:
    text = _response_text(response).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("memory model response is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("memory model response must be an object")
    return value


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[\w-]+", text.lower()) if len(token) > 1}


@dataclass
class RecallResult:
    system_context: str
    selected_ids: list[str]
    selector_path: str


class MemoryPipeline:
    def __init__(
        self,
        store: MemoryStore,
        create_message: Callable[..., Any],
        model: str,
        estimate_tokens: Callable[..., int],
        logger: Any,
    ):
        self.store = store
        self.create_message = create_message
        self.model = model
        self.estimate_tokens = estimate_tokens
        self.logger = logger

    def _emit(self, event: str, run_id: str | None, **data: Any) -> None:
        self.logger.emit(event, run_id, **data)

    def _fallback_ids(self, user_text: str, entries: list[dict[str, str]]) -> list[str]:
        query = _tokens(user_text)
        ranked: list[tuple[int, str]] = []
        for entry in entries:
            overlap = len(query & _tokens(" ".join((entry["name"], entry["description"], entry["type"]))))
            if overlap:
                ranked.append((overlap, entry["name"]))
        return [memory_id for _score, memory_id in sorted(ranked, key=lambda item: (-item[0], item[1]))[:MAX_RECALL]]

    def recall(self, user_text: str, run_id: str) -> RecallResult:
        self._emit("memory.recall.started", run_id)
        try:
            entries = self.store.read_index(validate_bodies=False)
        except Exception as exc:
            self._emit("memory.recall.failed", run_id, error_type=type(exc).__name__, message=str(exc))
            return RecallResult("", [], "unavailable")
        selected: list[str]
        path = "llm"
        if not entries:
            selected = []
        else:
            request = {"request": user_text, "memory_index": entries}
            try:
                response = self.create_message(
                    model=self.model,
                    system=SELECTOR_SYSTEM,
                    messages=[{"role": "user", "content": json.dumps(request, ensure_ascii=False)}],
                    max_tokens=SELECTOR_MAX_TOKENS,
                )
                result = _json_response(response)
                if set(result) != {"ids"} or not isinstance(result["ids"], list):
                    raise ValueError("selector response must contain only an ids array")
                selected = result["ids"]
                known = {entry["name"] for entry in entries}
                if (
                    len(selected) > MAX_RECALL
                    or any(not isinstance(item, str) or item not in known for item in selected)
                    or len(set(selected)) != len(selected)
                ):
                    raise ValueError("selector returned unknown, duplicate, invalid, or too many ids")
            except Exception as exc:
                path = "fallback"
                selected = self._fallback_ids(user_text, entries)
                self._emit("memory.selector.failed", run_id, error_type=type(exc).__name__, message=str(exc))

        bodies: list[dict[str, str]] = []
        valid_selected: list[str] = []
        for memory_id in selected:
            try:
                body = self.store.read(memory_id)
                index_entry = next(entry for entry in entries if entry["name"] == memory_id)
                if body["id"] != index_entry["name"] or body["summary"] != index_entry["description"] or body["type"] != index_entry["type"]:
                    raise MemoryStoreError("index/body metadata mismatch")
                bodies.append({**index_entry, "body": body["body"]})
                valid_selected.append(memory_id)
            except Exception as exc:
                self._emit("memory.recall.item_failed", run_id, memory_id=memory_id, error_type=type(exc).__name__, message=str(exc))
        context = ""
        if bodies:
            rendered = "\n\n".join(f"[{item['type']}:{item['name']}] {item['description']}\n{item['body']}" for item in bodies)
            context = "\n\n<PersistentMemory>\n" + rendered + "\n</PersistentMemory>"
        self._emit(
            "memory.recall.completed",
            run_id,
            selector_path=path,
            index_candidates=len(entries),
            selected_count=len(valid_selected),
        )
        return RecallResult(context, valid_selected, path)

    @staticmethod
    def incremental_count(messages: list[dict[str, Any]], cursor: int | None) -> int:
        if cursor is None:
            return len(messages)
        if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < -1 or cursor >= len(messages):
            raise ValueError("last_memory_message_index is outside active messages")
        return len(messages) - cursor - 1

    def extract_and_store(
        self,
        messages: list[dict[str, Any]],
        cursor: int | None,
        run_id: str,
    ) -> dict[str, Any]:
        n = self.incremental_count(messages, cursor)
        self._emit("memory.extract.started", run_id, recent_message_count=n)
        index_text = self.store.index_text()
        prompt = f"""Persistent Memory Index:\n{index_text}\n
Analyze the most recent ~{n} model-visible messages above
and use them to update persistent memory.

You MUST only use content from the last ~{n} messages
as the source of new or updated memories.

Earlier conversation may only be used to:
- understand context
- resolve references
- avoid semantic duplicates

Return candidates using the required JSON schema."""
        request_messages = copy.deepcopy(messages)
        request_messages.append({"role": "user", "content": prompt})
        estimate = self.estimate_tokens(request_messages, system=EXTRACT_SYSTEM, tools=[])
        if estimate >= EXTRACT_SAFE_INPUT_TOKENS:
            raise MemoryStoreError("memory extract request exceeds its safe input budget")
        response = self.create_message(
            model=self.model,
            system=EXTRACT_SYSTEM,
            messages=request_messages,
            max_tokens=EXTRACT_MAX_TOKENS,
        )
        result = _json_response(response)
        if set(result) != {"memories"} or not isinstance(result["memories"], list):
            raise ValueError("extract response must contain only a memories array")
        candidates = result["memories"]
        outcomes: list[str] = []
        for candidate in candidates:
            try:
                outcome = self.store.store(candidate)
            except Exception as exc:
                self._emit(
                    "memory.store.failed",
                    run_id,
                    memory_id=candidate.get("id") if isinstance(candidate, dict) else None,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
                raise
            outcomes.append(outcome)
            self._emit("memory.store." + outcome, run_id, memory_id=candidate.get("id") if isinstance(candidate, dict) else None)
        self._emit(
            "memory.extract.completed",
            run_id,
            recent_message_count=n,
            candidate_count=len(candidates),
            added=outcomes.count("added"),
            skipped=outcomes.count("skipped"),
            estimated_tokens=estimate,
        )
        return {"candidate_count": len(candidates), "outcomes": outcomes, "cursor": len(messages) - 1 if messages else None}

    def should_consolidate(self) -> bool:
        entries = self.store.read_index(validate_bodies=False)
        return len(entries) >= CONSOLIDATE_COUNT_THRESHOLD or len(self.store.index_text()) >= CONSOLIDATE_INDEX_CHARS_THRESHOLD

    def consolidate(self, run_id: str) -> bool:
        if not self.should_consolidate():
            return False
        entries = self.store.committed()
        self._emit("memory.consolidate.triggered", run_id, entry_count=len(entries))
        try:
            response = self.create_message(
                model=self.model,
                system=CONSOLIDATE_SYSTEM,
                messages=[{"role": "user", "content": json.dumps({"memories": entries}, ensure_ascii=False)}],
                max_tokens=EXTRACT_MAX_TOKENS,
            )
            result = _json_response(response)
            if set(result) != {"memories"} or not isinstance(result["memories"], list):
                raise ValueError("consolidate response must contain only a memories array")
            snapshot = self.store.consolidate(result["memories"])
            self._emit("memory.consolidate.snapshot", run_id, snapshot=str(snapshot))
            self._emit("memory.consolidate.committed", run_id, entry_count=len(result["memories"]), snapshot=str(snapshot))
            return True
        except Exception as exc:
            self._emit("memory.consolidate.rollback", run_id, error_type=type(exc).__name__, message=str(exc))
            return False
