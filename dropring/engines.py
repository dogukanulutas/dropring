import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from . import formats
from .formats import AUDIO, SPREADSHEET, VIDEO
from .i18n import t


class ConversionError(Exception):
    pass


class Cancelled(Exception):
    pass


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    cost: int = 10
    start_only: bool = False
    final_only: bool = False


class StepContext:
    def __init__(self, work_dir, options=None, progress=None, cancel=None):
        self.work_dir = Path(work_dir)
        self.options = options or {}
        self.progress = progress or (lambda fraction: None)
        self.cancel = cancel or CancelToken()


class CancelToken:
    def __init__(self):
        self._lock = threading.Lock()
        self._processes = set()
        self.cancelled = False

    def check(self):
        if self.cancelled:
            raise Cancelled()

    def cancel(self):
        with self._lock:
            self.cancelled = True
            for process in list(self._processes):
                try:
                    process.kill()
                except OSError:
                    pass

    def add(self, process):
        with self._lock:
            if self.cancelled:
                process.kill()
            self._processes.add(process)

    def remove(self, process):
        with self._lock:
            self._processes.discard(process)


def run_process(args, ctx, on_line=None, env=None, cwd=None):
    ctx.cancel.check()
    process = subprocess.Popen(
        [str(a) for a in args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        errors="replace",
        env=env,
        cwd=cwd,
        start_new_session=True,
    )
    ctx.cancel.add(process)
    tail = []
    try:
        with process.stdout:
            for line in process.stdout:
                tail.append(line.rstrip())
                del tail[:-30]
                if on_line:
                    on_line(line.strip())
        process.wait()
    finally:
        ctx.cancel.remove(process)
    ctx.cancel.check()
    if process.returncode != 0:
        lines = [l.strip() for l in tail if l.strip() and not l.startswith(("frame=", "size=", "progress=", "out_time", "bitrate=", "speed="))]
        generic = ("Conversion failed!", "Error opening output files: Invalid argument")
        useful = [l for l in lines if "rror" in l and not l.endswith(generic)]
        message = (useful or lines or [""])[-1]
        raise ConversionError(f"{Path(str(args[0])).name}: {message or 'exit code ' + str(process.returncode)}")
    return tail


class Engine:
    id = ""
    name = ""
    binaries = ()
    concurrency = 4

    def available(self):
        return all(shutil.which(b) for b in self.binaries)

    @cached_property
    def edges(self):
        return self.build_edges() if self.available() else []

    def build_edges(self):
        return []

    def run(self, source, source_format, target_format, ctx):
        raise NotImplementedError

    def output_path(self, source, target_format, ctx):
        fmt = formats.by_id(target_format)
        return ctx.work_dir / f"{formats.name_without_extension(source)}.{fmt.extension}"


class ImageMagickEngine(Engine):
    id = "imagemagick"
    name = "ImageMagick"
    binaries = ("magick",)
    animated = {"gif", "webp"}
    codec_names = {
        "jpg": "JPEG", "png": "PNG", "webp": "WEBP", "avif": "AVIF", "heic": "HEIC", "jxl": "JXL",
        "tiff": "TIFF", "bmp": "BMP", "gif": "GIF", "ico": "ICO", "tga": "TGA", "ppm": "PPM",
        "psd": "PSD", "svg": "SVG", "jp2": "JP2", "qoi": "QOI", "dds": "DDS", "xcf": "XCF", "raw": "DNG",
    }

    def supported(self):
        try:
            output = subprocess.run(["magick", "-list", "format"], capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.TimeoutExpired):
            return set(), set()
        readable, writable = set(), set()
        modes = {}
        for line in output.splitlines():
            match = re.match(r"\s*([A-Z0-9-]+)\*?\s+\S+\s+([r-][w-][+-])", line)
            if match:
                modes[match.group(1)] = match.group(2)
        for fid, codec in self.codec_names.items():
            mode = modes.get(codec, "---")
            if mode[0] == "r":
                readable.add(fid)
            if mode[1] == "w" and fid not in ("svg", "raw", "xcf"):
                writable.add(fid)
        return readable, writable

    def build_edges(self):
        readable, writable = self.supported()
        edges = []
        for source in sorted(readable):
            for target in sorted(writable):
                edges.append(Edge(source, target, 10))
            edges.append(Edge(source, "pdf", 14, final_only=True))
        return edges

    def run(self, source, source_format, target_format, ctx):
        output = self.output_path(source, target_format, ctx)
        options = ctx.options
        read = str(source)
        if source_format == "svg":
            args = ["magick", "-background", "none", "-density", str(options.get("dpi", 150)), read]
        else:
            if target_format not in self.animated or source_format not in self.animated:
                read += "[0]"
            args = ["magick", read]
        if source_format != "svg":
            args.append("-auto-orient")
        if options.get("resize"):
            args += ["-resize", options["resize"]]
        if options.get("max_size"):
            args += ["-resize", f"{options['max_size']}x{options['max_size']}>"]
        if options.get("grayscale"):
            args += ["-colorspace", "Gray"]
        if options.get("rotate"):
            args += ["-rotate", str(options["rotate"])]
        if options.get("flop"):
            args.append("-flop")
        if options.get("strip"):
            args.append("-strip")
        if options.get("quality"):
            args += ["-quality", str(options["quality"])]
        if target_format == "jpg":
            args += ["-background", "white", "-alpha", "remove", "-alpha", "off"]
        if target_format == "ico":
            args += ["-resize", "256x256>", "-define", "icon:auto-resize=256,128,64,48,32,16"]
        if source_format in self.animated and target_format in self.animated:
            args = args[:2] + ["-coalesce"] + args[2:]
        args.append(str(output))
        run_process(args, ctx)
        return [output]


class FfmpegEngine(Engine):
    id = "ffmpeg"
    name = "FFmpeg"
    binaries = ("ffmpeg", "ffprobe")
    video_codecs = {
        "mp4": ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart"],
        "m4v": ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"],
        "mov": ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"],
        "mkv": ["-c:v", "libx264", "-c:a", "aac"],
        "webm": ["-c:v", "libvpx-vp9", "-b:v", "0", "-row-mt", "1", "-c:a", "libopus"],
        "avi": ["-c:v", "mpeg4", "-q:v", "4", "-c:a", "libmp3lame"],
        "wmv": ["-c:v", "wmv2", "-q:v", "4", "-c:a", "wmav2"],
        "flv": ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"],
        "mpeg": ["-c:v", "mpeg2video", "-q:v", "4", "-c:a", "mp2"],
        "ts": ["-c:v", "libx264", "-c:a", "aac", "-f", "mpegts"],
        "3gp": ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "1", "-ar", "22050"],
        "ogv": ["-c:v", "libtheora", "-q:v", "7", "-c:a", "libvorbis"],
    }
    audio_codecs = {
        "mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
        "wav": ["-c:a", "pcm_s16le"],
        "flac": ["-c:a", "flac"],
        "ogg": ["-c:a", "libvorbis", "-q:a", "5"],
        "opus": ["-c:a", "libopus", "-b:a", "128k"],
        "m4a": ["-c:a", "aac", "-b:a", "192k"],
        "aac": ["-c:a", "aac", "-b:a", "192k", "-f", "adts"],
        "wma": ["-c:a", "wmav2", "-b:a", "192k"],
        "aiff": ["-c:a", "pcm_s16be"],
    }
    crf_flag = {"libx264": ("-crf", 23), "libvpx-vp9": ("-crf", 32)}

    def encoders(self):
        try:
            output = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.TimeoutExpired):
            return set()
        return {m.group(1) for m in re.finditer(r"^\s[VAS.][A-Z.]{5}\s+(\S+)", output, re.M)}

    def _codecs_ok(self, args, encoders):
        return all(args[i + 1] in encoders for i, a in enumerate(args) if a in ("-c:v", "-c:a"))

    def build_edges(self):
        encoders = self.encoders()
        videos = [f.id for f in formats.all_formats() if f.category == VIDEO]
        audios = [f.id for f in formats.all_formats() if f.category == AUDIO]
        video_targets = [v for v in videos if self._codecs_ok(self.video_codecs[v], encoders)]
        audio_targets = [a for a in audios if self._codecs_ok(self.audio_codecs[a], encoders)]
        edges = []
        for source in videos:
            edges += [Edge(source, t, 10) for t in video_targets]
            edges += [Edge(source, t, 10) for t in audio_targets]
            if "gif" in encoders:
                edges.append(Edge(source, "gif", 12, final_only=True))
            edges.append(Edge(source, "jpg", 30, start_only=True, final_only=True))
            edges.append(Edge(source, "png", 30, start_only=True, final_only=True))
        for source in audios:
            edges += [Edge(source, t, 10) for t in audio_targets]
        for target in ("mp4", "webm", "mkv"):
            if target in video_targets:
                edges.append(Edge("gif", target, 10, start_only=True, final_only=True))
        if "libaom-av1" in encoders or "libsvtav1" in encoders:
            for source in ("png", "jpg", "bmp", "tiff"):
                edges.append(Edge(source, "avif", 12))
        edges.append(Edge("avif", "png", 12))
        return edges

    def duration(self, source):
        try:
            output = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(source)],
                capture_output=True, text=True, timeout=30,
            ).stdout
            return float(json.loads(output)["format"]["duration"])
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
            return 0.0

    def has_audio(self, source):
        try:
            output = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(source)],
                capture_output=True, text=True, timeout=30,
            ).stdout
            return bool(output.strip())
        except (OSError, subprocess.TimeoutExpired):
            return True

    def run(self, source, source_format, target_format, ctx):
        output = self.output_path(source, target_format, ctx)
        options = ctx.options
        source_category = formats.by_id(source_format).category
        target_category = formats.by_id(target_format).category
        base = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(source)]
        filters = []
        if options.get("scale"):
            filters.append(f"scale=trunc(iw*{options['scale']}/2)*2:-2")
        if options.get("grayscale"):
            filters.append("hue=s=0")

        if target_format == "avif":
            args = base + ["-frames:v", "1", "-c:v", "libaom-av1", "-still-picture", "1", "-crf", "30", str(output)]
            run_process(args, ctx)
            return [output]
        if source_format == "avif":
            args = base + ["-frames:v", "1", str(output)]
            run_process(args, ctx)
            return [output]
        if target_format in ("jpg", "png"):
            seek = max(0.0, self.duration(source) * 0.1)
            args = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-ss", f"{seek:.2f}", "-i", str(source), "-frames:v", "1"]
            if filters:
                args += ["-vf", ",".join(filters)]
            run_process(args + [str(output)], ctx)
            return [output]
        if target_format == "gif":
            fps = options.get("fps", 12)
            width = options.get("width", 480)
            chain = f"fps={fps},scale={width}:-1:flags=lanczos" + ("," + ",".join(filters) if filters else "")
            graph = f"{chain},split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4"
            args = base + ["-filter_complex", graph, "-loop", "0", str(output)]
        elif target_category == AUDIO:
            args = base + ["-vn"] + self.audio_codecs[target_format]
            if options.get("audio_bitrate") and target_format not in ("wav", "flac", "aiff"):
                args += ["-b:a", options["audio_bitrate"]]
            args.append(str(output))
        else:
            codec = list(self.video_codecs[target_format])
            if source_format == "gif":
                filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
            video_encoder = codec[codec.index("-c:v") + 1]
            crf = options.get("crf")
            if crf and video_encoder in self.crf_flag:
                codec += [self.crf_flag[video_encoder][0], str(crf)]
            elif video_encoder in self.crf_flag and "-crf" not in codec:
                codec += [self.crf_flag[video_encoder][0], str(self.crf_flag[video_encoder][1])]
            if video_encoder == "libx264":
                codec += ["-preset", options.get("x264_preset", "medium")]
            if options.get("mute") or source_format == "gif" or not self.has_audio(source):
                codec = self._without_audio(codec) + ["-an"]
            args = base + (["-vf", ",".join(filters)] if filters else []) + codec + [str(output)]

        if target_category == AUDIO and source_category == VIDEO and not self.has_audio(source):
            raise ConversionError(t("This video has no sound"))
        total = self.duration(source) if source_category in (VIDEO, AUDIO) else 0.0
        args = args[:-1] + ["-progress", "pipe:1", "-nostats", args[-1]]

        def on_line(line):
            if total > 0 and line.startswith("out_time_us="):
                try:
                    ctx.progress(min(1.0, int(line.split("=", 1)[1]) / 1e6 / total))
                except ValueError:
                    pass

        run_process(args, ctx, on_line)
        return [output]

    def _without_audio(self, codec):
        result = []
        skip = False
        for item in codec:
            if skip:
                skip = False
                continue
            if item in ("-c:a", "-b:a", "-q:a", "-ac", "-ar"):
                skip = True
                continue
            result.append(item)
        return result


class LibreOfficeEngine(Engine):
    id = "libreoffice"
    name = "LibreOffice"
    binaries = ("soffice",)
    concurrency = 2
    documents = ["docx", "doc", "odt", "rtf", "txt", "html"]
    document_targets = ["pdf", "docx", "doc", "odt", "rtf", "txt", "html", "epub"]
    sheets = ["xlsx", "xls", "ods", "csv"]
    sheet_targets = ["pdf", "xlsx", "xls", "ods", "csv", "html"]
    slides = ["pptx", "ppt", "odp"]
    slide_targets = ["pdf", "pptx", "ppt", "odp"]
    filters = {
        "txt": "txt:Text (encoded):UTF8",
        "csv": "csv:Text - txt - csv (StarCalc):44,34,76,1",
        "html": "html:HTML (StarWriter)",
        "epub": "epub",
    }

    def build_edges(self):
        edges = []
        for group, targets in ((self.documents, self.document_targets), (self.sheets, self.sheet_targets), (self.slides, self.slide_targets)):
            for source in group:
                edges += [Edge(source, t, 20) for t in targets if t != source]
        edges.append(Edge("pdf", "odt", 40, start_only=True))
        edges.append(Edge("pdf", "docx", 40, start_only=True))
        return edges

    def run(self, source, source_format, target_format, ctx):
        fmt = formats.by_id(target_format)
        source_category = formats.by_id(source_format).category
        convert = self.filters.get(target_format, fmt.extension)
        if target_format == "html" and source_category == SPREADSHEET:
            convert = "html:HTML (StarCalc)"
        if target_format == "pdf" and ctx.options.get("pdf_filter"):
            convert = ctx.options["pdf_filter"]
        out_dir = ctx.work_dir / "lo-out"
        out_dir.mkdir(exist_ok=True)
        profile = tempfile.mkdtemp(prefix="lo-profile-", dir=ctx.work_dir)
        args = ["soffice", f"-env:UserInstallation=file://{profile}", "--headless", "--norestore", "--nolockcheck"]
        if source_format == "pdf":
            args.append("--infilter=writer_pdf_import")
        elif source_format == "html":
            args.append("--infilter=HTML (StarWriter)")
        args += ["--convert-to", convert, "--outdir", str(out_dir), str(source)]
        run_process(args, ctx)
        produced = out_dir / f"{Path(source).stem}.{fmt.extension}"
        if not produced.exists():
            candidates = list(out_dir.glob(f"*.{fmt.extension}"))
            if not candidates:
                raise ConversionError("LibreOffice produced no output")
            produced = candidates[0]
        output = self.output_path(source, target_format, ctx)
        shutil.move(produced, output)
        shutil.rmtree(profile, ignore_errors=True)
        return [output]


class PopplerEngine(Engine):
    id = "poppler"
    name = "Poppler"
    binaries = ("pdftoppm", "pdftotext")

    def build_edges(self):
        return [Edge("pdf", t, 8, final_only=True) for t in ("png", "jpg", "tiff")] + [Edge("pdf", "txt", 8)]

    def run(self, source, source_format, target_format, ctx):
        stem = formats.name_without_extension(source)
        if target_format == "txt":
            output = ctx.work_dir / f"{stem}.txt"
            run_process(["pdftotext", "-layout", "-enc", "UTF-8", str(source), str(output)], ctx)
            return [output]
        flag = {"png": "-png", "jpg": "-jpeg", "tiff": "-tiff"}[target_format]
        pages_dir = ctx.work_dir / "pages"
        pages_dir.mkdir(exist_ok=True)
        args = ["pdftoppm", flag, "-r", str(ctx.options.get("dpi", 150))]
        if ctx.options.get("first_page_only"):
            args += ["-f", "1", "-l", "1"]
        args += [str(source), str(pages_dir / stem)]
        run_process(args, ctx)
        pages = sorted(pages_dir.glob(f"{glob_escape(stem)}-*"), key=lambda p: int(re.findall(r"-(\d+)\.\w+$", p.name)[0]))
        if not pages:
            raise ConversionError("pdftoppm produced no pages")
        return pages


class GhostscriptEngine(Engine):
    id = "ghostscript"
    name = "Ghostscript"
    binaries = ("gs",)

    def build_edges(self):
        return [Edge("pdf", "pdf", 10)]

    def run(self, source, source_format, target_format, ctx):
        output = ctx.work_dir / f"{formats.name_without_extension(source)}.pdf"
        quality = ctx.options.get("pdf_quality", "ebook")
        args = [
            "gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.6", f"-dPDFSETTINGS=/{quality}",
            "-dNOPAUSE", "-dBATCH", "-dSAFER", "-dQUIET", f"-sOutputFile={output}", str(source),
        ]
        run_process(args, ctx)
        return [output]

    def merge(self, sources, output, ctx):
        args = ["gs", "-sDEVICE=pdfwrite", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-dQUIET", f"-sOutputFile={output}"]
        run_process(args + [str(s) for s in sources], ctx)


class TextEngine(Engine):
    id = "text"
    name = "Built-in text"

    def available(self):
        return True

    def build_edges(self):
        return [Edge("md", "txt", 2), Edge("txt", "md", 2), Edge("md", "html", 5), Edge("csv", "txt", 30, final_only=True)]

    def run(self, source, source_format, target_format, ctx):
        output = self.output_path(source, target_format, ctx)
        text = Path(source).read_text(encoding="utf-8", errors="replace")
        if target_format == "html":
            output.write_text(markdown_to_html(text, formats.name_without_extension(source)), encoding="utf-8")
        else:
            output.write_text(text, encoding="utf-8")
        return [output]


class ArchiveEngine(Engine):
    id = "archive"
    name = "Archives"
    writable = ["zip", "tar", "tgz", "txz", "tbz"]
    tar_modes = {"tar": "w", "tgz": "w:gz", "txz": "w:xz", "tbz": "w:bz2"}

    def available(self):
        return True

    def build_edges(self):
        readable = ["zip", "tar", "tgz", "txz", "tbz"]
        targets = list(self.writable)
        if shutil.which("7z"):
            readable += ["7z", "rar"]
            targets.append("7z")
        return [Edge(s, t, 10) for s in readable for t in targets if s != t]

    def run(self, source, source_format, target_format, ctx):
        folder = ctx.work_dir / "extracted"
        self.extract(source, source_format, folder, ctx)
        output = self.output_path(source, target_format, ctx)
        self.pack(list(folder.iterdir()), target_format, output, ctx)
        return [output]

    def extract(self, source, source_format, folder, ctx):
        folder.mkdir(parents=True, exist_ok=True)
        if source_format == "zip":
            with zipfile.ZipFile(source) as archive:
                archive.extractall(folder)
        elif source_format in self.tar_modes:
            with tarfile.open(source) as archive:
                archive.extractall(folder, filter="data")
        else:
            run_process(["7z", "x", "-y", "-snl-", f"-o{folder}", str(source)], ctx)
        root = folder.resolve()
        for path in folder.rglob("*"):
            if path.is_symlink() and not path.resolve().is_relative_to(root):
                path.unlink()

    def pack(self, paths, target_format, output, ctx):
        if target_format == "zip":
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in paths:
                    ctx.cancel.check()
                    if path.is_dir():
                        for child in sorted(path.rglob("*")):
                            archive.write(child, child.relative_to(path.parent))
                    else:
                        archive.write(path, path.name)
        elif target_format in self.tar_modes:
            with tarfile.open(output, self.tar_modes[target_format]) as archive:
                for path in paths:
                    ctx.cancel.check()
                    archive.add(path, path.name)
        elif target_format == "7z":
            run_process(["7z", "a", "-y", str(output)] + [str(p) for p in paths], ctx)
        else:
            raise ConversionError(f"Cannot write {target_format}")


def glob_escape(text):
    return re.sub(r"([*?\[\]])", r"[\1]", text)


def markdown_to_html(text, title):
    import html

    try:
        import markdown

        body = markdown.markdown(text, extensions=["tables", "fenced_code"])
    except ImportError:
        lines = []
        in_list = False
        in_code = False
        for raw in text.splitlines():
            if raw.strip().startswith("```"):
                lines.append("</pre>" if in_code else "<pre>")
                in_code = not in_code
                continue
            if in_code:
                lines.append(html.escape(raw))
                continue
            line = html.escape(raw)
            line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
            line = re.sub(r"(?<!\*)\*(?!\*)(.+?)\*", r"<em>\1</em>", line)
            line = re.sub(r"`(.+?)`", r"<code>\1</code>", line)
            line = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', line)
            heading = re.match(r"(#{1,6})\s+(.*)", line)
            item = re.match(r"\s*[-*+]\s+(.*)", line)
            if item and not in_list:
                lines.append("<ul>")
                in_list = True
            if not item and in_list:
                lines.append("</ul>")
                in_list = False
            if heading:
                level = len(heading.group(1))
                lines.append(f"<h{level}>{heading.group(2)}</h{level}>")
            elif item:
                lines.append(f"<li>{item.group(1)}</li>")
            elif line.strip():
                lines.append(f"<p>{line}</p>")
        if in_list:
            lines.append("</ul>")
        body = "\n".join(lines)
    return f'<!doctype html>\n<html><head><meta charset="utf-8"><title>{html.escape(title)}</title></head>\n<body>\n{body}\n</body></html>\n'


_ENGINES = None
_LOCK = threading.Lock()


def all_engines():
    global _ENGINES
    with _LOCK:
        if _ENGINES is None:
            _ENGINES = [
                PopplerEngine(), ImageMagickEngine(), FfmpegEngine(), LibreOfficeEngine(),
                GhostscriptEngine(), TextEngine(), ArchiveEngine(),
            ]
        return _ENGINES


def engine(engine_id):
    return next((e for e in all_engines() if e.id == engine_id), None)


def extract_archive(source, source_format, folder, ctx):
    engine("archive").extract(source, source_format, folder, ctx)


def pack_archive(paths, target_format, output, ctx):
    engine("archive").pack(paths, target_format, output, ctx)


def merge_pdfs(sources, output, ctx):
    if shutil.which("pdfunite"):
        run_process(["pdfunite"] + [str(s) for s in sources] + [str(output)], ctx)
    elif engine("ghostscript").available():
        engine("ghostscript").merge(sources, output, ctx)
    else:
        raise ConversionError("pdfunite or Ghostscript is needed to merge PDFs")


def environment_summary():
    return [(e.name, e.available(), ", ".join(e.binaries) or "built in") for e in all_engines()]


os.environ.setdefault("MAGICK_THREAD_LIMIT", "2")
