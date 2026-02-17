"""
Distraction List Editor for AURA

Provides:
- Tkinter UI to manage distraction rules (applications, websites, keywords)
- JSON persistence with atomic writes and import/export
- Integration helpers to apply rules to DistractionClassifier in real-time

Usage:
    from aura.distraction_list_editor import DistractionListEditor, DistractionListStore
    store = DistractionListStore()
    editor = DistractionListEditor(root, store, classifier)
    editor.show()

Data Model (JSON):
{
  "version": 1,
  "categories": ["Social Media", "Entertainment", "Games", "News", "Communication", "Work Whitelist"],
  "entries": [
    {"id": "uuid", "type": "app|website|keyword", "value": "chrome.exe|youtube.com|netflix",
     "category": "Entertainment", "match": "exact|contains|regex", "notes": "", "enabled": true,
     "priority": "high|normal|low"}
  ],
  "updated_at": "ISO-8601 timestamp"
}
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
from tkinter import ttk

try:
    # Optional: if classifier is available, we can integrate directly
    from .classifier import DistractionClassifier  # type: ignore
except Exception:  # pragma: no cover
    DistractionClassifier = None  # type: ignore


DEFAULT_CATEGORIES: List[str] = [
    "Social Media",
    "Entertainment",
    "Games",
    "News",
    "Communication",
    "Work Whitelist",
]


@dataclass
class DistractionEntry:
    id: str
    type: str  # "app" | "website" | "keyword"
    value: str
    category: str
    match: str = "exact"  # "exact" | "contains" | "regex"
    enabled: bool = True
    priority: str = "normal"  # "high" | "normal" | "low"
    notes: str = ""

    def matches(self, text: str) -> bool:
        if not self.enabled:
            return False
        t = (text or "")
        v = self.value or ""
        if self.match == "exact":
            return t.lower() == v.lower()
        if self.match == "contains":
            return v.lower() in t.lower()
        if self.match == "regex":
            try:
                return re.search(v, t, flags=re.IGNORECASE) is not None
            except re.error:
                return False
        return False


@dataclass
class DistractionList:
    version: int = 1
    categories: List[str] = field(default_factory=lambda: list(DEFAULT_CATEGORIES))
    entries: List[DistractionEntry] = field(default_factory=lambda: [])
    updated_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


class DistractionListStore:
    """Manages load/save of distraction rules with atomic writes."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or os.path.join(os.path.expanduser("~"), ".aura_distractions.json")
        self.data = DistractionList()
        if os.path.exists(self.path):
            try:
                self.load(self.path)
            except Exception:
                # Fallback to defaults if load fails
                self._load_defaults()
        else:
            self._load_defaults()

    def _load_defaults(self) -> None:
        defaults: List[Tuple[str, str, str]] = [
            ("website", "instagram.com", "Social Media"),
            ("website", "facebook.com", "Social Media"),
            ("website", "twitter.com", "Social Media"),
            ("website", "x.com", "Social Media"),
            ("website", "reddit.com", "Social Media"),
            ("website", "youtube.com", "Entertainment"),
            ("website", "tiktok.com", "Entertainment"),
            ("website", "netflix.com", "Entertainment"),
            ("website", "primevideo.com", "Entertainment"),
            ("app", "steam.exe", "Games"),
            ("app", "valorant.exe", "Games"),
            ("app", "discord.exe", "Communication"),
        ]
        self.data.entries = [
            DistractionEntry(
                id=str(uuid.uuid4()), type=t, value=v, category=c, match="contains" if t == "website" else "exact"
            )
            for (t, v, c) in defaults
        ]
        self._touch()

    def _touch(self) -> None:
        self.data.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # ---------------- Persistence ----------------
    def load(self, path: str) -> None:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        entries: List[DistractionEntry] = []
        for e in raw.get("entries", []):
            try:
                entries.append(
                    DistractionEntry(
                        id=e.get("id") or str(uuid.uuid4()),
                        type=str(e["type"]).lower(),
                        value=str(e["value"]).strip(),
                        category=str(e.get("category") or "Entertainment"),
                        match=str(e.get("match") or "exact"),
                        enabled=bool(e.get("enabled", True)),
                        priority=str(e.get("priority", "normal")),
                        notes=str(e.get("notes" or "")),
                    )
                )
            except Exception:
                # Skip invalid entry
                continue
        self.data = DistractionList(
            version=int(raw.get("version", 1)),
            categories=list(raw.get("categories") or DEFAULT_CATEGORIES),
            entries=entries,
            updated_at=str(raw.get("updated_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        )
        self.path = path

    def save(self, path: Optional[str] = None) -> None:
        # Atomic write: write to temp and replace
        out_path = path or self.path
        raw: dict[str, Any] = {
            "version": self.data.version,
            "categories": self.data.categories,
            "entries": [e.__dict__ for e in self.data.entries],
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        tmp = out_path + ".tmp"
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False)
        os.replace(tmp, out_path)
        self.path = out_path
        self._touch()

    def export_json(self, path: str) -> None:
        self.save(path)

    def import_json(self, path: str) -> None:
        self.load(path)

    # --------------- Query helpers ---------------
    def list_by_type(self, t: str) -> List[DistractionEntry]:
        return [e for e in self.data.entries if e.type == t]

    def add_entry(self, entry: DistractionEntry) -> None:
        # Prevent duplicates by value+type in exact mode
        if any(e.type == entry.type and e.value.lower() == entry.value.lower() for e in self.data.entries):
            raise ValueError("Entry already exists")
        self.data.entries.append(entry)
        self._touch()

    def update_entry(self, entry_id: str, updated: DistractionEntry) -> None:
        for i, e in enumerate(self.data.entries):
            if e.id == entry_id:
                self.data.entries[i] = updated
                self._touch()
                return
        raise KeyError("Entry not found")

    def delete_entry(self, entry_id: str) -> None:
        before = len(self.data.entries)
        self.data.entries = [e for e in self.data.entries if e.id != entry_id]
        if len(self.data.entries) == before:
            raise KeyError("Entry not found")
        self._touch()

    def match_text(self, text: str) -> Optional[DistractionEntry]:
        # Priority order: high > normal > low, exact > contains > regex
        priority_rank = {"high": 0, "normal": 1, "low": 2}
        match_rank = {"exact": 0, "contains": 1, "regex": 2}
        candidates = [e for e in self.data.entries if e.enabled and e.type in ("keyword", "website")]
        # Sort to ensure deterministic matching
        candidates.sort(key=lambda e: (priority_rank.get(e.priority, 1), match_rank.get(e.match, 1), e.value))
        for e in candidates:
            if e.matches(text):
                return e
        return None

    def match_app(self, app_name: str) -> Optional[DistractionEntry]:
        candidates = [e for e in self.data.entries if e.enabled and e.type == "app"]
        # Exact first
        exacts = [e for e in candidates if e.match == "exact"]
        for e in exacts:
            if e.matches(app_name):
                return e
        # Then contains/regex
        for e in candidates:
            if e.match != "exact" and e.matches(app_name):
                return e
        return None

    # --------------- Classifier integration ---------------
    def apply_to_classifier(self, classifier: object) -> None:  # type: ignore[override]
        """Push current rules into classifier.cfg sets.

        Mapping:
            - Websites:
                Social Media -> cfg.social_domains
                Entertainment -> cfg.entertainment_domains
                News -> cfg.news_domains
                Communication -> cfg.communication_domains
                Work Whitelist -> cfg.whitelist_domains
            - Applications:
                Games -> cfg.gaming_apps
                Communication -> cfg.communication_apps
                Work Whitelist -> cfg.whitelist_apps
            - Keywords:
                Not directly supported by classifier; expose via `match_text` for app to consult.
        """
        try:
            cfg = classifier.cfg  # type: ignore[attr-defined]
        except Exception:
            return

        # Functions to add safely (lowercased for domains/apps)
        def add_all(target_set: set[str], values: List[str]) -> None:
            for v in values:
                if v:
                    try:
                        target_set.add(v.lower())
                    except Exception:
                        pass

        web = [e for e in self.data.entries if e.type == "website" and e.enabled]
        apps = [e for e in self.data.entries if e.type == "app" and e.enabled]

        # Websites -> domain sets
        add_all(cfg.social_domains, [e.value for e in web if e.category == "Social Media"])  # type: ignore[attr-defined]
        add_all(cfg.entertainment_domains, [e.value for e in web if e.category == "Entertainment"])  # type: ignore[attr-defined]
        add_all(cfg.news_domains, [e.value for e in web if e.category == "News"])  # type: ignore[attr-defined]
        add_all(cfg.communication_domains, [e.value for e in web if e.category == "Communication"])  # type: ignore[attr-defined]
        add_all(cfg.whitelist_domains, [e.value for e in web if e.category == "Work Whitelist"])  # type: ignore[attr-defined]

        # Applications -> app sets
        add_all(cfg.gaming_apps, [e.value for e in apps if e.category == "Games"])  # type: ignore[attr-defined]
        add_all(cfg.communication_apps, [e.value for e in apps if e.category == "Communication"])  # type: ignore[attr-defined]
        add_all(cfg.whitelist_apps, [e.value for e in apps if e.category == "Work Whitelist"])  # type: ignore[attr-defined]


class DistractionEntryDialog(tk.Toplevel):
    """Dialog for adding/editing a distraction entry."""

    def __init__(self, master: tk.Misc, categories: List[str], entry: Optional[DistractionEntry] = None) -> None:
        super().__init__(master)
        self.title("Distraction Entry")
        self.resizable(False, False)
        self.transient(master)  # type: ignore[arg-type]
        self.grab_set()

        self.result: Optional[DistractionEntry] = None
        self._categories = categories

        # Variables
        self.var_type = tk.StringVar(value=(entry.type if entry else "website"))
        self.var_value = tk.StringVar(value=(entry.value if entry else ""))
        self.var_category = tk.StringVar(value=(entry.category if entry else categories[0]))
        self.var_match = tk.StringVar(value=(entry.match if entry else "exact"))
        self.var_enabled = tk.BooleanVar(value=(entry.enabled if entry else True))
        self.var_priority = tk.StringVar(value=(entry.priority if entry else "normal"))
        self.var_notes = tk.StringVar(value=(entry.notes if entry else ""))

        # Layout
        frm = ttk.Frame(self, padding=10)
        frm.grid(row=0, column=0, sticky="nsew")

        # Type
        ttk.Label(frm, text="Type:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(frm, textvariable=self.var_type, values=["app", "website", "keyword"], state="readonly", width=18).grid(row=0, column=1, sticky="w")
        # Value
        ttk.Label(frm, text="Value:").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.var_value, width=32).grid(row=1, column=1, sticky="w")
        # Category
        ttk.Label(frm, text="Category:").grid(row=2, column=0, sticky="w")
        ttk.Combobox(frm, textvariable=self.var_category, values=self._categories, state="readonly", width=18).grid(row=2, column=1, sticky="w")
        # Match
        ttk.Label(frm, text="Match:").grid(row=3, column=0, sticky="w")
        ttk.Combobox(frm, textvariable=self.var_match, values=["exact", "contains", "regex"], state="readonly", width=18).grid(row=3, column=1, sticky="w")
        # Priority
        ttk.Label(frm, text="Priority:").grid(row=4, column=0, sticky="w")
        ttk.Combobox(frm, textvariable=self.var_priority, values=["high", "normal", "low"], state="readonly", width=18).grid(row=4, column=1, sticky="w")
        # Enabled
        ttk.Checkbutton(frm, text="Enabled", variable=self.var_enabled).grid(row=5, column=1, sticky="w")
        # Notes
        ttk.Label(frm, text="Notes:").grid(row=6, column=0, sticky="nw")
        ttk.Entry(frm, textvariable=self.var_notes, width=32).grid(row=6, column=1, sticky="w")

        # Buttons
        btn_frm = ttk.Frame(frm)
        btn_frm.grid(row=7, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(btn_frm, text="Test", command=self._on_test).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(btn_frm, text="Cancel", command=self._on_cancel).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(btn_frm, text="Save", command=self._on_save).grid(row=0, column=2)

        self.bind("<Return>", lambda e: self._on_save())
        self.bind("<Escape>", lambda e: self._on_cancel())

    def _validate(self) -> Optional[str]:
        t = self.var_type.get()
        value = self.var_value.get().strip()
        match = self.var_match.get()
        if t not in {"app", "website", "keyword"}:
            return "Invalid type"
        if not value:
            return "Value is required"
        if match not in {"exact", "contains", "regex"}:
            return "Invalid match type"
        if t == "app":
            # crude app validation: must end with .exe on Windows
            if not value.lower().endswith(".exe") and match == "exact":
                return "App name should end with .exe for exact match"
        if t == "website":
            # domain-ish check
            if match != "regex" and "." not in value:
                return "Website should be a domain like example.com"
        if match == "regex":
            try:
                re.compile(value)
            except re.error:
                return "Invalid regex pattern"
        return None

    def _on_test(self) -> None:
        err = self._validate()
        if err:
            messagebox.showerror("Invalid Entry", err, parent=self)
            return
        value = self.var_value.get().strip()
        # Simple prompt to test against a string
        sample = simpledialog.askstring("Test Pattern", "Enter a window title / app / url to test:", parent=self)
        if sample is None:
            return
        # Synthetic entry to reuse matching logic
        e = DistractionEntry(
            id="test", type=self.var_type.get(), value=value, category=self.var_category.get(), match=self.var_match.get()
        )
        ok = e.matches(sample or "")
        messagebox.showinfo("Test Result", f"Match: {'YES' if ok else 'NO'}", parent=self)

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()

    def _on_save(self) -> None:
        err = self._validate()
        if err:
            messagebox.showerror("Invalid Entry", err, parent=self)
            return
        e = DistractionEntry(
            id=str(uuid.uuid4()),
            type=self.var_type.get(),
            value=self.var_value.get().strip(),
            category=self.var_category.get(),
            match=self.var_match.get(),
            enabled=self.var_enabled.get(),
            priority=self.var_priority.get(),
            notes=self.var_notes.get(),
        )
        self.result = e
        self.destroy()


class DistractionListEditor(ttk.Frame):
    """Embedded editor widget; call `show_window()` for a toplevel window."""

    def __init__(
        self,
        master: tk.Misc,
        store: DistractionListStore,
        classifier: Optional[object] = None,
        on_change: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(master, padding=10)
        self.store = store
        self.classifier = classifier
        self.on_change = on_change

        # Toolbar
        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(bar, text="Add", command=self._add_entry).pack(side=tk.LEFT)
        ttk.Button(bar, text="Edit", command=self._edit_selected).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(bar, text="Delete", command=self._delete_selected).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(bar, text="Import…", command=self._import).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Button(bar, text="Export…", command=self._export).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(bar, text="Save", command=self._save).pack(side=tk.RIGHT)

        # Tree
        self.tree = ttk.Treeview(self, columns=("type", "value", "category", "match", "priority", "enabled"), show="headings")
        for c, w in ("type", 90), ("value", 240), ("category", 140), ("match", 90), ("priority", 80), ("enabled", 80):
            self.tree.heading(c, text=c.title())
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<Double-1>", lambda e: self._edit_selected())

        self._refresh_tree()

    # ------------- UI helpers -------------
    def _refresh_tree(self) -> None:
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        # Stable sorted
        prio = {"high": 0, "normal": 1, "low": 2}
        for e in sorted(self.store.data.entries, key=lambda x: (x.type, prio.get(x.priority, 1), x.category, x.value.lower())):
            self.tree.insert("", tk.END, iid=e.id, values=(e.type, e.value, e.category, e.match, e.priority, "Yes" if e.enabled else "No"))

    def _notify_change(self) -> None:
        # Apply to classifier immediately
        if self.classifier is not None:
            try:
                self.store.apply_to_classifier(self.classifier)
            except Exception:
                pass
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                pass

    def _add_entry(self) -> None:
        dlg = DistractionEntryDialog(self, self.store.data.categories)
        self.wait_window(dlg)
        if dlg.result:
            try:
                self.store.add_entry(dlg.result)
                self._refresh_tree()
                self._notify_change()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to add: {e}")

    def _get_selected_entry(self) -> Optional[DistractionEntry]:
        sel = self.tree.selection()
        if not sel:
            return None
        entry_id = sel[0]
        for e in self.store.data.entries:
            if e.id == entry_id:
                return e
        return None

    def _edit_selected(self) -> None:
        e = self._get_selected_entry()
        if not e:
            return
        dlg = DistractionEntryDialog(self, self.store.data.categories, entry=e)
        self.wait_window(dlg)
        if dlg.result:
            updated = dlg.result
            updated.id = e.id  # preserve id
            try:
                self.store.update_entry(e.id, updated)
                self._refresh_tree()
                self._notify_change()
            except Exception as ex:
                messagebox.showerror("Error", f"Failed to update: {ex}")

    def _delete_selected(self) -> None:
        e = self._get_selected_entry()
        if not e:
            return
        if not messagebox.askyesno("Confirm", f"Delete '{e.value}'?", parent=self):
            return
        try:
            self.store.delete_entry(e.id)
            self._refresh_tree()
            self._notify_change()
        except Exception as ex:
            messagebox.showerror("Error", f"Failed to delete: {ex}")

    def _import(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")])
        if not path:
            return
        try:
            self.store.import_json(path)
            self._refresh_tree()
            self._notify_change()
        except Exception as ex:
            messagebox.showerror("Error", f"Failed to import: {ex}")

    def _export(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON Files", "*.json")])
        if not path:
            return
        try:
            self.store.export_json(path)
            messagebox.showinfo("Export", "Exported successfully.")
        except Exception as ex:
            messagebox.showerror("Error", f"Failed to export: {ex}")

    def _save(self) -> None:
        try:
            self.store.save()
            messagebox.showinfo("Saved", f"Saved to {self.store.path}")
        except Exception as ex:
            messagebox.showerror("Error", f"Failed to save: {ex}")

    # ------------- Window wrapper -------------
    def show_window(self, title: str = "Distraction List Editor") -> tk.Toplevel:
        win = tk.Toplevel(self.master)
        win.title(title)
        win.geometry("700x420")
        self.__class__(win, self.store, self.classifier, self.on_change).pack(fill=tk.BOTH, expand=True)
        return win


__all__ = [
    "DistractionEntry",
    "DistractionList",
    "DistractionListStore",
    "DistractionEntryDialog",
    "DistractionListEditor",
]
