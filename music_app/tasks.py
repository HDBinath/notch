"""Small, local, persistent checklist for the notch."""

from __future__ import annotations

import json
import os
import platform
import tempfile
import uuid
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    completed: bool = False


def default_tasks_path() -> Path:
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "Notch Music" / "tasks.json"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Notch Music" / "tasks.json"
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "notch-music" / "tasks.json"


class TaskStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_tasks_path()
        self.tasks = self._load()

    @property
    def completed_count(self) -> int:
        return sum(task.completed for task in self.tasks)

    def _load(self) -> list[Task]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("tasks"), list):
                raise ValueError("Invalid task file")
            tasks = []
            seen = set()
            for item in data["tasks"]:
                if (not isinstance(item, dict) or not isinstance(item.get("id"), str)
                        or not isinstance(item.get("title"), str)
                        or not isinstance(item.get("completed"), bool)):
                    raise ValueError("Invalid task")
                title = item["title"].strip()
                if not title or len(title) > 200 or not item["id"] or item["id"] in seen:
                    raise ValueError("Invalid task")
                seen.add(item["id"])
                tasks.append(Task(item["id"], title, item["completed"]))
            return tasks
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            # Preserve the unreadable file for recovery instead of overwriting it.
            try:
                self.path.replace(self.path.with_name(f"tasks.corrupt-{uuid.uuid4().hex}.json"))
            except OSError:
                pass
            return []

    def _save(self, tasks: list[Task]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "tasks": [vars(task) for task in tasks]}
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent,
                                             prefix="tasks-", suffix=".tmp", delete=False) as stream:
                temp_path = Path(stream.name)
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def _commit(self, tasks: list[Task]) -> None:
        self._save(tasks)
        self.tasks = tasks

    @staticmethod
    def _title(value: str) -> str:
        title = value.strip()
        if not title or len(title) > 200:
            raise ValueError("Task title must be 1–200 characters.")
        return title

    def add(self, title: str) -> Task:
        task = Task(uuid.uuid4().hex, self._title(title))
        self._commit([*self.tasks, task])
        return task

    def edit(self, task_id: str, title: str) -> None:
        title = self._title(title)
        if not any(task.id == task_id for task in self.tasks):
            raise KeyError(task_id)
        self._commit([replace(task, title=title) if task.id == task_id else task for task in self.tasks])

    def toggle(self, task_id: str) -> None:
        if not any(task.id == task_id for task in self.tasks):
            raise KeyError(task_id)
        self._commit([replace(task, completed=not task.completed) if task.id == task_id else task
                      for task in self.tasks])

    def delete(self, task_id: str) -> None:
        if not any(task.id == task_id for task in self.tasks):
            raise KeyError(task_id)
        self._commit([task for task in self.tasks if task.id != task_id])
