import itertools
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import engines, formats, output, presets
from .i18n import t
from .engines import Cancelled, CancelToken, ConversionError, StepContext
from .planner import RoutePlanner

WAITING = "waiting"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
SKIPPED = "skipped"
CANCELLED = "cancelled"


class FileResult:
    def __init__(self, source):
        self.source = Path(source)
        self.status = WAITING
        self.progress = 0.0
        self.outputs = []
        self.error = ""


class Job:
    _ids = itertools.count(1)

    def __init__(self, paths, preset, rule, origin="app"):
        self.id = next(self._ids)
        self.preset = preset
        self.rule = rule
        self.origin = origin
        self.files = [FileResult(p) for p in paths]
        self.cancel_token = CancelToken()
        self.started = time.time()
        self.finished = None
        self.combined_output = None

    @property
    def progress(self):
        if not self.files:
            return 1.0
        return sum(1.0 if f.status in (DONE, FAILED, SKIPPED, CANCELLED) else f.progress for f in self.files) / len(self.files)

    @property
    def done(self):
        return self.finished is not None

    @property
    def outputs(self):
        if self.combined_output:
            return [self.combined_output]
        return [o for f in self.files for o in f.outputs]

    def counts(self):
        result = {}
        for f in self.files:
            result[f.status] = result.get(f.status, 0) + 1
        return result

    def cancel(self):
        self.cancel_token.cancel()


class JobQueue:
    def __init__(self, store=None, on_update=None, workers=None):
        self.store = store
        self.on_update = on_update or (lambda job: None)
        settings = store.settings if store else {}
        self.planner = RoutePlanner(engines.all_engines(), settings.get("engine_priority"))
        self.pool = ThreadPoolExecutor(max_workers=workers or settings.get("parallel", 2), thread_name_prefix="convert")
        self.job_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="job")
        self._limits = {e.id: threading.Semaphore(e.concurrency) for e in engines.all_engines()}
        self._reserved = set()
        self._lock = threading.Lock()
        self.jobs = []

    def submit(self, paths, preset, rule=None, origin="app"):
        rule = dict(rule if rule is not None else (self.store.settings["output"] if self.store else {}))
        job = Job([Path(p).expanduser().absolute() for p in paths], preset, rule, origin)
        self.jobs.append(job)
        self.job_pool.submit(self._run_job, job)
        return job

    def run_sync(self, paths, preset, rule=None, origin="cli"):
        job = self.submit(paths, preset, rule, origin)
        while not job.done:
            time.sleep(0.1)
        return job

    def shutdown(self):
        for job in self.jobs:
            if not job.done:
                job.cancel()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.job_pool.shutdown(wait=False, cancel_futures=True)

    def _notify(self, job):
        try:
            self.on_update(job)
        except Exception:
            pass

    def _exists(self, path):
        return Path(path).exists() or str(path) in self._reserved

    def reserve(self, rule, source, extension, suffix="", folder=None, preset_name=""):
        with self._lock:
            path = output.resolve(rule, source, extension, self._exists, suffix=suffix, preset_name=preset_name, folder=folder)
            if path is not None:
                self._reserved.add(str(path))
            return path

    def release(self, paths):
        with self._lock:
            for p in paths:
                self._reserved.discard(str(p))

    def _run_job(self, job):
        self._notify(job)
        try:
            if job.preset.kind == presets.MERGE_PDF:
                self._merge_pdf(job)
            elif job.preset.kind == presets.ZIP_ALL:
                self._zip_all(job)
            else:
                futures = [self.pool.submit(self._run_file, job, f) for f in job.files]
                for future in futures:
                    future.result()
        except Exception as error:
            for f in job.files:
                if f.status in (WAITING, RUNNING):
                    f.status = CANCELLED if isinstance(error, Cancelled) else FAILED
                    f.error = "" if isinstance(error, Cancelled) else str(error)
        job.finished = time.time()
        self._notify(job)
        self._record(job)

    def _record(self, job):
        if not self.store:
            return
        self.store.add_history({
            "preset": job.preset.id,
            "preset_name": job.preset.name,
            "origin": job.origin,
            "sources": [str(f.source) for f in job.files],
            "outputs": [str(o) for o in job.outputs],
            "errors": [f"{f.source.name}: {f.error}" for f in job.files if f.status == FAILED],
            "ok": all(f.status in (DONE, SKIPPED) for f in job.files),
        })

    def _step_progress(self, job, result, index, count):
        def report(fraction):
            result.progress = min(0.99, (index + max(0.0, min(1.0, fraction))) / count)
            self._notify(job)
        return report

    def convert_to_temp(self, job, result, source, target, work, progress_share=1.0, options=None):
        source_format = formats.detect(source).id
        route = self.planner.plan(source_format, target)
        if route is None:
            raise ConversionError(t("{0} → {1} is not available").format(formats.by_id(source_format).name, formats.by_id(target).name))
        inputs = [Path(source)]
        for index, step in enumerate(route.steps):
            step_dir = Path(tempfile.mkdtemp(prefix=f"step{index}-", dir=work))
            produced = []
            report = self._step_progress(job, result, index, len(route.steps) / progress_share)
            for item in inputs:
                job.cancel_token.check()
                ctx = StepContext(step_dir, options if options is not None else job.preset.options, report, job.cancel_token)
                with self._limits[step.engine.id]:
                    job.cancel_token.check()
                    produced += step.engine.run(item, step.source, step.target, ctx)
            inputs = produced
        return inputs

    def _run_file(self, job, result):
        if job.cancel_token.cancelled:
            result.status = CANCELLED
            return
        result.status = RUNNING
        self._notify(job)
        reserved = []
        work = Path(tempfile.mkdtemp(prefix="dropring-"))
        try:
            fmt = formats.detect(result.source)
            if fmt is None:
                raise ConversionError(t("Unknown file type"))
            if not result.source.is_file():
                raise ConversionError(t("File not found"))
            if job.preset.kind == presets.EXTRACT:
                self._extract(job, result, fmt, work, reserved)
            else:
                target = presets.target_for(job.preset, fmt.id)
                if job.preset.kind == presets.CONVERT and fmt.id == target:
                    result.status = SKIPPED
                    result.error = t("Already {0}").format(fmt.name)
                    return
                produced = self.convert_to_temp(job, result, result.source, target, work)
                extension = formats.extension_of(produced[0]) or formats.by_id(target).extension
                for page, item in enumerate(produced, 1):
                    suffix = job.preset.suffix + (f"_p{page}" if len(produced) > 1 else "")
                    destination = self.reserve(job.rule, result.source, extension, suffix, preset_name=job.preset.name)
                    if destination is None:
                        continue
                    reserved.append(destination)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(item, destination)
                    result.outputs.append(destination)
                if not result.outputs:
                    result.status = SKIPPED
                    result.error = t("Output exists")
                    return
            result.status = DONE
        except Cancelled:
            result.status = CANCELLED
            self._discard(result.outputs)
            result.outputs = []
        except Exception as error:
            result.status = FAILED
            result.error = str(error) or error.__class__.__name__
            self._discard(result.outputs)
            result.outputs = []
        finally:
            result.progress = 1.0
            self.release(reserved)
            shutil.rmtree(work, ignore_errors=True)
            self._notify(job)

    def _discard(self, paths):
        for p in paths:
            try:
                Path(p).unlink()
            except OSError:
                pass

    def _extract(self, job, result, fmt, work, reserved):
        destination = self.reserve(job.rule, result.source, "", preset_name=job.preset.name)
        if destination is None:
            result.status = SKIPPED
            return
        reserved.append(destination)
        ctx = StepContext(work, {}, None, job.cancel_token)
        temp = work / "out"
        engines.extract_archive(result.source, fmt.id, temp, ctx)
        children = list(temp.iterdir())
        if len(children) == 1 and children[0].is_dir() and children[0].name == destination.name:
            temp = children[0]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(temp), str(destination))
        result.outputs.append(destination)

    def _combined_name(self, job, extension, suffix):
        first = job.files[0].source
        rule = dict(job.rule)
        if len(job.files) > 1:
            rule["location"] = "same" if rule.get("location") == "subfolder" else rule.get("location", "same")
        return self.reserve(rule, first, extension, suffix, preset_name=job.preset.name)

    def _merge_pdf(self, job):
        work = Path(tempfile.mkdtemp(prefix="dropring-merge-"))
        destination = None
        try:
            parts = []
            for index, result in enumerate(job.files):
                job.cancel_token.check()
                result.status = RUNNING
                self._notify(job)
                try:
                    fmt = formats.detect(result.source)
                    if fmt and fmt.id == "pdf":
                        parts.append(result.source)
                    else:
                        item_dir = work / str(index)
                        item_dir.mkdir()
                        parts += self.convert_to_temp(job, result, result.source, "pdf", item_dir, options={})
                    result.status = DONE
                except Cancelled:
                    raise
                except Exception as error:
                    result.status = FAILED
                    result.error = str(error)
                result.progress = 1.0
                self._notify(job)
            if not parts:
                return
            destination = self._combined_name(job, "pdf", "_merged")
            if destination is None:
                return
            destination.parent.mkdir(parents=True, exist_ok=True)
            engines.merge_pdfs(parts, destination, StepContext(work, {}, None, job.cancel_token))
            job.combined_output = destination
        except Cancelled:
            for f in job.files:
                if f.status != FAILED:
                    f.status = CANCELLED
            if destination:
                self._discard([destination])
        finally:
            if destination:
                self.release([destination])
            shutil.rmtree(work, ignore_errors=True)

    def _zip_all(self, job):
        destination = self._combined_name(job, "zip", "_archive" if len(job.files) > 1 else "")
        if destination is None:
            for f in job.files:
                f.status = SKIPPED
            return
        try:
            for f in job.files:
                f.status = RUNNING
            self._notify(job)
            destination.parent.mkdir(parents=True, exist_ok=True)
            engines.pack_archive([f.source for f in job.files], "zip", destination, StepContext(destination.parent, {}, None, job.cancel_token))
            for f in job.files:
                f.status = DONE
            job.combined_output = destination
        except Cancelled:
            self._discard([destination])
            for f in job.files:
                f.status = CANCELLED
        finally:
            self.release([destination])
