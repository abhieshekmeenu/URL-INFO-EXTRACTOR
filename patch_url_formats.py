from pathlib import Path
import shutil
import py_compile
import time

file = Path(__file__).with_name("downloader.py")
source = file.read_text(encoding="utf-8")

backup = file.with_name(
    f"downloader.py.backup-formats-{time.strftime('%Y%m%d-%H%M%S')}"
)
shutil.copy2(file, backup)

def replace_between(text, start, end, replacement):
    a = text.find(start)
    if a < 0:
        raise ValueError("Missing start: " + start)
    b = text.find(end, a + len(start))
    if b < 0:
        raise ValueError("Missing end: " + end)
    return text[:a] + replacement + "\n\n\n" + text[b:]

try:
    source = replace_between(
        source,
        "def load_config():",
        "def save_config(config):",
        '''def load_config():
    if not CONFIG_FILE.exists():
        return {}

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            return {}

        # पुरानी single-format settings को सुरक्षित रखें।
        if data.get("format") in ("mp3", "mp4"):
            kind = data["format"]
            if data.get("quality") and data.get("folder"):
                return {kind: data}
            return {}

        configs = {}
        for kind in ("mp3", "mp4"):
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
        return {}'''
    )

    source = replace_between(
        source,
        "def setup_settings():",
        "def human_size(",
        '''def setup_settings(kind=None):
    configs = load_config()

    if kind is None:
        while True:
            page("DEFAULT DOWNLOAD SETTINGS")
            print("[1] MP3 - Audio")
            print("[2] MP4 - Video")
            print("[0] Back to URL INFO")

            choice = input("\\\\nSelect Format: ").strip()

            if choice in ("", "0"):
                return BACK
            if choice == "1":
                kind = "mp3"
                break
            if choice == "2":
                kind = "mp4"
                break

    if kind not in ("mp3", "mp4"):
        return BACK

    while True:
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

        print("\\\\n[Enter] Back")
        print("[0] Back to URL INFO")
        choice = input("\\\\nSelect Quality: ").strip()

        if choice == "":
            return setup_settings(kind)
        if choice == "0":
            return BACK
        if choice in qualities:
            quality = qualities[choice]
            break

    folder = default_folder(kind)

    while True:
        page("DEFAULT SAVE LOCATION")
        print("Format:", kind.upper())
        print("[1] Default:", folder)
        print("[2] Custom Folder")
        print("\\\\n[Enter] Back")
        print("[0] Back to URL INFO")

        choice = input("\\\\nSelect Location: ").strip()

        if choice == "":
            return setup_settings(kind)
        if choice == "0":
            return BACK

        if choice == "1":
            selected = folder
        elif choice == "2":
            custom = input("\\\\nEnter folder path: ").strip()
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

    return configs'''
    )

    source = replace_between(
        source,
        "def main_menu(config=None):",
        "def main():",
        '''def choose_download_format():
    while True:
        page("SELECT DOWNLOAD FORMAT")
        print("[1] MP3 - Audio")
        print("[2] MP4 - Video")
        print("\\\\n[Enter] Back")
        print("[0] Back to URL INFO")

        choice = input("\\\\nSelect Format: ").strip()

        if choice == "":
            return BACK
        if choice == "0":
            return EXIT
        if choice == "1":
            return "mp3"
        if choice == "2":
            return "mp4"


def main_menu(config=None):
    while True:
        configs = load_config()
        pending = load_queue()

        page("URL DOWNLOAD MANAGER")
        print("MAIN MENU\\\\n")
        print("[1] SINGLE DOWNLOAD")
        print("[2] DEFAULT DOWNLOAD")
        print("[3] ALL DOWNLOAD")
        print("[4] DEFAULT DOWNLOAD SETTINGS")

        if pending:
            print("[5] RESUME QUEUE")

        print("\\\\n[0] BACK TO URL INFO")
        choice = input("\\\\nSelect: ").strip()

        if choice == "0":
            return

        elif choice == "1":
            result = single_download()
            if result == EXIT:
                return

        elif choice in ("2", "3"):
            kind = choose_download_format()

            if kind == BACK:
                continue
            if kind == EXIT:
                return

            selected_config = configs.get(kind)

            if not selected_config:
                page("DEFAULT SETTINGS REQUIRED")
                print("Configure", kind.upper(), "settings first.")
                pause()

                updated = setup_settings(kind)
                if updated == BACK:
                    continue

                configs = load_config()
                selected_config = configs.get(kind)

            if not selected_config:
                continue

            if choice == "2":
                result = default_download(selected_config)
                if result == EXIT:
                    return
            else:
                result = multiple_download(selected_config)
                if result == "0":
                    return

        elif choice == "4":
            updated = setup_settings()
            if updated == BACK:
                continue

        elif choice == "5" and pending:
            result = resume_queue()
            if result == "0":
                return

        else:
            page("INVALID OPTION")
            pause()'''
    )

    file.write_text(source, encoding="utf-8")
    py_compile.compile(str(file), doraise=True)

except Exception as error:
    shutil.copy2(backup, file)
    print("PATCH FAILED - ORIGINAL RESTORED")
    print("Reason:", error)
    raise SystemExit(1)

print("PATCH SUCCESSFUL")
print("Backup:", backup)
print("Python syntax: OK")
