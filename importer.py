"""
Automatic news import from RSS / Atom feeds (see feeds.py).

Only the headline, a short summary, the picture link and the link to the
original article are stored. "Read More" always opens the original site.
Uses only the Python standard library plus `requests`.
"""
import html
import re
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

import feeds

USER_AGENT = "Mozilla/5.0 (compatible; DailyNewsReader/1.0)"
MAX_BYTES = 3 * 1024 * 1024  # never read more than 3 MB from one feed

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
IMG_RE = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.I)
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")

_lock = threading.Lock()

# Shown on the admin page
IMPORT_STATUS = {"running": False, "last_run": None, "added": 0, "errors": []}


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean(text, limit):
    """Strip HTML tags, decode entities, squeeze spaces, cut to `limit`."""
    text = html.unescape(TAG_RE.sub(" ", text or ""))
    text = SPACE_RE.sub(" ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def http_url(url):
    url = (url or "").strip()
    return url if url.lower().startswith(("http://", "https://")) else ""


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def _local(tag):
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _text(el, *names):
    """Text of the first direct child whose (namespace-free) name matches."""
    for child in el:
        if _local(child.tag) in names and (child.text or "").strip():
            return child.text
    return ""


def _link(el):
    for child in el:
        if _local(child.tag) != "link":
            continue
        href = child.get("href")  # Atom
        if href and child.get("rel", "alternate") == "alternate":
            return http_url(href)
        if child.text and child.text.strip():  # RSS
            return http_url(child.text)
    return ""


def _image(el, body):
    for node in el.iter():
        name = _local(node.tag)
        if name == "thumbnail" and http_url(node.get("url")):
            return http_url(node.get("url"))
        if name in ("content", "enclosure"):
            url = http_url(node.get("url"))
            kind = (node.get("type") or "") + (node.get("medium") or "")
            if url and ("image" in kind or url.lower().split("?")[0].endswith(IMG_EXT)):
                return url
    match = IMG_RE.search(body or "")
    return http_url(html.unescape(match.group(1))) if match else ""


def _date(el):
    raw = _text(el, "pubdate", "published", "updated", "date")
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)  # RSS: "Sat, 18 Oct 2026 10:00:00 +0530"
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))  # Atom
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_feed(data):
    """bytes of an RSS or Atom feed -> list of dicts (may be empty)."""
    root = ET.fromstring(data.lstrip())
    entries = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        body = _text(el, "encoded") or _text(el, "description", "summary", "content")
        summary = _text(el, "description", "summary") or body
        entries.append(
            {
                "title": _text(el, "title"),
                "link": _link(el),
                "summary": summary,
                "image": _image(el, body or summary),
                "published": _date(el),
            }
        )
    return entries


def fetch_feed(url):
    """Download one feed and return its parsed entries."""
    with requests.get(
        url, headers={"User-Agent": USER_AGENT}, timeout=10, stream=True
    ) as response:
        response.raise_for_status()
        data = b""
        for chunk in response.iter_content(65536):
            data += chunk
            if len(data) > MAX_BYTES:
                break
    return parse_feed(data)


# --------------------------------------------------------------------------
# Import job
# --------------------------------------------------------------------------
def run_import(db_path, keep_days=14, per_feed=15):
    """Fetch every feed once. Returns number of new articles, or None if
    another import is already running."""
    if not _lock.acquire(blocking=False):
        return None
    IMPORT_STATUS["running"] = True
    added, errors = 0, []
    try:
        now = datetime.now(timezone.utc)
        con = sqlite3.connect(db_path, timeout=30)
        try:
            for category, source, url in feeds.FEEDS:
                label = f"{source} ({category})"
                try:
                    entries = fetch_feed(url)
                except ET.ParseError:
                    errors.append(f"{label}: feed is not valid RSS/XML")
                    continue
                except Exception as exc:  # network, HTTP error, timeout...
                    errors.append(f"{label}: {exc.__class__.__name__}")
                    continue
                if not entries:
                    errors.append(f"{label}: no articles found in feed")
                    continue

                for entry in entries[:per_feed]:
                    title = clean(entry["title"], 200)
                    link = entry["link"]
                    if not title or not link:
                        continue
                    published = min(entry["published"] or now, now)  # never in the future
                    if published < now - timedelta(days=keep_days):
                        continue  # too old: would be deleted again right away
                    cur = con.execute(
                        "INSERT OR IGNORE INTO articles (title, description, "
                        "content, category, image, source, source_url, "
                        "created_at, updated_at) "
                        "VALUES (?, ?, '', ?, ?, ?, ?, ?, ?)",
                        (
                            title,
                            clean(entry["summary"], 500) or title,
                            category,
                            entry["image"],
                            source,
                            link,
                            iso(published),
                            iso(now),
                        ),
                    )
                    added += cur.rowcount
                con.commit()

            # Keep the database small: drop old imported news (never your own)
            cutoff = iso(now - timedelta(days=keep_days))
            con.execute(
                "DELETE FROM articles WHERE source_url != '' AND created_at < ?",
                (cutoff,),
            )
            con.commit()
        finally:
            con.close()
        return added
    finally:
        IMPORT_STATUS.update(
            running=False,
            last_run=iso(datetime.now(timezone.utc)),
            added=added,
            errors=errors,
        )
        _lock.release()


def start_background(db_path, interval_minutes, keep_days):
    """Import now (after a few seconds), then again every interval."""

    def loop():
        time.sleep(5)
        while True:
            try:
                run_import(db_path, keep_days)
            except Exception as exc:  # never let the thread die
                IMPORT_STATUS["errors"] = [f"Import crashed: {exc!r}"]
            time.sleep(interval_minutes * 60)

    threading.Thread(target=loop, daemon=True, name="feed-import").start()
