#!/usr/bin/env python3
import sys
import os
import json
import shutil
import subprocess
import threading
import itertools
import time
from datetime import datetime
from pathlib import Path

HOME = Path.home()
BASE = HOME / "Desktop" / "URL-INFO"
CONFIG_DIR = HOME / ".config" / "urlinfo"
CONFIG_FILE = CONFIG_DIR / "download-settings.json"
QUEUE_FILE = CONFIG_DIR / "download-queue.json"
HISTORY_FILE = CONFIG_DIR / "download-history.json"

CONFIG_DIR.mkdir(parents=True, exist_ok=True)

BACK = "BACK"
EXIT = "EXIT"

# TERMINAL COLORS
RESET = "\033[0m"
CYAN = "\033[96m"
BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
PURPLE = "\033[95m"
BOLD = "\033[1m"

def paint(value, color):
    value = str(value)
    if sys.stdout.isatty():
        return f"{color}{value}{RESET}"
    return value


# ==============================
# SCREEN UI
# ==============================

def clear_screen():
    if sys.stdout.isatty():
        os.system("clear")


def page(title, subtitle=""):
    clear_screen()
    print(paint("=" * 40, CYAN))
    print(paint("       URL DOWNLOAD MANAGER", BOLD + CYAN))
    print(paint("=" * 40, CYAN))
    if subtitle:
        print(paint(subtitle, PURPLE))
    print()
    print(paint("       " + title, BOLD + GREEN))
    print(paint("-" * 40, BLUE))


def blue_input(prompt=""):
    return input(paint(prompt, BLUE))


def pause(message="Press Enter to continue..."):
    input("\n" + message)



# ==============================
# INTERACTIVE ARROW MENU
# ==============================

def choose_menu(title, options, allow_back=True):
    import select
    import termios
    import tty

    if not options:
        return ""

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        for key, label in options:
            print(f"[{key}] {label}")
        return input("Select: ").strip()

    selected = 0
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    width = min(96, max(32, shutil.get_terminal_size((80, 24)).columns - 2))
    inner = width - 2

    def row(value=""):
        return "│" + str(value)[:inner].ljust(inner) + "│"

    def border(left, fill, right):
        return left + fill * inner + right

    def draw():
        lines = [
            paint(border("┌", "─", "┐"), CYAN),
            row(),
            paint(row("  URL DOWNLOAD MANAGER"), BOLD + CYAN),
            row(),
            paint(border("├", "─", "┤"), CYAN),
            row(),
            paint(row("  " + title), BOLD + GREEN),
            row(),
            paint(row("  ↑ / ↓ Move   ENTER Select   Number + ENTER"), CYAN),
            row()
        ]

        for i, (key, label) in enumerate(options):
            prefix = "  › " if i == selected else "    "
            content = f"[{key}] {label}"
            padding = max(0, inner - len(prefix) - len(content))

            if i == selected:
                content = paint(content, "\033[1;30;46m")

            lines.append("│" + prefix + content + " " * padding + "│")

        lines.extend([
            row(),
            paint(border("└", "─", "┘"), CYAN)
        ])

        sys.stdout.write("\033[2J\033[H" + "\n".join(lines) + "\033[J")
        sys.stdout.flush()

    try:
        tty.setcbreak(fd)

        while True:
            draw()
            first = sys.stdin.read(1)

            if first in ("\r", "\n"):
                return options[selected][0]

            if first == "\x1b":
                ready, _, _ = select.select([sys.stdin], [], [], 0.2)
                if not ready:
                    continue

                second = sys.stdin.read(1)
                if second != "[":
                    continue

                ready, _, _ = select.select([sys.stdin], [], [], 0.2)
                if not ready:
                    continue

                third = sys.stdin.read(1)

                if third == "A":
                    selected = (selected - 1) % len(options)
                elif third == "B":
                    selected = (selected + 1) % len(options)

                continue

            if first.isdigit():
                found = next(
                    (i for i, item in enumerate(options) if item[0] == first),
                    None
                )
                if found is not None:
                    selected = found

    except OSError as error:
        print("Menu Error:", error)
        return ""

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        sys.stdout.write("\033[?1000l\033[?1006l")
        sys.stdout.flush()


# ==============================
# DOWNLOAD ENGINE
# ==============================

def load_config():
    if not CONFIG_FILE.exists():
        return {}

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            return {}

        # पुरानी single-format settings को सुरक्षित रखें।
        if data.get("format") in ("mp3", "mp4", "image", "other"):
            kind = data["format"]
            if data.get("quality") and data.get("folder"):
                return {kind: data}
            return {}

        configs = {}
        for kind in ("mp3", "mp4", "image", "other"):
            item = data.get(kind)
            if (
                isinstance(item, dict)
                and item.get("format") == kind
                and item.get("quality")
                and item.get("folder")
            ):
                configs[kind] = item

        return configs

    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def save_config(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    temporary = CONFIG_FILE.with_suffix(".tmp")

    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(config, file, indent=4)

    temporary.replace(CONFIG_FILE)


def format_speed(value):
    try:
        speed = float(value)

        if speed >= 1_000_000:
            return f"{speed / 1_000_000:.2f} MB/s"

        return f"{speed / 1000:.1f} KB/s"

    except (ValueError, TypeError):
        return "Unknown"


def run_download(command):
    import shlex

    if isinstance(command, str):
        command = shlex.split(command)
    else:
        command = list(command)

    run_download._progress_started = False

    progress_template = (
        "download:@@PROGRESS@@%(progress._percent_str)s|"
        "%(progress.downloaded_bytes)s|"
        "%(progress.total_bytes)s|"
        "%(progress.speed)s|%(progress.eta)s"
    )

    command[1:1] = [
        "--progress",
        "--newline",
        "--progress-template", progress_template,
        "--print", "after_move:@@SAVED@@%(filepath)s"
    ]

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    saved_path = None

    try:
        for line in process.stdout:
            line = line.rstrip()

            if "@@SAVED@@" in line:
                saved_path = line.split("@@SAVED@@", 1)[1].strip()
                continue

            if "@@PROGRESS@@" in line:
                parts = line.split("@@PROGRESS@@", 1)[1].split("|")

                if len(parts) == 5:
                    percent, downloaded, total, speed, eta = parts

                    try:
                        downloaded_text = human_size(float(downloaded))
                    except (ValueError, TypeError):
                        downloaded_text = "Unknown"

                    try:
                        total_text = human_size(float(total))
                    except (ValueError, TypeError):
                        total_text = "Unknown"

                    try:
                        downloaded_bytes = float(downloaded)
                        total_bytes = float(total)
                        remaining_bytes = max(0, total_bytes - downloaded_bytes)
                        remaining_text = human_size(remaining_bytes)
                        percentage = float(percent.replace("%", "").strip())
                    except (ValueError, TypeError):
                        remaining_text = "Unknown"
                        percentage = 0.0

                    bar_width = 24
                    filled = int(bar_width * min(100, max(0, percentage)) / 100)
                    bar = "#" * filled + "-" * (bar_width - filled)

                    lines = [
                        paint("● LIVE DOWNLOADING", CYAN),
                        paint(
                            f"Progress   : [{bar}] {percent.strip()}",
                            GREEN
                        ),
                        paint("Downloaded : " + downloaded_text, CYAN),
                        paint("Total Size : " + total_text, PURPLE),
                        paint("Remaining  : " + remaining_text, YELLOW),
                        paint(
                            "Speed      : " + format_speed(speed)
                            + " | ETA: " + eta.strip(),
                            GREEN
                        )
                    ]

                    if sys.stdout.isatty():
                        if getattr(run_download, "_progress_started", False):
                            print("\033[6A\033[J", end="")
                        print("\n".join(lines), flush=True)
                        run_download._progress_started = True
                    else:
                        print(" | ".join(lines), flush=True)
                continue

            print(line)

        process.wait()
        print()

        return process.returncode, saved_path

    except KeyboardInterrupt:
        process.terminate()

        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

        raise


# ==============================
# QUEUE STORAGE
# ==============================


def record_download(url, title, path, category):
    if not path:
        return
    try:
        records = json.loads(HISTORY_FILE.read_text(encoding="utf-8")) if HISTORY_FILE.exists() else []
        if not isinstance(records, list):
            records = []
        records.append({
            "url": url,
            "title": str(title or Path(path).name),
            "path": str(path),
            "category": category,
            "date": datetime.now().astimezone().isoformat(timespec="seconds")
        })
        temporary = HISTORY_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(HISTORY_FILE)
    except (OSError, ValueError, TypeError):
        pass


def show_download_history():
    try:
        records = json.loads(HISTORY_FILE.read_text(encoding="utf-8")) if HISTORY_FILE.exists() else []
    except (OSError, ValueError):
        records = []
    if not isinstance(records, list):
        records = []

    counts = {"video": 0, "audio": 0, "image": 0, "other": 0}
    for item in records:
        category = item.get("category", "other")
        counts[category] = counts.get(category, 0) + 1

    page("DOWNLOAD HISTORY")
    print("TOTAL DOWNLOADS:", len(records))
    print("VIDEO:", counts["video"], "| AUDIO:", counts["audio"])
    print("IMAGE:", counts["image"], "| OTHER:", counts["other"])
    print("\n" + "-" * 38)

    for number, item in enumerate(reversed(records), 1):
        print(f"\n{number}. [{item.get('category', 'other').upper()}] {item.get('title', 'Unknown')}")
        print("Saved:", item.get("path", "Unknown"))
        print("Date:", item.get("date", "Unknown"))
        print("URL:", item.get("url", ""))

    if not records:
        print("No successful downloads yet.")
    pause()


def save_queue(urls, index, config, completed=None, failed=None):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "urls": urls,
        "index": index,
        "config": config,
        "completed": completed or [],
        "failed": failed or []
    }
    temporary = QUEUE_FILE.with_suffix(".tmp")
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)
    temporary.replace(QUEUE_FILE)


def load_queue():
    if not QUEUE_FILE.exists():
        return None
    try:
        with open(QUEUE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            return None
        urls = data.get("urls")
        index = data.get("index", 0)
        if not isinstance(urls, list) or not isinstance(index, int):
            return None
        if index < 0 or index >= len(urls):
            return None
        return data
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def auto_download_config(url, configs):
    file_info = inspect_direct_file(url)
    if file_info:
        category = "image" if file_info["mime"].startswith("image/") else "other"
        kind = category
    else:
        media = inspect_url(url)
        if not media:
            return None, None
        if media.get("has_video"):
            kind, category = "mp4", "video"
        elif media.get("has_audio"):
            kind, category = "mp3", "audio"
        else:
            return None, None

    config = configs.get(kind)
    if not config:
        config = {
            "format": kind,
            "quality": "best" if kind in ("mp3", "mp4") else "original",
            "folder": str(default_folder(kind))
        }
    return config, category


def default_download(configs):
    while True:
        page("DEFAULT DOWNLOAD")
        print("Automatic media detection enabled.")
        print("Video | Audio | Image | Documents")
        print("\nPaste one URL.")
        print("[Enter] Back")
        print("[0] Back to URL INFO")
        url = blue_input("URL: ")
        if not url:
            return
        if url == "0":
            return EXIT
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        config, category = auto_download_config(url, configs)
        if not config:
            success, saved_path, title, folder = False, None, "Could not detect supported media", ""
        else:
            folder = config["folder"]
            success, saved_path, title = download_one(url, config)
            if success:
                record_download(url, title, saved_path, category)

        choice = completion_screen(success, title, saved_path, folder)
        if choice == "0":
            return EXIT


def multiple_download(configs, urls=None, start_index=0,
                      completed=None, failed=None):
    if urls is None:
        page("ALL DOWNLOAD")
        print("Automatic detection for every URL.")
        print("Paste URLs one per line.")
        print("Press Enter on an empty line when finished.")
        print("[Type 0 on the first line to cancel]\\n")
        urls = []
        while True:
            value = blue_input("URL: ")
            if not value:
                break
            if value == "0" and not urls:
                return
            if not value.startswith(("http://", "https://")):
                value = "https://" + value
            urls.append(value)
        if not urls:
            return
        start_index, completed, failed = 0, [], []
        save_queue(urls, start_index, configs, completed, failed)

    completed = completed or []
    failed = failed or []
    total = len(urls)

    for index in range(start_index, total):
        url = urls[index]
        save_queue(urls, index, configs, completed, failed)
        page(f"QUEUE ITEM {index + 1} / {total}", "AUTOMATIC MEDIA DETECTION")
        try:
            config, category = auto_download_config(url, configs)
            if config:
                success, saved_path, title = download_one(url, config)
            else:
                success, saved_path, title = False, None, "Could not detect supported media"
        except KeyboardInterrupt:
            save_queue(urls, index, configs, completed, failed)
            page("QUEUE PAUSED")
            print("Queue progress has been saved.")
            return

        if success:
            record_download(url, title, saved_path, category)
            completed.append({"url": url, "title": title, "path": saved_path})
        else:
            failed.append({"url": url, "reason": title})

        save_queue(urls, index + 1, configs, completed, failed)
        page("QUEUE PROGRESS")
        print(f"Finished: {index + 1} / {total}")
        print("Completed:", len(completed))
        print("Failed:", len(failed))

    QUEUE_FILE.unlink(missing_ok=True)
    page("QUEUE COMPLETED")
    print("TOTAL URLS:", total)
    print("COMPLETED:", len(completed))
    print("FAILED:", len(failed))
    print("\\n[Enter] Main Menu")
    print("[0] Back to URL INFO")
    return input("Select: ").strip()


def resume_queue():
    data = load_queue()

    if not data:
        return

    page("RESUME DOWNLOAD QUEUE")

    print("Total URLs:", len(data["urls"]))
    print("Next URL:", data["index"] + 1)
    print("Completed:", len(data.get("completed", [])))
    print("Failed:", len(data.get("failed", [])))

    print("\n[1] Resume Queue")
    print("[2] Cancel Saved Queue")
    print("[0] Back")

    choice = input("\nSelect: ").strip()

    if choice == "1":
        result = multiple_download(
            data["config"],
            data["urls"],
            data["index"],
            data.get("completed", []),
            data.get("failed", [])
        )
        return result

    if choice == "2":
        QUEUE_FILE.unlink(missing_ok=True)
        page("QUEUE CANCELLED")
        print("Saved queue removed.")
        pause()




# ==============================
# MAIN MENU
# ==============================

def main_menu(config=None):
    while True:
        configs = load_config()
        pending = load_queue()
        options = [
            ("1", "SINGLE DOWNLOAD"),
            ("2", "DEFAULT DOWNLOAD"),
            ("3", "ALL DOWNLOAD"),
            ("4", "DOWNLOAD SETTINGS"),
            ("5", "DOWNLOAD HISTORY")
        ]
        if pending:
            options.append(("6", "RESUME QUEUE"))
        options.append(("0", "BACK TO URL INFO"))

        choice = choose_menu("URL DOWNLOAD MANAGER", options)

        if choice == "0":
            return
        elif choice == "1":
            if single_download() == EXIT:
                return
        elif choice == "2":
            if default_download(configs) == EXIT:
                return
        elif choice == "3":
            if multiple_download(configs) == "0":
                return
        elif choice == "4":
            setup_settings()
        elif choice == "5":
            show_download_history()
        elif choice == "6" and pending:
            if resume_queue() == "0":
                return


class LiveSpinner:
    def __init__(self, messages):
        self.messages = messages
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        if not sys.stdout.isatty():
            print(self.messages[0], flush=True)
            return

        def animate():
            frames = itertools.cycle(
                ["⠋", "⠙", "⠹", "⠸", "⠼",
                 "⠴", "⠦", "⠧", "⠇", "⠏"]
            )
            index = 0

            while not self.stop_event.is_set():
                frame = next(frames)
                message = self.messages[index % len(self.messages)]

                print(
                    f"\r\033[K{frame} {message}",
                    end="",
                    flush=True
                )

                index += 1
                self.stop_event.wait(0.12)

        self.thread = threading.Thread(target=animate, daemon=True)
        self.thread.start()

    def stop(self, success=True):
        self.stop_event.set()

        if self.thread:
            self.thread.join()

        if sys.stdout.isatty():
            print("\r\033[K", end="", flush=True)

        print(
            "✓ CHECK COMPLETE" if success else "✗ CHECK FAILED",
            flush=True
        )

IMAGE_MIME_EXTENSIONS = {
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/webp': '.webp',
}

def download_direct_image(url, folder):
    """Save a public direct JPG, PNG, or WEBP image without recompression."""
    import urllib.request
    import urllib.parse
    try:
        parsed = urllib.parse.urlparse(url.strip())
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            return False, 'Only public HTTP/HTTPS URLs are supported.'
        req = urllib.request.Request(url.strip(), headers={'User-Agent': 'Mozilla/5.0 URL-Download-Manager'})
        with urllib.request.urlopen(req, timeout=30) as response:
            final = urllib.parse.urlparse(response.geturl())
            if final.scheme not in ('http', 'https'):
                return False, 'Redirected URL is not HTTP/HTTPS.'
            ext = IMAGE_MIME_EXTENSIONS.get(response.headers.get_content_type().lower())
            if not ext:
                return False, 'Unsupported image type. Allowed: JPG, PNG, WEBP.'
            size = response.headers.get('Content-Length')
            if size and int(size) > 50 * 1024 * 1024:
                return False, 'Image exceeds 50 MB.'
            folder = Path(folder).expanduser()
            folder.mkdir(parents=True, exist_ok=True)
            name = Path(urllib.parse.unquote(final.path)).stem or 'image'
            name = ''.join(c for c in name if c.isalnum() or c in '-_.').strip('._') or 'image'
            dest = folder / (name + ext)
            n = 1
            while dest.exists():
                dest = folder / (name + '_' + str(n) + ext)
                n += 1
            part = dest.with_name(dest.name + '.part')
            total = 0
            try:
                with part.open('wb') as out:
                    while True:
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > 50 * 1024 * 1024:
                            raise ValueError('Image exceeds 50 MB.')
                        out.write(chunk)
                if not total:
                    raise ValueError('Image is empty.')
                part.replace(dest)
            except Exception:
                part.unlink(missing_ok=True)
                raise
        return True, str(dest)
    except Exception as exc:
        return False, str(exc)

def default_folder(kind):
    shared = HOME / "storage" / "shared"

    if not shared.is_dir():
        shared = BASE / "Downloads"

    folders = {"mp3": "Music", "mp4": "Movies", "image": "Pictures"}
    return shared / folders.get(kind, "Downloads")

def setup_settings(kind=None):
    configs = load_config()

    if kind is None:
        while True:
            page("DEFAULT DOWNLOAD SETTINGS")
            print("[1] MP3 - Audio")
            print("[2] MP4 - Video")
            print("[3] IMAGE - Original")
            print("[0] Back to URL INFO")

            choice = input("\nSelect Format: ").strip()

            if choice in ("", "0"):
                return BACK
            if choice == "1":
                kind = "mp3"
                break
            if choice == "2":
                kind = "mp4"
                break
            if choice == "3":
                kind = "image"
                break

    if kind not in ("mp3", "mp4", "image"): 
        return BACK

    while kind != "image":
        page("DEFAULT QUALITY")
        print("Format:", kind.upper())

        if kind == "mp4":
            print("[1] 360p")
            print("[2] 480p")
            print("[3] 720p")
            print("[4] 1080p")
            print("[5] BEST AVAILABLE")
            qualities = {
                "1": "360p", "2": "480p", "3": "720p",
                "4": "1080p", "5": "best"
            }
        else:
            print("[1] 128 Kbps")
            print("[2] 192 Kbps")
            print("[3] 320 Kbps")
            print("[4] BEST QUALITY")
            qualities = {
                "1": "128K", "2": "192K",
                "3": "320K", "4": "best"
            }

        print("\n[Enter] Back")
        print("[0] Back to URL INFO")
        choice = input("\nSelect Quality: ").strip()

        if choice == "":
            return setup_settings(kind)
        if choice == "0":
            return BACK
        if choice in qualities:
            quality = qualities[choice]
            break

    if kind == "image":
        quality = "original"

    folder = default_folder(kind)

    while True:
        page("DEFAULT SAVE LOCATION")
        print("Format:", kind.upper())
        print("[1] Default:", folder)
        print("[2] Custom Folder")
        print("\n[Enter] Back")
        print("[0] Back to URL INFO")

        choice = input("\nSelect Location: ").strip()

        if choice == "":
            return setup_settings(kind)
        if choice == "0":
            return BACK

        if choice == "1":
            selected = folder
        elif choice == "2":
            custom = input("\nEnter folder path: ").strip()
            if not custom:
                continue
            selected = Path(custom).expanduser()
        else:
            continue

        try:
            selected.mkdir(parents=True, exist_ok=True)
            break
        except OSError as error:
            print("Folder Error:", error)
            pause()

    config = {
        "format": kind,
        "quality": quality,
        "folder": str(selected)
    }

    configs[kind] = config
    save_config(configs)

    page("SETTINGS SAVED")
    print("Default settings saved successfully.")
    print("Format:", kind.upper())
    print("Quality:", quality)
    print("Folder:", selected)
    pause()

    return configs

def human_size(value):
    if value is None or value <= 0:
        return "Size unknown"

    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f} GB"

    return f"{value / 1_000_000:.1f} MB"

def estimate_format(fmt, duration):
    size = fmt.get("filesize") or fmt.get("filesize_approx")

    if size:
        return size

    bitrate = fmt.get("tbr") or fmt.get("abr")

    if bitrate and duration:
        return bitrate * 1000 * duration / 8

    return None

def is_direct_image_url(url):
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 URL-Download-Manager"}, method="HEAD")
        with urllib.request.urlopen(req, timeout=12) as response:
            final = response.geturl()
            import urllib.parse
            parsed = urllib.parse.urlparse(final)
            if parsed.scheme not in ("http", "https"):
                return False
            return response.headers.get_content_type().lower() in IMAGE_MIME_EXTENSIONS
    except Exception:
        return False

def inspect_url(url):
    try:
        import urllib.parse
        import urllib.request
        import re
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if host == "pin.it" or host.endswith(".pin.it") or host == "pinterest.com" or host.endswith(".pinterest.com"):
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as response:
                final_url = response.geturl()
            match = re.search(r"/pin/(\d+)", final_url)
            if not match:
                result = subprocess.run(["gallery-dl", "--get-urls", url], capture_output=True, text=True, timeout=45)
                match = re.search(r"/pin/(\d+)", result.stdout) if result.returncode == 0 else None
            if match:
                url = "https://www.pinterest.com/pin/" + match.group(1) + "/"
    except Exception:
        pass

    spinner = LiveSpinner([
        "CONNECTING TO SOURCE",
        "FETCHING MEDIA INFORMATION",
        "ANALYZING AVAILABLE FORMATS",
        "PREPARING QUALITY OPTIONS"
    ])

    spinner.start()

    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--dump-single-json",
                "--no-warnings",
                "--no-playlist",
                url
            ],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            spinner.stop(False)
            print("\nCould not read media information.")

            if result.stderr:
                print(result.stderr.strip()[-1200:])

            return None

        info = json.loads(result.stdout)
        formats = info.get("formats") or []
        duration = info.get("duration") or 0

        videos = [
            item for item in formats
            if item.get("vcodec") not in (None, "none")
        ]

        audios = [
            item for item in formats
            if item.get("acodec") not in (None, "none")
            and item.get("vcodec") in (None, "none")
        ]

        if not audios:
            audios = [
                item for item in formats
                if item.get("acodec") not in (None, "none")
            ]

        has_video = bool(videos) or info.get("vcodec") not in (None, "none")

        has_audio = (
            bool(audios)
            or info.get("acodec") not in (None, "none")
            or has_video
        )

        heights = sorted({
            int(item["height"])
            for item in videos
            if item.get("height")
        })

        audio_sizes = [
            estimate_format(item, duration)
            for item in audios
        ]

        audio_size = max(
            (size for size in audio_sizes if size),
            default=None
        )

        quality_sizes = {}

        for height in heights:
            candidates = [
                item for item in videos
                if item.get("height") == height
            ]

            estimates = []

            for item in candidates:
                size = estimate_format(item, duration)

                if size and item.get("acodec") in (None, "none"):
                    if audio_size:
                        size += audio_size
                    else:
                        size = None

                if size:
                    estimates.append(size)

            quality_sizes[height] = max(estimates, default=None)

        mp3_sizes = {}

        for kbps in (64, 96, 128, 160, 192, 256, 320):
            mp3_sizes[kbps] = (
                duration * kbps * 1000 / 8 if duration else None
            )

        mp3_best_size = (
            duration * 245 * 1000 / 8 if duration else None
        )

        spinner.stop(True)

        print("\n✓ URL RECEIVED")
        print("✓ VIDEO FORMATS DETECTED" if has_video else "• NO VIDEO FORMAT")
        print("✓ AUDIO FORMATS DETECTED" if has_audio else "• NO AUDIO FORMAT")
        print("✓ QUALITY OPTIONS PREPARED")

        print("\nTitle:", info.get("title", "Unknown"))
        print(
            "Duration:",
            f"{duration:.1f} seconds" if duration else "Unknown"
        )

        return {
            "title": info.get("title", "Unknown"),
            "has_video": has_video,
            "has_audio": has_audio,
            "heights": heights,
            "quality_sizes": quality_sizes,
            "mp3_sizes": mp3_sizes,
            "mp3_best_size": mp3_best_size,
            "duration": duration
        }

    except Exception as error:
        spinner.stop(False)
        print("Check Error:", error)
        return None

def make_download_options(config, media):
    kind = config["format"]
    quality = config["quality"]

    if kind == "mp3":
        if not media["has_audio"]:
            return None, None

        if quality == "best":
            return (
                ["-x", "--audio-format", "mp3", "--audio-quality", "0"],
                media.get("mp3_best_size")
            )

        try:
            kbps = int(quality.replace("K", ""))
        except ValueError:
            kbps = 128

        return (
            [
                "-x",
                "--audio-format", "mp3",
                "--audio-quality", f"{kbps}K"
            ],
            media["mp3_sizes"].get(kbps)
        )

    if not media["has_video"]:
        return None, None

    heights = media["heights"]

    if quality == "best":
        size = (
            media["quality_sizes"].get(max(heights))
            if heights else None
        )

        return (
            ["-f", "bv*+ba/b", "--merge-output-format", "mp4"],
            size
        )

    try:
        target = int(quality.replace("p", ""))
    except ValueError:
        target = 720

    available = [height for height in heights if height <= target]

    if available:
        selected_height = max(available)
    elif heights:
        selected_height = min(heights)
    else:
        return None, None

    fmt = (
        f"bv*[height<={selected_height}]+"
        f"ba/b[height<={selected_height}]"
    )

    return (
        ["-f", fmt, "--merge-output-format", "mp4"],
        media["quality_sizes"].get(selected_height)
    )

def show_storage(folder, estimated_size):
    print("\n---------- STORAGE CHECK ----------")
    print("Save Location:", folder)

    try:
        free = shutil.disk_usage(folder).free
        print("Available Storage:", human_size(free))

        if estimated_size:
            required = estimated_size * 1.10

            print("Estimated File Size:", human_size(estimated_size))
            print(
                "Space Needed (with 10% margin):",
                human_size(required)
            )

            if free < required:
                print("WARNING: Available storage may be insufficient.")
            else:
                print("Storage Check: Enough space appears available.")

        else:
            print("Estimated File Size: Size unknown")
            print("Required Space: Cannot estimate")

    except OSError as error:
        print("Storage Check Error:", error)

def completion_screen(success, title, saved_path, folder):
    page(
        "DOWNLOAD COMPLETED" if success else "DOWNLOAD FAILED"
    )

    if success:
        print(paint("✓ DOWNLOAD COMPLETED!", GREEN))
    else:
        print(paint("✗ DOWNLOAD DID NOT COMPLETE", RED))

    print("\nFile:", title)
    print("Save Location:", saved_path or folder)

    print("\n[Enter] Continue")
    print("[0] Back to URL INFO")

    return input("\nSelect: ").strip()

def download_one(url, config):
    import urllib.parse
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host == "pin.it" or host.endswith(".pin.it") or host == "pinterest.com" or host.endswith(".pinterest.com"):
        image_url = extract_pinterest_image_url(url) if config.get("format") == "image" else None
        if image_url:
            url = image_url
        else:
            try:
                import re
                result = subprocess.run(["gallery-dl", "--get-urls", url], capture_output=True, text=True, timeout=45)
                match = re.search(r"/pin/(\d+)", result.stdout) if result.returncode == 0 else None
                if match:
                    url = "https://www.pinterest.com/pin/" + match.group(1) + "/"
            except Exception:
                pass
    page("CHECKING URL")
    print("URL:", url)

    file_info = inspect_direct_file(url)
    keep_existing_image = (config.get("format") == "image" and file_info and file_info["mime"] in IMAGE_MIME_EXTENSIONS)
    if file_info and not keep_existing_image:
        folder = Path(config["folder"]).expanduser()
        ok, result = download_direct_file(file_info, folder)
        if ok:
            return True, result, Path(result).name
        return False, None, result

    if config.get("format") == "image":
        import urllib.parse
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if host == "pin.it" or host.endswith(".pin.it") or host == "pinterest.com" or host.endswith(".pinterest.com"):
            image_url = extract_pinterest_image_url(url)
            if not image_url:
                return False, None, "Could not find a public Pinterest image URL."
            url = image_url
        page("IMAGE DOWNLOAD")
        print("Format: IMAGE - Original")
        print("Save Folder:", config["folder"])
        ok, result = download_direct_image(url, config["folder"])
        if ok:
            return True, result, Path(result).name
        return False, None, result

    media = inspect_url(url)

    if not media:
        return False, None, "Could not inspect URL"

    options, estimated_size = make_download_options(config, media)

    if options is None:
        print("\nSelected format is not available for this URL.")
        return False, None, "Selected format unavailable"

    folder = Path(config["folder"]).expanduser()

    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print("Folder Error:", error)
        return False, None, str(error)

    command = [
        "yt-dlp",
        "--no-playlist",
        "-P", str(folder),
        "-o", "%(title).150B [%(id)s].%(ext)s"
    ] + options + [url]

    page("DOWNLOADING")
    print("Title:", media["title"])
    print("Format:", config["format"].upper())
    print("Quality:", config["quality"])
    print("\n" + paint("● LIVE DOWNLOAD STARTING...", GREEN) + "\n")

    result, saved_path = run_download(command)

    if result == 0:
        return True, saved_path, media["title"]

    return False, saved_path, media["title"]

def extract_pinterest_image_url(url):
    import urllib.parse
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host != "pin.it" and not host.endswith(".pin.it") and host != "pinterest.com" and not host.endswith(".pinterest.com"):
        return None
    try:
        import urllib.request
        import re
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as response:
            final_url = response.geturl()
        match = re.search(r"/pin/(\d+)", final_url)
        if match:
            url = "https://www.pinterest.com/pin/" + match.group(1) + "/"
        result = subprocess.run(["gallery-dl", "--get-urls", url], capture_output=True, text=True, timeout=45)
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            candidate = line.strip()
            parsed = urllib.parse.urlparse(candidate)
            if parsed.scheme == "https" and parsed.hostname == "i.pinimg.com":
                return candidate
    except Exception:
        pass
    return None

DIRECT_FILE_MIME = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
    "application/pdf": ".pdf", "text/plain": ".txt", "text/markdown": ".md",
    "text/csv": ".csv", "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/epub+zip": ".epub", "application/x-mobipocket-ebook": ".mobi",
    "application/zip": ".zip", "application/x-rar-compressed": ".rar",
    "application/vnd.rar": ".rar", "application/x-7z-compressed": ".7z",
    "application/gzip": ".gz", "application/octet-stream": "",
}

def inspect_direct_file(url):
    import urllib.request, urllib.parse
    try:
        parsed = urllib.parse.urlparse(url.strip())
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return None
        req = urllib.request.Request(url.strip(), headers={"User-Agent": "Mozilla/5.0 URL-Download-Manager"}, method="HEAD")
        with urllib.request.urlopen(req, timeout=15) as response:
            final = urllib.parse.urlparse(response.geturl())
            if final.scheme not in ("http", "https"):
                return None
            mime = response.headers.get_content_type().lower()
            if mime in ("text/html", "application/xhtml+xml"):
                return None
            disposition = response.headers.get("Content-Disposition", "")
            import re
            match = re.search(r"filename\\*?=(?:UTF-8\\x27\\x27)?[\"\\x27]?([^\"\\x27;]+)", disposition, re.I)
            filename = urllib.parse.unquote(match.group(1).strip()) if match else Path(urllib.parse.unquote(final.path)).name
            ext = Path(filename).suffix.lower()
            mapped = DIRECT_FILE_MIME.get(mime)
            if mapped is None:
                return None
            if not mapped and not ext:
                return None
            if not ext:
                ext = mapped
            return {"url": response.geturl(), "mime": mime, "name": filename or "download" + ext, "ext": ext, "size": response.headers.get("Content-Length", "Unknown")}
    except Exception:
        return None

def download_direct_file(info, folder):
    import urllib.request
    folder = Path(folder).expanduser()
    part = None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        name = Path(info["name"]).name
        if not name or name in (".", ".."):
            name = "download" + info["ext"]
        dest = folder / name
        stem, suffix = dest.stem, dest.suffix
        n = 1
        while dest.exists():
            dest = folder / (stem + "_" + str(n) + suffix)
            n += 1
        part = dest.with_name(dest.name + ".part")
        req = urllib.request.Request(info["url"], headers={"User-Agent": "Mozilla/5.0 URL-Download-Manager"})
        total = 0
        with urllib.request.urlopen(req, timeout=30) as response, part.open("wb") as out:
            if response.headers.get_content_type().lower() in ("text/html", "application/xhtml+xml"):
                raise ValueError("Server returned a web page, not a file.")
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > 200 * 1024 * 1024:
                    raise ValueError("File exceeds 200 MB limit.")
                out.write(chunk)
        if not total:
            raise ValueError("Downloaded file is empty.")
        part.replace(dest)
        return True, str(dest)
    except Exception as exc:
        if part:
            part.unlink(missing_ok=True)
        return False, str(exc)

def choose_single_settings(media):
    format_options = []
    formats = {}

    if media.get("has_video"):
        formats["1"] = "mp4"
        format_options.append(("1", "MP4 - VIDEO"))

    if media.get("has_audio"):
        formats["2" if media.get("has_video") else "1"] = "mp3"
        key = "2" if media.get("has_video") else "1"
        format_options.append((key, "MP3 - AUDIO"))

    format_options.append(("0", "BACK"))

    choice = choose_menu("SELECT DOWNLOAD FORMAT", format_options)

    if choice == "0" or choice not in formats:
        return None

    kind = formats[choice]

    quality_options = []
    qualities = {}

    if kind == "mp4":
        heights = sorted({
            int(height)
            for height in media.get("heights", [])
            if str(height).isdigit() and int(height) > 0
        })

        if not heights:
            print("No video quality available.")
            pause()
            return None

        for index, height in enumerate(heights, 1):
            key = str(index)
            candidate = f"{height}p"
            _, estimate = make_download_options(
                {"format": kind, "quality": candidate}, media
            )
            size = "Unknown" if estimate is None else human_size(estimate)
            quality_options.append((key, f"{height}p  -  ~ {size}"))
            qualities[key] = candidate

        best_key = str(len(heights) + 1)
        _, estimate = make_download_options(
            {"format": kind, "quality": "best"}, media
        )
        size = "Unknown" if estimate is None else human_size(estimate)
        quality_options.append((best_key, f"BEST AVAILABLE  -  ~ {size}"))
        qualities[best_key] = "best"

        quality_title = "SELECT VIDEO QUALITY"

    else:
        audio_choices = [
            ("1", "128K"),
            ("2", "192K"),
            ("3", "320K"),
            ("4", "BEST QUALITY"),
        ]

        for key, label in audio_choices:
            candidate = "best" if key == "4" else label
            _, estimate = make_download_options(
                {"format": kind, "quality": candidate}, media
            )
            size = "Unknown" if estimate is None else human_size(estimate)
            quality_options.append((key, f"{label}  -  ~ {size}"))
            qualities[key] = candidate

        quality_title = "SELECT AUDIO QUALITY"

    quality_options.append(("0", "BACK"))
    choice = choose_menu(quality_title, quality_options)

    if choice == "0" or choice not in qualities:
        return None

    quality = qualities[choice]
    folder = default_folder(kind)

    location_options = [
        ("1", f"DEFAULT - {folder}"),
        ("2", "CUSTOM FOLDER"),
        ("0", "BACK"),
    ]

    choice = choose_menu("SELECT SAVE LOCATION", location_options)

    if choice == "0":
        return None

    if choice == "2":
        width = min(76, max(40, shutil.get_terminal_size((80, 24)).columns - 2))
        inner = width - 2
        border = "┌" + "─" * inner + "┐"
        print(border)
        print("│" + " ENTER CUSTOM SAVE PATH ".center(inner) + "│")
        print("├" + "─" * inner + "┤")
        custom = input("│  Folder path: ").strip()
        print("└" + "─" * inner + "┘")

        if not custom:
            return None

        folder = Path(custom).expanduser()

    elif choice != "1":
        return None

    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print("Folder Error:", error)
        pause()
        return None

    return {
        "format": kind,
        "quality": quality,
        "folder": str(folder)
    }

def custom_download(url):
    page("MEDIA CHECK")
    print("URL:", url)

    import urllib.parse
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host == "pin.it" or host.endswith(".pin.it") or host == "pinterest.com" or host.endswith(".pinterest.com"):
        image_url = extract_pinterest_image_url(url)
        if image_url:
            url = image_url

    if is_direct_image_url(url):
        kind = "image"
        folder = default_folder(kind)
        page("IMAGE DOWNLOAD SETTINGS")
        print("Format: IMAGE - Original")
        print("[1] Default:", folder)
        print("[2] Custom Folder")
        print("[0] Back")
        choice = input("\nSelect Location: ").strip()
        if choice == "0" or choice not in ("1", "2"):
            return
        if choice == "2":
            custom = input("\nEnter folder path: ").strip()
            if not custom:
                return
            folder = Path(custom).expanduser()
        ok, result = download_direct_image(url, folder)
        completion_screen(ok, Path(result).name if ok else "Image", result if ok else None, folder)
        return

    file_info = inspect_direct_file(url)
    if file_info:
        kind = "image" if file_info["mime"].startswith("image/") else "file"
        folder = default_folder(kind)
        page("FILE DOWNLOAD SETTINGS")
        print("Detected Type:", file_info["mime"])
        print("File Name:", file_info["name"])
        print("File Size:", file_info["size"])
        print("Format: ORIGINAL")
        print("[1] Default:", folder)
        print("[2] Custom Folder")
        print("[0] Back")
        choice = input("\nSelect Location: ").strip()
        if choice not in ("1", "2"):
            return
        if choice == "2":
            custom = input("\nEnter folder path: ").strip()
            if not custom:
                return
            folder = Path(custom).expanduser()
        ok, result = download_direct_file(file_info, folder)
        completion_screen(ok, Path(result).name if ok else file_info["name"], result if ok else None, folder)
        return

    media = inspect_url(url)

    if not media:
        pause()
        return

    config = choose_single_settings(media)

    if not config:
        return

    options, estimated_size = make_download_options(
        config, media
    )

    if options is None:
        page("FORMAT UNAVAILABLE")
        print("Selected format or quality is unavailable.")
        pause()
        return

    folder = Path(config["folder"]).expanduser()

    command = [
        "yt-dlp",
        "--no-playlist",
        "-P", str(folder),
        "-o", "%(title).150B [%(id)s].%(ext)s"
    ] + options + [url]

    page("DOWNLOADING")
    print("Title:", media["title"])
    print("Format:", config["format"].upper())
    print("Quality:", config["quality"])
    print("\n" + paint("● LIVE DOWNLOAD STARTING...", GREEN) + "\n")

    result, saved_path = run_download(command)

    completion_screen(
        result == 0,
        media["title"],
        saved_path,
        folder
    )

def single_download(url=None):
    if url:
        custom_download(url)
        return

    while True:
        page("SINGLE DOWNLOAD")

        print("Every download can use different settings.")
        print("\nPaste one URL.")
        print("[Enter] Back")
        print("[0] Back to URL INFO")

        value = input("\nURL: ").strip()

        if not value:
            return

        if value == "0":
            return EXIT

        if not value.startswith(("http://", "https://")):
            value = "https://" + value

        custom_download(value)

def main():
    clear_screen()

    if not shutil.which("yt-dlp"):
        print("Install first: apt install python-yt-dlp -y")
        return

    if not shutil.which("ffmpeg"):
        print("FFmpeg not found.")
        print("MP4 merging and MP3 conversion may not work.")
        print("Install: apt install ffmpeg -y")
        pause()

    if len(sys.argv) >= 2 and sys.argv[1] in ("-d", "--download"):
        if len(sys.argv) >= 3:
            url = sys.argv[2].strip()

            if not url.startswith(("http://", "https://")):
                url = "https://" + url

            single_download(url)
            return

    main_menu(load_config())


if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        print("\n\n================================")
        print("       PROGRAM STOPPED")
        print("================================")
        print("If a queue was active, its progress was saved.")
        print("Program ended.\n")

    except EOFError:
        print("\nProgram ended.")
