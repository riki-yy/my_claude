"""Validated filesystem store for V08 persistent memories."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


MEMORY_TYPES = {"user", "feedback", "project", "reference"}
MAX_MEMORY_BODY_CHARS = 12_000
MAX_TITLE_CHARS = 120
MAX_SUMMARY_CHARS = 240
INDEX_HEADER = "# Memory Index\n\n"
ID_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9_-]{0,63})\Z")


class MemoryStoreError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class MemoryStore:
    """A small index plus one independently validated Markdown file per memory."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.index_path = self.root / "MEMORY.md"
        self.snapshots_dir = self.root / ".snapshots"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self._atomic_write(self.index_path, INDEX_HEADER)

    @staticmethod
    def validate_candidate(candidate: Any) -> dict[str, str]:
        required = {"id", "type", "title", "summary", "body"}
        if not isinstance(candidate, dict) or set(candidate) - (required | {"created_at", "updated_at"}):
            raise MemoryStoreError("candidate has unknown fields or is not an object")
        if not required <= set(candidate):
            raise MemoryStoreError("candidate is missing required fields")
        normalized: dict[str, str] = {}
        for field in required:
            value = candidate[field]
            if not isinstance(value, str) or not value.strip():
                raise MemoryStoreError(f"candidate.{field} must be a non-empty string")
            normalized[field] = value.strip()
        if not ID_PATTERN.fullmatch(normalized["id"]):
            raise MemoryStoreError("candidate.id is not a safe stable identifier")
        if normalized["type"] not in MEMORY_TYPES:
            raise MemoryStoreError("candidate.type is not supported")
        if len(normalized["title"]) > MAX_TITLE_CHARS:
            raise MemoryStoreError("candidate.title exceeds its size limit")
        if len(normalized["summary"]) > MAX_SUMMARY_CHARS:
            raise MemoryStoreError("candidate.summary exceeds its size limit")
        if "\n" in normalized["summary"] or "\r" in normalized["summary"]:
            raise MemoryStoreError("candidate.summary must be a single-line description")
        if len(normalized["body"]) > MAX_MEMORY_BODY_CHARS:
            raise MemoryStoreError("candidate.body exceeds its size limit")
        return normalized

    @staticmethod
    def _render_body(entry: dict[str, str]) -> str:
        description = json.dumps(entry["summary"], ensure_ascii=False)
        return (
            "---\n"
            f"name: {entry['id']}\n"
            f"description: {description}\n"
            f"type: {entry['type']}\n"
            "---\n\n"
            f"{entry['body'].strip()}\n"
        )

    @staticmethod
    def _parse_frontmatter_scalar(raw: str, field: str) -> str:
        value = raw.strip()
        if not value:
            raise MemoryStoreError(f"memory frontmatter {field} must be non-empty")
        if value.startswith(('"', "'")):
            if value.startswith("'"):
                if not value.endswith("'") or len(value) < 2:
                    raise MemoryStoreError(f"memory frontmatter {field} has an invalid scalar")
                value = value[1:-1].replace("''", "'")
            else:
                try:
                    decoded = json.loads(value)
                except json.JSONDecodeError as exc:
                    raise MemoryStoreError(f"memory frontmatter {field} has an invalid scalar") from exc
                if not isinstance(decoded, str):
                    raise MemoryStoreError(f"memory frontmatter {field} must be a string")
                value = decoded
        if not value.strip() or "\n" in value or "\r" in value:
            raise MemoryStoreError(f"memory frontmatter {field} must be a non-empty single-line string")
        return value.strip()

    @staticmethod
    def _parse_body(text: str) -> dict[str, str]:
        match = re.fullmatch(r"---\n(.*?)\n---\n\n(.+)\n", text, re.DOTALL)
        if not match:
            raise MemoryStoreError("memory body must contain YAML frontmatter followed by Markdown")
        metadata: dict[str, str] = {}
        for line in match.group(1).splitlines():
            if ":" not in line:
                raise MemoryStoreError("memory frontmatter contains an invalid line")
            key, raw_value = line.split(":", 1)
            key = key.strip()
            if key in metadata:
                raise MemoryStoreError(f"memory frontmatter contains duplicate field: {key}")
            metadata[key] = MemoryStore._parse_frontmatter_scalar(raw_value, key)
        required = {"name", "description", "type"}
        if set(metadata) != required:
            raise MemoryStoreError("memory frontmatter must contain only name, description, and type")
        if not ID_PATTERN.fullmatch(metadata["name"]):
            raise MemoryStoreError("memory frontmatter name is not a safe stable identifier")
        if metadata["type"] not in MEMORY_TYPES:
            raise MemoryStoreError("memory frontmatter type is not supported")
        if len(metadata["description"]) > MAX_SUMMARY_CHARS:
            raise MemoryStoreError("memory frontmatter description exceeds its size limit")
        body = match.group(2).strip()
        if not body:
            raise MemoryStoreError("memory Markdown body must be non-empty")
        if len(body) > MAX_MEMORY_BODY_CHARS:
            raise MemoryStoreError("memory Markdown body exceeds its size limit")
        return {
            "id": metadata["name"],
            "summary": metadata["description"],
            "type": metadata["type"],
            "body": body,
        }

    @staticmethod
    def _render_index(entries: list[dict[str, str]]) -> str:
        if not entries:
            return INDEX_HEADER
        lines = [INDEX_HEADER.rstrip(), ""]
        for entry in sorted(entries, key=lambda item: item["name"]):
            lines.extend((
                f"- name: {entry['name']}",
                f"  description: {entry['description']}",
                f"  type: {entry['type']}",
                "",
            ))
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _parse_index(text: str) -> list[dict[str, str]]:
        if not text.startswith(INDEX_HEADER):
            raise MemoryStoreError("MEMORY.md has an invalid header")
        payload = text[len(INDEX_HEADER):]
        if not payload.strip():
            return []
        entries: list[dict[str, str]] = []
        seen: set[str] = set()
        for block in re.split(r"\n[ \t]*\n", payload.strip()):
            lines = block.splitlines()
            if len(lines) != 3 or not lines[0].startswith("- name: ") or not lines[1].startswith("  description: ") or not lines[2].startswith("  type: "):
                raise MemoryStoreError("MEMORY.md entry must contain only name, description, and type")
            entry = {
                "name": MemoryStore._parse_frontmatter_scalar(lines[0][8:], "name"),
                "description": MemoryStore._parse_frontmatter_scalar(lines[1][15:], "description"),
                "type": MemoryStore._parse_frontmatter_scalar(lines[2][8:], "type"),
            }
            if not ID_PATTERN.fullmatch(entry["name"]):
                raise MemoryStoreError("MEMORY.md name is not a safe stable identifier")
            if len(entry["description"]) > MAX_SUMMARY_CHARS:
                raise MemoryStoreError("MEMORY.md description exceeds its size limit")
            if entry["type"] not in MEMORY_TYPES:
                raise MemoryStoreError("MEMORY.md type is not supported")
            if entry["name"] in seen:
                raise MemoryStoreError("MEMORY.md contains a duplicate name")
            seen.add(entry["name"])
            entries.append(entry)
        return entries

    def read_index(self, *, validate_bodies: bool = False) -> list[dict[str, str]]:
        self.ensure()
        try:
            entries = self._parse_index(self.index_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            raise MemoryStoreError(f"cannot read MEMORY.md: {exc}") from exc
        if validate_bodies:
            for entry in entries:
                body = self.read(entry["name"])
                if body["id"] != entry["name"] or body["summary"] != entry["description"] or body["type"] != entry["type"]:
                    raise MemoryStoreError(f"index/body metadata mismatch for {entry['name']}")
        return entries

    def index_text(self) -> str:
        entries = self.read_index(validate_bodies=False)
        return self._render_index(entries)

    def read(self, memory_id: str) -> dict[str, str]:
        if not isinstance(memory_id, str) or not ID_PATTERN.fullmatch(memory_id):
            raise MemoryStoreError("memory id is unsafe")
        path = self.root / f"{memory_id}.md"
        try:
            entry = self._parse_body(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            raise MemoryStoreError(f"cannot read memory {memory_id}: {exc}") from exc
        if entry["id"] != memory_id:
            raise MemoryStoreError("memory filename and embedded name differ")
        return entry

    def committed(self) -> list[dict[str, str]]:
        entries = self.read_index(validate_bodies=True)
        return [{
            "id": entry["name"],
            "type": entry["type"],
            "title": entry["description"],
            "summary": entry["description"],
            "body": self.read(entry["name"])["body"],
        } for entry in entries]

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".tmp-{path.name}-{os.getpid()}-{next(tempfile._get_candidate_names())}")
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if temporary.read_text(encoding="utf-8") != content:
                raise OSError("atomic write verification failed")
            os.replace(temporary, path)
            _fsync_directory(path.parent)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def _same_semantics(left: dict[str, str], right: dict[str, str]) -> bool:
        normalize = lambda text: " ".join(text.lower().split())
        return left["type"] == right["type"] and (
            normalize(left["body"]) == normalize(right["body"])
            or normalize(left["summary"]) == normalize(right["summary"])
        )

    def store(self, candidate: Any) -> str:
        """Commit one candidate, or return ``skipped`` when already covered."""
        entry = self.validate_candidate(candidate)
        index = self.read_index(validate_bodies=True)
        existing_bodies = {item["name"]: self.read(item["name"]) for item in index}
        if entry["id"] in existing_bodies:
            return "skipped" if self._same_semantics(existing_bodies[entry["id"]], entry) else self._raise_conflict(entry["id"])
        if any(self._same_semantics(existing, entry) for existing in existing_bodies.values()):
            return "skipped"

        body_path = self.root / f"{entry['id']}.md"
        if body_path.exists():
            raise MemoryStoreError(f"uncommitted body already exists: {entry['id']}")
        self._atomic_write(body_path, self._render_body(entry))
        index_entry = {"name": entry["id"], "description": entry["summary"], "type": entry["type"]}
        try:
            self._atomic_write(self.index_path, self._render_index(index + [index_entry]))
        except Exception as exc:
            rollback_error: Exception | None = None
            try:
                self._atomic_write(self.index_path, self._render_index(index))
                body_path.unlink(missing_ok=True)
                for prefix in (f".tmp-{body_path.name}-", f".tmp-{self.index_path.name}-"):
                    for temporary in self.root.iterdir():
                        if temporary.is_file() and temporary.name.startswith(prefix):
                            temporary.unlink()
                _fsync_directory(self.root)
            except Exception as cleanup_exc:
                rollback_error = cleanup_exc
            if rollback_error is not None:
                raise MemoryStoreError(
                    f"index update failed and candidate rollback failed: {entry['id']}: {rollback_error}"
                ) from exc
            raise MemoryStoreError(f"index update failed; candidate body rolled back: {entry['id']}") from exc
        return "added"

    @staticmethod
    def _raise_conflict(memory_id: str) -> str:
        raise MemoryStoreError(f"stable id conflicts with committed memory: {memory_id}")

    def cleanup_uncommitted(self) -> dict[str, int]:
        committed_ids = {entry["name"] for entry in self.read_index(validate_bodies=False)}
        temporary = 0
        orphan = 0
        for path in self.root.iterdir():
            if path.is_file() and path.name.startswith(".tmp-"):
                path.unlink()
                temporary += 1
            elif path.is_file() and path.suffix == ".md" and path.name != "MEMORY.md" and path.stem not in committed_ids:
                path.unlink()
                orphan += 1
        return {"temporary": temporary, "orphan": orphan}

    def consolidate(self, candidates: list[dict[str, Any]], *, keep_snapshots: int = 2) -> Path:
        """Validate a complete replacement store, snapshot, then replace with rollback."""
        validated = [self.validate_candidate(item) for item in candidates]
        if len({item["id"] for item in validated}) != len(validated):
            raise MemoryStoreError("consolidation contains duplicate ids")
        for index, left in enumerate(validated):
            if any(self._same_semantics(left, right) for right in validated[index + 1:]):
                raise MemoryStoreError("consolidation contains semantic duplicates")
        self.committed()
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        snapshot = self.snapshots_dir / stamp
        snapshot.mkdir(parents=True, exist_ok=False)
        shutil.copy2(self.index_path, snapshot / "MEMORY.md")
        for entry in self.read_index():
            shutil.copy2(self.root / f"{entry['name']}.md", snapshot / f"{entry['name']}.md")

        staging = Path(tempfile.mkdtemp(prefix=".consolidate-", dir=self.root))
        try:
            staged = MemoryStore(staging)
            staged.ensure()
            for item in validated:
                if staged.store(item) != "added":
                    raise MemoryStoreError("consolidation candidate was unexpectedly skipped")
            staged.committed()
            old_files = [path for path in self.root.glob("*.md") if path.name != "MEMORY.md"]
            try:
                for path in old_files:
                    path.unlink()
                for path in staging.glob("*.md"):
                    if path.name != "MEMORY.md":
                        os.replace(path, self.root / path.name)
                self._atomic_write(self.index_path, staged.index_path.read_text(encoding="utf-8"))
                self.committed()
            except Exception:
                for path in self.root.glob("*.md"):
                    path.unlink()
                for path in snapshot.glob("*.md"):
                    shutil.copy2(path, self.root / path.name)
                self.committed()
                raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        snapshots = sorted((path for path in self.snapshots_dir.iterdir() if path.is_dir()), reverse=True)
        for old in snapshots[max(0, keep_snapshots):]:
            shutil.rmtree(old)
        return snapshot
