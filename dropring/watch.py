import hashlib
import json
import os
import threading
import time
from pathlib import Path

from . import formats, presets

IGNORED_SUFFIXES = (".part", ".crdownload", ".tmp", ".download", ".partial", "~", ".kate-swp", ".swp")
STATE_PREFIX = ".dropring-watch-"


def rule_key(rule):
    text = f"{Path(rule['folder']).resolve()}|{rule['preset']}"
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def output_rule(rule):
    return {
        "location": "subfolder" if rule.get("into_subfolder", True) else "same",
        "subfolder": rule.get("subfolder", "converted"),
        "pattern": "{name}",
        "conflict": "rename",
    }


class FolderWatcher:
    interval = 2.0
    settle = 2.0

    def __init__(self, rule, queue, on_log=None):
        self.rule = rule
        self.queue = queue
        self.on_log = on_log or (lambda message: None)
        self.folder = Path(os.path.expanduser(rule["folder"]))
        self.preset = presets.BY_ID.get(rule["preset"])
        self.recursive = rule.get("recursive", False)
        self.state_path = self.folder / f"{STATE_PREFIX}{rule_key(rule)}.json"
        self._stop = threading.Event()
        self._thread = None
        self._seen = {}
        self._pending = {}
        self._generated = self._load_state()

    def _load_state(self):
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return {k: tuple(v) for k, v in data.get("generated", {}).items()}
        except (OSError, ValueError, AttributeError):
            return {}

    def _save_state(self):
        try:
            self.state_path.write_text(json.dumps({"generated": self._generated}), encoding="utf-8")
        except OSError as error:
            self.on_log(f"Cannot save watch state: {error}")

    def _skip_dirs(self):
        rule = output_rule(self.rule)
        return {(self.folder / rule["subfolder"]).resolve()} if rule["location"] == "subfolder" else set()

    def _scan(self):
        skip = self._skip_dirs()
        found = {}
        walker = os.walk(self.folder) if self.recursive else [(str(self.folder), [], os.listdir(self.folder))]
        for root, dirs, files in walker:
            if Path(root).resolve() in skip:
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if not d.startswith(".") and (Path(root) / d).resolve() not in skip]
            for name in files:
                if name.startswith(".") or name.lower().endswith(IGNORED_SUFFIXES):
                    continue
                path = Path(root) / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if not path.is_file() or formats.detect(path) is None:
                    continue
                found[str(path)] = (stat.st_size, stat.st_mtime)
        return found

    def start(self):
        if self.preset is None or not self.folder.is_dir():
            self.on_log(f"Watch folder not started: {self.folder}")
            return False
        self._seen = self._scan()
        self._thread = threading.Thread(target=self._loop, name=f"watch-{self.folder.name}", daemon=True)
        self._thread.start()
        self.on_log(f"Watching {self.folder} → {self.preset.name}")
        return True

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                self.poll()
            except OSError as error:
                self.on_log(f"Watch error in {self.folder}: {error}")

    def poll(self):
        now = time.time()
        current = self._scan()
        for path, signature in current.items():
            if self._seen.get(path) == signature or self._generated.get(path) == tuple(signature):
                continue
            pending = self._pending.get(path)
            if pending and pending[0] == signature and now - pending[1] >= self.settle:
                del self._pending[path]
                self._seen[path] = signature
                self._convert(Path(path))
            elif not pending or pending[0] != signature:
                self._pending[path] = (signature, now)
        for path in list(self._seen):
            if path not in current:
                del self._seen[path]

    def _convert(self, path):
        ok, reason = presets.availability(self.preset, [path], self.queue.planner)
        if not ok:
            self.on_log(f"Skipped {path.name}: {reason}")
            return
        try:
            with open(path, "rb"):
                pass
        except OSError:
            return
        job = self.queue.submit([path], self.preset, output_rule(self.rule), origin="watch")
        threading.Thread(target=self._remember, args=(job,), daemon=True).start()

    def _remember(self, job):
        while not job.done:
            time.sleep(0.2)
        changed = False
        for out in job.outputs:
            try:
                stat = Path(out).stat()
            except OSError:
                continue
            self._generated[str(out)] = (stat.st_size, stat.st_mtime)
            self._seen[str(out)] = (stat.st_size, stat.st_mtime)
            changed = True
        if changed:
            self._save_state()


class WatchManager:
    def __init__(self, queue, on_log=None):
        self.queue = queue
        self.on_log = on_log
        self.watchers = []

    def apply(self, rules):
        for watcher in self.watchers:
            watcher.stop()
        self.watchers = []
        for rule in rules:
            if rule.get("enabled", True):
                watcher = FolderWatcher(rule, self.queue, self.on_log)
                if watcher.start():
                    self.watchers.append(watcher)

    def stop(self):
        for watcher in self.watchers:
            watcher.stop()
