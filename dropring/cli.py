import argparse
import os
import signal
import sys
import time
from pathlib import Path

from . import APP_NAME, __version__, engines, formats, presets
from .i18n import t


def _rule_from(args, store):
    rule = dict(store.settings["output"])
    if args.out:
        rule["location"] = "custom"
        rule["custom_folder"] = str(Path(args.out).expanduser().resolve())
    elif args.subfolder:
        rule["location"] = "subfolder"
        rule["subfolder"] = args.subfolder
    elif args.out is None and args.subfolder is None and rule.get("location") not in ("same", "subfolder", "custom"):
        rule["location"] = "same"
    if args.overwrite:
        rule["conflict"] = "overwrite"
    if args.skip:
        rule["conflict"] = "skip"
    if args.name:
        rule["pattern"] = args.name
    return rule


def _expand(paths):
    result = []
    for p in paths:
        path = Path(p).expanduser()
        if path.is_dir():
            result += sorted(c for c in path.iterdir() if c.is_file() and formats.detect(c))
        else:
            result.append(path)
    return result


def _bar(fraction, width=24):
    filled = int(round(fraction * width))
    return "█" * filled + "░" * (width - filled)


def cmd_convert(args):
    from .jobs import FAILED, JobQueue
    from .store import Store

    store = Store()
    preset = presets.find(args.to)
    if preset is None:
        print(t("Unknown target or preset: {0}").format(args.to), file=sys.stderr)
        return 2
    paths = _expand(args.files)
    missing = [p for p in paths if not p.exists()]
    if missing:
        print(t("File not found: {0}").format(missing[0]), file=sys.stderr)
        return 2
    queue = JobQueue(store, workers=args.jobs)
    ok, reason = presets.availability(preset, paths, queue.planner)
    if not ok:
        print(f"{preset.name}: {t(reason)}", file=sys.stderr)
        return 2
    job = queue.submit(paths, preset, _rule_from(args, store), origin="cli")
    signal.signal(signal.SIGINT, lambda *_: job.cancel())
    interactive = sys.stderr.isatty() and not args.quiet
    while not job.done:
        if interactive:
            print(f"\r{_bar(job.progress)} {job.progress * 100:5.1f}%", end="", file=sys.stderr, flush=True)
        time.sleep(0.15)
    if interactive:
        print("\r" + " " * 40 + "\r", end="", file=sys.stderr)
    for f in job.files:
        if f.status == FAILED:
            print(f"✗ {f.source.name}: {f.error}", file=sys.stderr)
        elif f.status == "skipped":
            print(f"– {f.source.name}: {t(f.error)}", file=sys.stderr)
        elif f.status == "cancelled":
            print(f"■ {f.source.name}: {t('cancelled')}", file=sys.stderr)
    for out in job.outputs:
        print(out)
    queue.shutdown()
    return 0 if all(f.status in ("done", "skipped") for f in job.files) and (job.outputs or job.preset.kind == presets.CONVERT) else 1


def cmd_presets(args):
    from .planner import RoutePlanner

    planner = RoutePlanner(engines.all_engines())
    paths = _expand(args.files) if args.files else []
    if paths:
        profile = presets.select_profile(paths)
        print(f"{t('Profile')}: {t(profile.name)}")
        for face, ids in ((t("Front"), profile.front), (t("Back"), profile.back)):
            print(f"\n{face}:")
            for pid in ids:
                preset = presets.BY_ID[pid]
                ok, reason = presets.availability(preset, paths, planner)
                print(f"  {'✓' if ok else '·'} {preset.id:<18} {t(preset.name)}" + ("" if ok else f"  ({t(reason)})"))
        return 0
    for preset in presets.PRESETS:
        print(f"{preset.id:<18} {t(preset.name)}")
    return 0


def cmd_formats(args):
    from .planner import RoutePlanner

    planner = RoutePlanner(engines.all_engines())
    for category, title in formats.CATEGORY_NAMES.items():
        print(f"\n{t(title)}")
        for fmt in (f for f in formats.all_formats() if f.category == category):
            targets = sorted(planner.targets_from(fmt.id))
            names = ", ".join(formats.by_id(x).name for x in targets) or "—"
            print(f"  {fmt.name:<11} → {names}")
    return 0


def cmd_engines(args):
    for name, available, binaries in engines.environment_summary():
        print(f"{'✓' if available else '✗'} {name:<14} {binaries}")
    return 0


def cmd_watch(args):
    from .jobs import FAILED, JobQueue
    from .store import Store
    from .watch import FolderWatcher

    preset = presets.find(args.to)
    if preset is None:
        print(t("Unknown target or preset: {0}").format(args.to), file=sys.stderr)
        return 2
    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        print(t("Folder not found: {0}").format(folder), file=sys.stderr)
        return 2

    def on_update(job):
        if job.done:
            for f in job.files:
                mark = "✗" if f.status == FAILED else "✓"
                outs = ", ".join(str(o) for o in f.outputs)
                print(f"{mark} {f.source.name} {('→ ' + outs) if outs else f.error}", flush=True)

    queue = JobQueue(Store(), on_update=on_update)
    rule = {"folder": str(folder), "preset": preset.id, "recursive": args.recursive, "into_subfolder": not args.here, "subfolder": args.subfolder or "converted"}
    watcher = FolderWatcher(rule, queue, on_log=lambda m: print(m, file=sys.stderr, flush=True))
    if not watcher.start():
        return 1
    print(t("Press Ctrl+C to stop."), file=sys.stderr)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        watcher.stop()
        queue.shutdown()
    return 0


def cmd_gui(args, menu_files=None, convert=None, clipboard=False):
    from .gui.app import run

    return run(background=getattr(args, "background", False), menu_files=menu_files, show=getattr(args, "show", False), convert=convert,
               clipboard=clipboard)


def cmd_menu(args):
    from .ipc import send

    if args.clipboard or not args.files:
        if send({"cmd": "clipboard"}):
            return 0
        args.background = True
        return cmd_gui(args, clipboard=True)
    files = [str(Path(f).expanduser().resolve()) for f in args.files]
    if args.run:
        if presets.find(args.run) is None:
            print(t("Unknown target or preset: {0}").format(args.run), file=sys.stderr)
            return 2
        if send({"cmd": "convert", "preset": args.run, "files": files}):
            return 0
        args.background = True
        return cmd_gui(args, convert=(args.run, files))
    if send({"cmd": "menu", "files": files}):
        return 0
    args.background = True
    return cmd_gui(args, menu_files=files)


def cmd_integrate(args):
    from . import integration

    if args.remove:
        integration.remove()
        print(t("Desktop integration removed."))
    else:
        for path in integration.install():
            print(path)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="dropring", description=t("{0}: drag, drop, converted.").format(APP_NAME))
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    sub = parser.add_subparsers(dest="command")

    gui = sub.add_parser("gui", help=t("Start the app"))
    gui.add_argument("--background", action="store_true", help=t("Start in the tray"))
    gui.add_argument("--show", action="store_true", help=t("Show the main window"))

    menu = sub.add_parser("menu", help=t("Open the format ring for files"))
    menu.add_argument("files", nargs="*")
    menu.add_argument("--clipboard", action="store_true", help=t("Open the ring for the files copied with Ctrl+C"))
    menu.add_argument("--run", metavar="PRESET", help=t("Convert at once instead of showing the ring"))

    convert = sub.add_parser("convert", help=t("Convert files"))
    convert.add_argument("files", nargs="+")
    convert.add_argument("-t", "--to", required=True, help=t("Target format or preset id (png, mp3, half-size, merge-pdf …)"))
    convert.add_argument("-o", "--out", help=t("Output folder"))
    convert.add_argument("-s", "--subfolder", help=t("Put results in a subfolder next to the source"))
    convert.add_argument("-n", "--name", help=t("File name pattern: {name} {ext} {preset} {date} {time} {index}"))
    convert.add_argument("-j", "--jobs", type=int, help=t("Files converted in parallel"))
    convert.add_argument("-q", "--quiet", action="store_true")
    group = convert.add_mutually_exclusive_group()
    group.add_argument("--overwrite", action="store_true", help=t("Replace existing outputs"))
    group.add_argument("--skip", action="store_true", help=t("Skip when the output exists"))

    pres = sub.add_parser("presets", help=t("List presets (for files, the ring they get)"))
    pres.add_argument("files", nargs="*")

    sub.add_parser("formats", help=t("List formats and what they convert to"))
    sub.add_parser("engines", help=t("Show which conversion tools were found"))

    watch = sub.add_parser("watch", help=t("Convert every new file in a folder"))
    watch.add_argument("folder")
    watch.add_argument("-t", "--to", required=True)
    watch.add_argument("-r", "--recursive", action="store_true")
    watch.add_argument("--here", action="store_true", help=t("Write results into the folder itself"))
    watch.add_argument("-s", "--subfolder")

    integrate = sub.add_parser("integrate", help=t("Add the file manager menu and launcher"))
    integrate.add_argument("--remove", action="store_true")
    return parser


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "convert": cmd_convert, "presets": cmd_presets, "formats": cmd_formats, "engines": cmd_engines,
        "watch": cmd_watch, "menu": cmd_menu, "integrate": cmd_integrate, "gui": cmd_gui,
    }
    if args.command is None:
        args.show = True
        return cmd_gui(args)
    try:
        return handlers[args.command](args) or 0
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 1


if __name__ == "__main__":
    sys.exit(main())
