from dataclasses import dataclass, field

from . import formats
from .i18n import t
from .formats import ARCHIVE, AUDIO, DOCUMENT, IMAGE, PDF, PRESENTATION, SPREADSHEET, VIDEO

CONVERT = "convert"
TRANSFORM = "transform"
MERGE_PDF = "merge-pdf"
ZIP_ALL = "zip-all"
EXTRACT = "extract"


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    kind: str = CONVERT
    target: str = ""
    options: dict = field(default_factory=dict)
    categories: tuple = ()
    suffix: str = ""
    short: str = ""

    @property
    def label(self):
        return self.short or self.name


def _convert(target, **options):
    fmt = formats.by_id(target)
    return Preset(f"to-{target}", fmt.name, CONVERT, target, options, short=fmt.name)


PRESETS = [
    _convert(f.id) for f in formats.all_formats() if f.id not in ("raw", "xcf", "psd", "svg", "rar", "dds")
] + [
    Preset("half-size", "Half size", TRANSFORM, options={"resize": "50%", "scale": 0.5}, categories=(IMAGE, VIDEO), suffix="_half", short="50%"),
    Preset("grayscale", "Grayscale", TRANSFORM, options={"grayscale": True}, categories=(IMAGE, VIDEO), suffix="_gray", short="Gray"),
    Preset("rotate", "Rotate right", TRANSFORM, options={"rotate": 90}, categories=(IMAGE,), suffix="_rotated", short="↻ 90°"),
    Preset("flip", "Mirror", TRANSFORM, options={"flop": True}, categories=(IMAGE,), suffix="_mirrored", short="⇋ Mirror"),
    Preset("strip-metadata", "Remove metadata", TRANSFORM, options={"strip": True}, categories=(IMAGE,), suffix="_clean", short="No EXIF"),
    Preset("compress-image", "Compress image", TRANSFORM, options={"quality": 75, "strip": True, "max_size": 2560}, categories=(IMAGE,), suffix="_small", short="Compress"),
    Preset("compress-pdf", "Compress PDF", TRANSFORM, options={"pdf_quality": "ebook"}, categories=(PDF,), suffix="_compressed", short="Compress"),
    Preset("compress-video", "Compress video", TRANSFORM, options={"crf": 30, "x264_preset": "slow"}, categories=(VIDEO,), suffix="_small", short="Compress"),
    Preset("mute-video", "Remove sound", TRANSFORM, options={"mute": True}, categories=(VIDEO,), suffix="_muted", short="Mute"),
    Preset("merge-pdf", "Merge into one PDF", MERGE_PDF, "pdf", short="Merge PDF"),
    Preset("zip-all", "Compress into ZIP", ZIP_ALL, "zip", short="ZIP all"),
    Preset("extract", "Extract archive", EXTRACT, categories=(ARCHIVE,), short="Extract"),
]

BY_ID = {p.id: p for p in PRESETS}


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    categories: tuple
    front: tuple
    back: tuple = ()
    fallback: bool = False


PROFILES = [
    Profile("images", "Images", (IMAGE,),
            ("to-png", "to-jpg", "to-webp", "to-pdf", "to-avif", "to-tiff", "to-bmp", "to-ico", "half-size", "grayscale"),
            ("rotate", "flip", "strip-metadata", "compress-image", "to-gif", "to-jxl", "to-qoi", "to-jp2", "merge-pdf", "zip-all")),
    Profile("pdf", "PDF", (PDF,),
            ("to-png", "to-jpg", "to-docx", "to-txt", "to-odt", "to-epub", "to-html", "compress-pdf", "merge-pdf", "to-tiff"),
            ("to-rtf", "to-md", "zip-all")),
    Profile("documents", "Documents", (DOCUMENT,),
            ("to-pdf", "to-docx", "to-odt", "to-txt", "to-html", "to-epub", "to-rtf", "to-md", "to-png", "merge-pdf"),
            ("to-doc", "to-jpg", "zip-all")),
    Profile("spreadsheets", "Spreadsheets", (SPREADSHEET,),
            ("to-pdf", "to-xlsx", "to-ods", "to-csv", "to-html", "merge-pdf"),
            ("to-xls", "to-png", "to-txt", "zip-all")),
    Profile("presentations", "Presentations", (PRESENTATION,),
            ("to-pdf", "to-pptx", "to-odp", "to-png", "to-jpg", "merge-pdf"),
            ("to-ppt", "zip-all")),
    Profile("video", "Video", (VIDEO,),
            ("to-mp4", "to-mkv", "to-webm", "to-mov", "to-gif", "to-mp3", "to-avi", "compress-video", "half-size", "mute-video"),
            ("to-wav", "to-flac", "to-ogg", "to-m4a", "to-jpg", "grayscale", "to-wmv", "to-ogv", "zip-all")),
    Profile("audio", "Audio", (AUDIO,),
            ("to-mp3", "to-wav", "to-flac", "to-ogg", "to-opus", "to-m4a", "to-aac"),
            ("to-aiff", "to-wma", "zip-all")),
    Profile("archives", "Archives", (ARCHIVE,),
            ("extract", "to-zip", "to-7z", "to-tgz", "to-tar"),
            ("to-txz", "to-tbz")),
    Profile("mixed", "Mixed files", (), ("to-pdf", "merge-pdf", "zip-all", "to-png", "to-txt"),
            ("to-jpg", "to-webp", "to-docx", "to-html", "compress-pdf"), fallback=True),
]


def select_profile(paths):
    categories = {fmt.category for fmt in (formats.detect(p) for p in paths) if fmt}
    for profile in PROFILES:
        if not profile.fallback and categories and categories <= set(profile.categories):
            return profile
    return next(p for p in PROFILES if p.fallback)


def target_for(preset, source_format):
    if preset.kind == TRANSFORM:
        return source_format
    return preset.target


def availability(preset, paths, planner):
    detected = [formats.detect(p) for p in paths]
    if not paths:
        return False, t("No files")
    if preset.kind == ZIP_ALL:
        return True, ""
    if preset.kind == EXTRACT:
        ok = all(f and f.category == ARCHIVE and planner.targets_from(f.id) for f in detected)
        return ok, "" if ok else t("Only archives can be extracted")
    if preset.kind == MERGE_PDF:
        if len(paths) < 2:
            return False, t("Needs two or more files")
        ok = all(f and (f.id == "pdf" or planner.can_convert(f.id, "pdf")) for f in detected)
        return ok, "" if ok else t("Some files cannot become PDF")
    if any(f is None for f in detected):
        return False, t("Unknown file type")
    if preset.categories and any(f.category not in preset.categories for f in detected):
        return False, t("Not for these files")
    if preset.kind == CONVERT and all(f.id == preset.target for f in detected):
        return False, t("Already {0}").format(formats.by_id(preset.target).name)
    for fmt in detected:
        target = target_for(preset, fmt.id)
        if fmt.id == target and preset.kind == CONVERT:
            continue
        if not planner.can_convert(fmt.id, target):
            return False, t("{0} → {1} is not available").format(fmt.name, formats.by_id(target).name)
    return True, ""


def find(preset_id_or_format):
    key = preset_id_or_format.lower().lstrip(".")
    if key in BY_ID:
        return BY_ID[key]
    fmt = formats.by_id(key) or formats.detect("x." + key)
    if fmt and f"to-{fmt.id}" in BY_ID:
        return BY_ID[f"to-{fmt.id}"]
    return None
