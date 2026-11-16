#!/usr/bin/env python3
import sys
import os
import json
import shutil
import subprocess
from pathlib import Path
from datetime import datetime

C = "\033[96m"
G = "\033[92m"
Y = "\033[93m"
R = "\033[91m"
B = "\033[94m"
X = "\033[0m"

DEFAULT_DOWNLOAD = Path.home() / "Desktop" / "URL" / "Downloads"

def show(label, value):
    if value is None or value == "":
        value = "Not Available"
    print(f"{B}{label:<22}{X}: {G}{value}{X}")

def format_date(value):
    if not value:
        return "Not Available"
    try:
        return datetime.strptime(str(value), "%Y%m%d").strftime("%d/%m/%Y")
    except ValueError:
        return value

def format_time(value):
    if not value:
        return "Not Available"
    try:
        dt = datetime.fromtimestamp(value).astimezone()
        return dt.strftime("%d/%m/%Y  %H:%M:%S") + " (Device Local Time)"
    except (ValueError, TypeError, OSError):
        return "Not Available"

def format_duration(value):
    if value is None:
        return "Not Available"
    seconds = int(value)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02}:{m:02}:{s:02}"

def detect_media_type(data):
    formats = data.get("formats") or []
    has_video = data.get("vcodec", "none") != "none"
    has_audio = data.get("acodec", "none") != "none"

    for item in formats:
        if item.get("vcodec") not in (None, "none"):
            has_video = True
        if item.get("acodec") not in (None, "none"):
            has_audio = True

    if has_video and has_audio:
        return "VIDEO + AUDIO"
    if has_video:
        return "VIDEO ONLY"
    if has_audio:
        return "AUDIO ONLY"
    return "UNKNOWN / NOT AVAILABLE"


def download_video(url):
    print(f"\n{Y}Do you want to download this video?{X}")
    answer = input("[Y] Download / [Enter] Skip: ").strip().lower()

    if answer not in ("y", "yes"):
        print("Download skipped.")
        return

    path_text = input(
        "\nDownload folder (Enter = URL-INFO/Downloads): "
    ).strip()

    folder = Path(path_text).expanduser() if path_text else DEFAULT_DOWNLOAD

    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"{R}Folder Error: {e}{X}")
        return

    print(f"\nSave Location: {folder}")
    confirm = input("Confirm download? [Y/Enter to cancel]: ").strip().lower()

    if confirm not in ("y", "yes"):
        print("Download cancelled.")
        return

    print(f"\n{G}Starting download...{X}")

    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--no-playlist",
                "--no-warnings",
                "-P", str(folder),
                "-o", "%(title).150B [%(id)s].%(ext)s",
                url
            ],
            timeout=3600
        )

        if result.returncode == 0:
            print(f"\n{G}Download completed!{X}")
            print("Saved in:", folder)
        else:
            print(f"\n{R}Download failed. Exit code:", result.returncode, X)

    except subprocess.TimeoutExpired:
        print(f"{R}Download timed out.{X}")
    except Exception as e:
        print(f"{R}Download Error: {e}{X}")


def get_instagram_profile(username):
    """Read only metadata publicly returned by an Instagram profile page."""
    import re
    import urllib.request
    from html.parser import HTMLParser

    result = {
        "name": None, "username": username, "user_id": None,
        "url": None, "bio": None, "followers": None,
        "following": None, "posts": None
    }

    if not username or not re.fullmatch(r"[A-Za-z0-9._]+", str(username)):
        return result

    profile_url = "https://www.instagram.com/" + username.strip("/") + "/"
    result["url"] = profile_url

    class MetaParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.meta = {}

        def handle_starttag(self, tag, attrs):
            if tag.lower() == "meta":
                a = dict(attrs)
                key = (a.get("property") or a.get("name") or "").lower()
                if key:
                    self.meta[key] = a.get("content", "")

    try:
        req = urllib.request.Request(
            profile_url,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read(1500000).decode("utf-8", errors="replace")

        parser = MetaParser()
        parser.feed(html)

        title = parser.meta.get("og:title", "")
        og_description = parser.meta.get("og:description", "")
        full_description = parser.meta.get("description", "")

        name_match = re.search(r"^(.*?)\s*\(@", title)
        if name_match:
            result["name"] = name_match.group(1).strip()

        # Counts are commonly abbreviated, e.g. 25K, 1.2M.
        patterns = {
            "followers": r"([\d,.]+\s*[KMB]?)\s+Followers?",
            "following": r"([\d,.]+\s*[KMB]?)\s+Following",
            "posts": r"([\d,.]+\s*[KMB]?)\s+Posts?"
        }

        for field, pattern in patterns.items():
            match = re.search(pattern, og_description, re.I)
            if not match:
                match = re.search(pattern, full_description, re.I)
            if match:
                result[field] = match.group(1).strip()

        # Remove Instagram's count/name prefix to keep the actual bio.
        bio = full_description
        bio = re.sub(
            r"^.*?\bPosts?\s*[-–]\s*",
            "",
            bio,
            count=1,
            flags=re.I
        )
        bio = re.sub(
            r"^.*?\bon Instagram\s*:\s*",
            "",
            bio,
            count=1,
            flags=re.I
        )
        bio = bio.strip().strip('"').strip()
        if bio:
            result["bio"] = bio[:500]

        id_match = re.search(
            r'"profile_id"\s*:\s*"(\d+)"', html
        )
        if id_match:
            result["user_id"] = id_match.group(1)

    except Exception:
        pass

    return result

def video_info(url, allow_download=True):
    from urllib.parse import urlsplit, urlunsplit

    clean_url = url
    parsed = urlsplit(url)

    if (parsed.hostname or "").lower() in (
        "instagram.com", "www.instagram.com"
    ):
        parts = [x for x in parsed.path.split("/") if x]
        reserved = {"reel", "reels", "p", "tv", "stories", "explore"}

        if len(parts) == 1 and parts[0].lower() not in reserved:
            clean_url = urlunsplit((
                parsed.scheme,
                parsed.netloc,
                "/" + parts[0] + "/",
                "",
                ""
            ))

    if not shutil.which("yt-dlp"):
        print(f"{R}yt-dlp is not installed.{X}")
        return False

    print(f"\n{Y}Checking video information...{X}")

    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--dump-single-json",
                "--skip-download",
                "--no-warnings",
                "--no-playlist",
                clean_url
            ],
            capture_output=True,
            text=True,
            timeout=90
        )

        if result.returncode != 0:
            print(f"{R}{result.stderr.strip()}{X}")
            return False

        if not result.stdout.strip():
            print(f"{R}No information returned by the platform.{X}")
            return False

        d = json.loads(result.stdout)

        if not isinstance(d, dict):
            print(f"{R}Invalid information returned by the platform.{X}")
            return False

        platform = str(
            d.get("extractor_key") or d.get("extractor") or "Unknown"
        )
        platform_lower = platform.lower()

        print(f"\n{Y}========== URL INFORMATION =========={X}")

        if "youtube" in platform_lower:
            print(f"{C}Platform Type: YouTube{X}")
            show("Platform", platform)
            show("Media Type", detect_media_type(d))
            show("Video Title", d.get("title"))
            show("Channel Name", d.get("channel") or d.get("uploader"))
            show("Channel ID", d.get("channel_id"))
            show("Channel URL", d.get("channel_url"))
            show("Username / ID", d.get("uploader_id"))
            show("Subscribers", d.get("channel_follower_count"))
            show("Views", d.get("view_count"))
            show("Likes", d.get("like_count"))
            show("Comments", d.get("comment_count"))
            show("Upload Date", format_date(d.get("upload_date")))
            published = d.get("release_timestamp") or d.get("timestamp")
            show("Published Date/Time", format_time(published))
            show("Duration", format_duration(d.get("duration")))
            show("Video ID", d.get("id"))
            show("Video URL", d.get("webpage_url"))
            show("Description", (d.get("description") or "")[:500])

        elif "instagram" in platform_lower:
            print(f"{C}Platform Type: Instagram{X}")
            show("Platform", platform)
            show("Media Type", detect_media_type(d))
            show("Post / Reel Title", d.get("title"))
            username = d.get("channel") or d.get("uploader_id")
            print(f"{Y}Checking public Instagram profile...{X}")
            profile = get_instagram_profile(username)

            show("Profile Name", profile.get("name") or d.get("uploader"))
            show("Username", username)
            show("User ID", profile.get("user_id") or d.get("uploader_id"))
            show("Profile URL", profile.get("url") or d.get("channel_url") or d.get("uploader_url"))
            show("Bio", profile.get("bio"))

            print()
            print(f"{Y}--- REEL SOURCE DATA ---{X}")
            show("Followers (Reel Source)", d.get("follower_count") or d.get("channel_follower_count"))
            show("Following (Reel Source)", d.get("following_count"))
            show("Total Posts (Reel Source)", d.get("media_count") or d.get("playlist_count"))

            print()
            print(f"{Y}--- USERNAME PROFILE DATA ---{X}")
            show("Followers (Profile)", profile.get("followers"))
            show("Following (Profile)", profile.get("following"))
            show("Total Posts (Profile)", profile.get("posts"))

            show("Views", d.get("view_count"))
            show("Likes", d.get("like_count"))
            show("Comments", d.get("comment_count"))
            show("Upload Date", format_date(d.get("upload_date")))
            published = d.get("release_timestamp") or d.get("timestamp")
            show("Published Date/Time", format_time(published))
            show("Duration", format_duration(d.get("duration")))
            show("Reel / Post ID", d.get("id"))
            show("Reel / Post URL", d.get("webpage_url"))
            show("Description", (d.get("description") or "")[:500])

        elif "facebook" in platform_lower:
            print(f"{C}Platform Type: Facebook{X}")
            show("Platform", platform)
            show("Media Type", detect_media_type(d))
            show("Post / Video Title", d.get("title"))
            show("Profile / Page Name", d.get("channel") or d.get("uploader"))
            show("Username / ID", d.get("uploader_id") or d.get("channel_id"))
            show("Profile / Page URL", d.get("channel_url") or d.get("uploader_url"))
            show("Followers", d.get("follower_count") or d.get("channel_follower_count"))
            show("Following", d.get("following_count"))
            show("Views", d.get("view_count"))
            show("Likes", d.get("like_count"))
            show("Comments", d.get("comment_count"))
            show("Upload Date", format_date(d.get("upload_date")))
            published = d.get("release_timestamp") or d.get("timestamp")
            show("Published Date/Time", format_time(published))
            show("Duration", format_duration(d.get("duration")))
            show("Post / Video ID", d.get("id"))
            show("Post / Video URL", d.get("webpage_url"))
            show("Description", (d.get("description") or "")[:500])

        elif "moj" in platform_lower:
            print(f"{C}Platform Type: Moj{X}")
            show("Platform", platform)
            show("Media Type", detect_media_type(d))
            show("Video Title", d.get("title"))
            show("Creator Name", d.get("uploader") or d.get("channel"))
            show("Username / ID", d.get("uploader_id") or d.get("channel_id"))
            show("Profile URL", d.get("channel_url") or d.get("uploader_url"))
            show("Followers", d.get("follower_count") or d.get("channel_follower_count"))
            show("Views", d.get("view_count"))
            show("Likes", d.get("like_count"))
            show("Comments", d.get("comment_count"))
            show("Upload Date", format_date(d.get("upload_date")))
            published = d.get("release_timestamp") or d.get("timestamp")
            show("Published Date/Time", format_time(published))
            show("Duration", format_duration(d.get("duration")))
            show("Video ID", d.get("id"))
            show("Video URL", d.get("webpage_url"))
            show("Description", (d.get("description") or "")[:500])

        else:
            print(f"{C}Platform Type: Other / Generic{X}")
            show("Platform", platform)
            show("Media Type", detect_media_type(d))
            show("Title", d.get("title"))
            show("Creator / Uploader", d.get("uploader") or d.get("channel"))
            show("Username / ID", d.get("uploader_id") or d.get("channel_id"))
            show("Profile / Channel URL", d.get("channel_url") or d.get("uploader_url"))
            show("Views", d.get("view_count"))
            show("Likes", d.get("like_count"))
            show("Comments", d.get("comment_count"))
            show("Upload Date", format_date(d.get("upload_date")))
            published = d.get("release_timestamp") or d.get("timestamp")
            show("Published Date/Time", format_time(published))
            show("Duration", format_duration(d.get("duration")))
            show("Media ID", d.get("id"))
            show("Media URL", d.get("webpage_url"))
            show("Description", (d.get("description") or "")[:500])

        if allow_download and d.get("duration") is not None:
            download_video(url)

        return True

    except Exception as e:
        print(f"{R}Error: {e}{X}")
        return False

def website_info(url):
    import urllib.request
    import re
    from urllib.parse import urlparse, urlsplit
    from html.parser import HTMLParser

    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.title = ""
            self.meta = {}
            self.in_title = False

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "title":
                self.in_title = True
            if tag == "meta":
                key = (a.get("property") or a.get("name") or "").lower()
                if key:
                    self.meta[key] = a.get("content", "")

        def handle_endtag(self, tag):
            if tag == "title":
                self.in_title = False

        def handle_data(self, data):
            if self.in_title:
                self.title += data.strip()

    parsed_input = urlsplit(url)
    profile_parts = [x for x in parsed_input.path.split("/") if x]
    is_instagram_profile = (
        (parsed_input.hostname or "").lower() in
        ("instagram.com", "www.instagram.com")
        and len(profile_parts) == 1
        and profile_parts[0].lower() not in
        {"reel", "reels", "p", "tv", "stories", "explore"}
    )

    if is_instagram_profile:
        print(f"\n{Y}========== INSTAGRAM PROFILE =========={X}")
    else:
        print(f"\n{Y}========== WEBSITE INFORMATION =========={X}")

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 URL-INFO/3.0"}
        )

        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read(2000000).decode("utf-8", errors="replace")
            p = Parser()
            p.feed(html)

            if is_instagram_profile:
                page_title = p.meta.get("og:title") or p.title
                desc_text = (
                    p.meta.get("og:description")
                    or p.meta.get("description")
                    or ""
                )

                username = profile_parts[0]
                clean_profile_url = (
                    f"https://www.instagram.com/{username}/"
                )

                show("Platform", "Instagram")
                show("Profile URL", clean_profile_url)
                show(
                    "Profile Name",
                    page_title.split(" (@", 1)[0].strip()
                    if page_title else None
                )
                show("Username", username)

                stats = re.search(
                    r"([\d,.]+\s*[KMB]?)\s+Followers?,\s*"
                    r"([\d,.]+\s*[KMB]?)\s+Following,\s*"
                    r"([\d,.]+\s*[KMB]?)\s+Posts?",
                    desc_text,
                    re.IGNORECASE
                )

                if stats:
                    show("Followers (Profile)", stats.group(1))
                    show("Following (Profile)", stats.group(2))
                    show("Total Posts (Profile)", stats.group(3))
                else:
                    show("Followers (Profile)", None)
                    show("Following (Profile)", None)
                    show("Total Posts (Profile)", None)

                show("Data Source", "Public Instagram Page")
                return

            show("Website / Domain", urlparse(r.geturl()).netloc)
            show("Final URL", r.geturl())
            show("HTTP Status", r.status)
            show("Page Title", p.meta.get("og:title") or p.title)
            show("Site Name", p.meta.get("og:site_name"))
            show("Description", p.meta.get("og:description") or p.meta.get("description"))
            show("Author", p.meta.get("author"))
            show("Published", p.meta.get("article:published_time"))
            show("Content Type", r.headers.get("Content-Type"))
            show("Server", r.headers.get("Server"))

    except Exception as e:
        print(f"{R}Website information unavailable: {e}{X}")

def inspect(url, choice="1"):
    if not url.startswith(("https://", "http://")):
        url = "https://" + url

    print(f"\n{Y}Checking URL: {url}{X}")

    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    parts = [x for x in parsed.path.split("/") if x]

    if (
        (parsed.hostname or "").lower() in
        ("instagram.com", "www.instagram.com")
        and len(parts) == 1
        and parts[0].lower() not in
        {"reel", "reels", "p", "tv", "stories", "explore"}
    ):
        website_info(url)
        return

    if choice in ("1", "2", "3"):
        if video_info(url, allow_download=False):
            return

    website_info(url)


def main():
    print()
    print(f"{Y}===================================={X}")
    print(f"{Y}              URL INFO{X}")
    print(f"{Y}===================================={X}")
    print()
    try:
        url = input(f"{C}Paste URL: {X}").strip()

        if not url:
            print(f"{R}No URL entered.{X}")
            return

        if url == "0":
            return

        inspect(url)

        print()
        input(f"{Y}Press Enter to exit...{X}")

    except (KeyboardInterrupt, EOFError):
        print()
        print(f"{R}\\n====================================")
        print("          PROGRAM STOPPED")
        print("====================================")
        print("You stopped the operation using Ctrl+C.")
        print(f"Program ended.{X}")

if __name__ == "__main__":
    try:
        if len(sys.argv) > 1:
            inspect(sys.argv[1])
        else:
            main()
    except KeyboardInterrupt:
        print(f"\\n\\n{R}====================================")
        print("          PROGRAM STOPPED")
        print("====================================")
        print("You stopped the operation using Ctrl+C.")
        print(f"Program ended.{X}\\n")
    except EOFError:
        print(f"\\n{Y}Input closed. Program ended.{X}")
