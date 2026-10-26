from dataclasses import dataclass, field
from pathlib import Path

IMAGE = "image"
VIDEO = "video"
AUDIO = "audio"
PDF = "pdf"
DOCUMENT = "document"
SPREADSHEET = "spreadsheet"
PRESENTATION = "presentation"
ARCHIVE = "archive"

CATEGORY_NAMES = {
    IMAGE: "Images",
    VIDEO: "Video",
    AUDIO: "Audio",
    PDF: "PDF",
    DOCUMENT: "Documents",
    SPREADSHEET: "Spreadsheets",
    PRESENTATION: "Presentations",
    ARCHIVE: "Archives",
}


@dataclass(frozen=True)
class FileFormat:
    id: str
    name: str
    category: str
    extensions: tuple = field(default_factory=tuple)

    @property
    def extension(self):
        return self.extensions[0]


_FORMATS = [
    FileFormat("jpg", "JPG", IMAGE, ("jpg", "jpeg", "jpe", "jfif")),
    FileFormat("png", "PNG", IMAGE, ("png",)),
    FileFormat("webp", "WEBP", IMAGE, ("webp",)),
    FileFormat("avif", "AVIF", IMAGE, ("avif",)),
    FileFormat("heic", "HEIC", IMAGE, ("heic", "heif")),
    FileFormat("jxl", "JPEG XL", IMAGE, ("jxl",)),
    FileFormat("tiff", "TIFF", IMAGE, ("tiff", "tif")),
    FileFormat("bmp", "BMP", IMAGE, ("bmp",)),
    FileFormat("gif", "GIF", IMAGE, ("gif",)),
    FileFormat("ico", "ICO", IMAGE, ("ico",)),
    FileFormat("tga", "TGA", IMAGE, ("tga",)),
    FileFormat("ppm", "PPM", IMAGE, ("ppm", "pgm", "pbm", "pnm")),
    FileFormat("psd", "PSD", IMAGE, ("psd",)),
    FileFormat("svg", "SVG", IMAGE, ("svg",)),
    FileFormat("jp2", "JPEG 2000", IMAGE, ("jp2", "j2k")),
    FileFormat("qoi", "QOI", IMAGE, ("qoi",)),
    FileFormat("dds", "DDS", IMAGE, ("dds",)),
    FileFormat("xcf", "GIMP XCF", IMAGE, ("xcf",)),
    FileFormat("raw", "Kamera RAW", IMAGE, ("cr2", "cr3", "nef", "arw", "dng", "orf", "rw2", "raf")),
    FileFormat("mp4", "MP4", VIDEO, ("mp4",)),
    FileFormat("mkv", "MKV", VIDEO, ("mkv",)),
    FileFormat("webm", "WEBM", VIDEO, ("webm",)),
    FileFormat("mov", "MOV", VIDEO, ("mov",)),
    FileFormat("avi", "AVI", VIDEO, ("avi",)),
    FileFormat("m4v", "M4V", VIDEO, ("m4v",)),
    FileFormat("wmv", "WMV", VIDEO, ("wmv",)),
    FileFormat("flv", "FLV", VIDEO, ("flv",)),
    FileFormat("mpeg", "MPEG", VIDEO, ("mpeg", "mpg")),
    FileFormat("ts", "MPEG-TS", VIDEO, ("ts", "mts", "m2ts")),
    FileFormat("3gp", "3GP", VIDEO, ("3gp",)),
    FileFormat("ogv", "OGV", VIDEO, ("ogv",)),
    FileFormat("mp3", "MP3", AUDIO, ("mp3",)),
    FileFormat("wav", "WAV", AUDIO, ("wav",)),
    FileFormat("flac", "FLAC", AUDIO, ("flac",)),
    FileFormat("ogg", "OGG", AUDIO, ("ogg", "oga")),
    FileFormat("opus", "OPUS", AUDIO, ("opus",)),
    FileFormat("m4a", "M4A", AUDIO, ("m4a",)),
    FileFormat("aac", "AAC", AUDIO, ("aac",)),
    FileFormat("wma", "WMA", AUDIO, ("wma",)),
    FileFormat("aiff", "AIFF", AUDIO, ("aiff", "aif")),
    FileFormat("pdf", "PDF", PDF, ("pdf",)),
    FileFormat("docx", "DOCX", DOCUMENT, ("docx",)),
    FileFormat("doc", "DOC", DOCUMENT, ("doc",)),
    FileFormat("odt", "ODT", DOCUMENT, ("odt",)),
    FileFormat("rtf", "RTF", DOCUMENT, ("rtf",)),
    FileFormat("txt", "TXT", DOCUMENT, ("txt", "text", "log")),
    FileFormat("html", "HTML", DOCUMENT, ("html", "htm")),
    FileFormat("md", "Markdown", DOCUMENT, ("md", "markdown")),
    FileFormat("epub", "EPUB", DOCUMENT, ("epub",)),
    FileFormat("xlsx", "XLSX", SPREADSHEET, ("xlsx",)),
    FileFormat("xls", "XLS", SPREADSHEET, ("xls",)),
    FileFormat("ods", "ODS", SPREADSHEET, ("ods",)),
    FileFormat("csv", "CSV", SPREADSHEET, ("csv",)),
    FileFormat("pptx", "PPTX", PRESENTATION, ("pptx",)),
    FileFormat("ppt", "PPT", PRESENTATION, ("ppt", "pps")),
    FileFormat("odp", "ODP", PRESENTATION, ("odp",)),
    FileFormat("zip", "ZIP", ARCHIVE, ("zip",)),
    FileFormat("7z", "7Z", ARCHIVE, ("7z",)),
    FileFormat("tar", "TAR", ARCHIVE, ("tar",)),
    FileFormat("tgz", "TAR.GZ", ARCHIVE, ("tar.gz", "tgz")),
    FileFormat("txz", "TAR.XZ", ARCHIVE, ("tar.xz", "txz")),
    FileFormat("tbz", "TAR.BZ2", ARCHIVE, ("tar.bz2", "tbz2")),
    FileFormat("rar", "RAR", ARCHIVE, ("rar",)),
]

BY_ID = {f.id: f for f in _FORMATS}
_BY_EXT = {}
for _f in _FORMATS:
    for _e in _f.extensions:
        _BY_EXT[_e] = _f


def all_formats():
    return list(_FORMATS)


def by_id(format_id):
    return BY_ID.get(format_id.lower())


def extension_of(path):
    name = Path(path).name.lower()
    for ext in sorted((e for e in _BY_EXT if "." in e), key=len, reverse=True):
        if name.endswith("." + ext):
            return ext
    suffix = Path(name).suffix
    return suffix[1:] if suffix else ""


def name_without_extension(path):
    name = Path(path).name
    ext = extension_of(path)
    if ext and name.lower().endswith("." + ext):
        return name[: -len(ext) - 1]
    return name


def detect(path):
    return _BY_EXT.get(extension_of(path))


def extensions_of(category):
    return [e for f in _FORMATS if f.category == category for e in f.extensions]
