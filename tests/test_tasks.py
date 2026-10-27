import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from music_app.notch import NotchView
from music_app.tasks import TaskStore


TEST_DIR = Path(__file__).resolve().parent


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=TEST_DIR)
        self.path = Path(self.folder.name) / "tasks.json"
        self.store = TaskStore(self.path)

    def tearDown(self):
        self.folder.cleanup()

    def test_add_edit_toggle_delete_and_restart(self):
        first = self.store.add("  First task  ")
        second = self.store.add("Second task")
        self.assertEqual([task.title for task in self.store.tasks], ["First task", "Second task"])
        self.store.toggle(first.id)
        self.store.edit(second.id, "Updated task")
        reloaded = TaskStore(self.path)
        self.assertEqual([task.id for task in reloaded.tasks], [first.id, second.id])
        self.assertEqual(reloaded.completed_count, 1)
        self.assertEqual(reloaded.tasks[1].title, "Updated task")
        reloaded.toggle(first.id)
        self.assertEqual(reloaded.completed_count, 0)
        reloaded.delete(second.id)
        self.assertEqual([task.id for task in TaskStore(self.path).tasks], [first.id])

    def test_invalid_file_is_preserved_and_empty_list_recovers(self):
        self.path.write_text("not json", encoding="utf-8")
        recovered = TaskStore(self.path)
        self.assertEqual(recovered.tasks, [])
        backups = list(self.path.parent.glob("tasks.corrupt-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "not json")
        recovered.add("New task")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["tasks"][0]["title"], "New task")

    def test_invalid_titles_and_missing_ids_do_not_change_file(self):
        task = self.store.add("Keep")
        saved = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.add("  ")
        with self.assertRaises(ValueError):
            self.store.edit(task.id, "x" * 201)
        with self.assertRaises(KeyError):
            self.store.delete("missing")
        self.assertEqual(self.path.read_bytes(), saved)

    def test_failed_save_keeps_memory_and_file_unchanged(self):
        self.store.add("Keep")
        saved = self.path.read_bytes()
        with patch.object(self.store, "_save", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.store.add("Unsaved")
        self.assertEqual([task.title for task in self.store.tasks], ["Keep"])
        self.assertEqual(self.path.read_bytes(), saved)


class TodoViewTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=TEST_DIR)
        self.store = TaskStore(Path(self.folder.name) / "tasks.json")
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.folder.cleanup()
            self.skipTest(f"Tk display unavailable: {exc}")
        self.root.withdraw()
        self.view = NotchView(self.root, lambda _action: None, lambda _stage: None, task_store=self.store)
        self.view.size = self.view.SIZES[self.view.FULL]
        self.view.stage = self.view.FULL
        self.view.target_stage = self.view.FULL
        self.view._position(*self.view.size)
        self.view._draw()
        self.root.deiconify()
        self.root.lift()
        self.root.update()

    def tearDown(self):
        if hasattr(self, "root"):
            self.view.close()
            self.root.destroy()
        if hasattr(self, "folder"):
            self.folder.cleanup()

    def test_page_switch_and_controls(self):
        self.assertEqual(self.view.page, "music")
        self.assertEqual(self.view._control_at(484, 168), "page_todo")
        self.view._key_page("todo")
        self.assertEqual(self.view.page, "todo")
        self.assertEqual(self.view._control_at(25, 99), "page_music")
        self.view._key_page("music")
        self.assertEqual(self.view.page, "music")
        self.root.focus_force()
        self.root.event_generate("<Right>")
        self.root.update()
        self.assertEqual(self.view.page, "todo")
        self.root.event_generate("<Left>")
        self.root.update()
        self.assertEqual(self.view.page, "music")

    def test_clicking_check_updates_count_and_keeps_row_position(self):
        first = self.store.add("First")
        self.store.add("Second")
        self.view._switch_page("todo")
        self.view._click(SimpleNamespace(x=254, y=63))
        self.assertEqual(self.store.completed_count, 1)
        self.assertEqual(self.store.tasks[0].id, first.id)
        self.assertEqual(self.view._control_at(300, 63), f"task_select:{first.id}")

    def test_scroll_and_long_title(self):
        for number in range(5):
            self.store.add(f"Task {number} " + "long text " * 9)
        self.view._switch_page("todo")
        self.assertTrue(self.view._fit(self.store.tasks[0].title, self.view._task_font, 164).endswith("…"))
        self.view._wheel(SimpleNamespace(delta=-120, num=None))
        self.assertEqual(self.view._task_scroll, 1)
        self.assertEqual(self.view._control_at(300, 50), f"task_select:{self.store.tasks[1].id}")
        self.view._wheel(SimpleNamespace(delta=120, num=None))
        self.assertEqual(self.view._task_scroll, 0)

    def test_editor_add_and_edit(self):
        self.view._switch_page("todo")
        self.view._open_task_editor()
        self.assertIsNotNone(self.view._task_editor)
        editor = self.view._task_editor
        entry = next(widget for widget in editor.winfo_children()[0].winfo_children() if isinstance(widget, tk.Entry))
        entry.insert(0, "New task")
        buttons = next(widget for widget in editor.winfo_children()[0].winfo_children() if isinstance(widget, tk.Frame))
        next(widget for widget in buttons.winfo_children() if isinstance(widget, tk.Button) and widget.cget("text") == "Save").invoke()
        self.assertEqual(len(self.store.tasks), 1)
        self.assertIsNone(self.view._task_editor)
        task = self.store.tasks[0]
        self.view._open_task_editor(task.id)
        editor = self.view._task_editor
        entry = next(widget for widget in editor.winfo_children()[0].winfo_children() if isinstance(widget, tk.Entry))
        entry.delete(0, "end")
        entry.insert(0, "Edited task")
        buttons = next(widget for widget in editor.winfo_children()[0].winfo_children() if isinstance(widget, tk.Frame))
        next(widget for widget in buttons.winfo_children() if isinstance(widget, tk.Button) and widget.cget("text") == "Save").invoke()
        self.assertEqual(self.store.tasks[0].title, "Edited task")

    def test_editor_delete(self):
        task = self.store.add("Delete me")
        self.view._switch_page("todo")
        self.view._open_task_editor(task.id)
        self.assertIsNone(self.view._collapse_id)
        editor = self.view._task_editor
        buttons = next(widget for widget in editor.winfo_children()[0].winfo_children() if isinstance(widget, tk.Frame))
        delete = next(widget for widget in buttons.winfo_children()
                      if isinstance(widget, tk.Button) and widget.cget("text") == "Delete")
        with patch("music_app.notch.messagebox.askyesno", return_value=True):
            delete.invoke()
        self.assertEqual(self.store.tasks, [])
        self.assertIsNone(self.view._task_editor)


if __name__ == "__main__":
    unittest.main()
