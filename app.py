import hmac
import os
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

import requests
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from importer import IMPORT_STATUS, run_import, start_background

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

API_KEY = os.getenv("NEWS_API_KEY")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_hex(32)

DEBUG = os.getenv("FLASK_DEBUG") == "1"


def _int_env(name, default, minimum):
    try:
        return max(minimum, int(os.getenv(name, default)))
    except ValueError:
        return default


# Automatic news import from RSS feeds (see feeds.py). Set AUTO_IMPORT=0 to stop it.
AUTO_IMPORT = os.getenv("AUTO_IMPORT", "1") != "0"
FEED_REFRESH_MINUTES = _int_env("FEED_REFRESH_MINUTES", 60, 10)
KEEP_DAYS = _int_env("KEEP_DAYS", 14, 1)

DB_PATH = os.path.join(BASE_DIR, "news.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
UPLOAD_URL_PREFIX = "/static/uploads/"
os.makedirs(UPLOAD_DIR, exist_ok=True)

NEWS_URL = "https://newsapi.org/v2/top-headlines"
SEARCH_URL = "https://newsapi.org/v2/everything"

CATEGORIES = [
    "general",
    "technology",
    "business",
    "sports",
    "science",
    "entertainment",
    "health",
]
ALLOWED_EXT = {"png", "jpg", "jpeg", "webp", "gif"}

app = Flask(__name__)

# Set BEHIND_PROXY=1 on hosts like Render (the site sits behind their proxy).
# Then we read the visitor's real IP / https from the proxy headers, and the
# session cookie is only sent over https. Leave it off on your own laptop.
BEHIND_PROXY = os.getenv("BEHIND_PROXY") == "1"
if BEHIND_PROXY:
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

app.config.update(
    SECRET_KEY=SECRET_KEY,
    MAX_CONTENT_LENGTH=6 * 1024 * 1024,  # 6 MB per request
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=BEHIND_PROXY,
)


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
def init_db():
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS articles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                description TEXT NOT NULL,
                content     TEXT NOT NULL DEFAULT '',
                category    TEXT NOT NULL DEFAULT 'general',
                image       TEXT NOT NULL DEFAULT '',
                source      TEXT NOT NULL DEFAULT '',
                source_url  TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_articles_cat "
            "ON articles(category, created_at)"
        )
        # Upgrade an older news.db that was created before auto-import existed
        cols = {r[1] for r in con.execute("PRAGMA table_info(articles)")}
        if "source" not in cols:
            con.execute("ALTER TABLE articles ADD COLUMN source TEXT NOT NULL DEFAULT ''")
        if "source_url" not in cols:
            con.execute(
                "ALTER TABLE articles ADD COLUMN source_url TEXT NOT NULL DEFAULT ''"
            )
        # The same original article is never saved twice
        con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_source_url "
            "ON articles(source_url) WHERE source_url != ''"
        )


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# Security helpers: CSRF + admin login
# --------------------------------------------------------------------------
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token
app.jinja_env.globals["CATEGORIES"] = CATEGORIES
app.jinja_env.globals["import_status"] = lambda: IMPORT_STATUS
app.jinja_env.globals["AUTO_IMPORT"] = AUTO_IMPORT
app.jinja_env.globals["FEED_REFRESH_MINUTES"] = FEED_REFRESH_MINUTES


@app.before_request
def check_csrf():
    if request.method == "POST":
        sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token", "")
        if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
            abort(400, "Invalid or missing CSRF token. Refresh the page and try again.")


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapper


FAILED_LOGINS = {}  # ip -> (count, first_fail_time)
MAX_FAILS = 5
LOCK_SECONDS = 300


def is_locked(ip):
    rec = FAILED_LOGINS.get(ip)
    if not rec:
        return False
    count, first = rec
    if time.time() - first > LOCK_SECONDS:
        FAILED_LOGINS.pop(ip, None)
        return False
    return count >= MAX_FAILS


def register_fail(ip):
    count, first = FAILED_LOGINS.get(ip, (0, time.time()))
    FAILED_LOGINS[ip] = (count + 1, first)


@app.route("/login", methods=["GET", "POST"])
def login():
    if not ADMIN_PASSWORD:
        return (
            "ADMIN_PASSWORD is not set. Add it to your .env file and restart the app.",
            503,
        )

    if request.method == "POST":
        ip = request.remote_addr or "unknown"
        if is_locked(ip):
            flash("Too many failed attempts. Try again in a few minutes.", "error")
            return render_template("login.html"), 429

        password = request.form.get("password", "")
        if hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode()):
            FAILED_LOGINS.pop(ip, None)
            session.clear()
            session["is_admin"] = True
            csrf_token()
            return redirect(url_for("admin"))

        register_fail(ip)
        flash("Wrong password.", "error")

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("home"))


# --------------------------------------------------------------------------
# Image upload helpers
# --------------------------------------------------------------------------
def looks_like_image(head, ext):
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ext == "png"
    if head.startswith(b"\xff\xd8\xff"):
        return ext in ("jpg", "jpeg")
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ext == "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ext == "webp"
    return False


def save_image(file_storage):
    """Validate and store an uploaded image. Returns its public URL path."""
    name = file_storage.filename or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED_EXT:
        raise ValueError("Image must be PNG, JPG, WEBP or GIF.")

    head = file_storage.stream.read(16)
    file_storage.stream.seek(0)
    if not looks_like_image(head, ext):
        raise ValueError("That file is not a valid image.")

    filename = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(UPLOAD_DIR, filename))
    return UPLOAD_URL_PREFIX + filename


def delete_local_image(image_path):
    if image_path and image_path.startswith(UPLOAD_URL_PREFIX):
        target = os.path.join(UPLOAD_DIR, os.path.basename(image_path))
        if os.path.isfile(target):
            os.remove(target)


def read_article_form(existing=None):
    """Parse + validate the add/edit form. Returns (data, errors)."""
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    content = request.form.get("content", "").strip()
    category = request.form.get("category", "general")
    image_url = request.form.get("image_url", "").strip()
    errors = []

    if not title:
        errors.append("Title is required.")
    elif len(title) > 200:
        errors.append("Title can be at most 200 characters.")
    if not description:
        errors.append("Short description is required.")
    elif len(description) > 500:
        errors.append("Description can be at most 500 characters.")
    if len(content) > 20000:
        errors.append("Full story can be at most 20,000 characters.")
    if category not in CATEGORIES:
        errors.append("Pick a valid category.")
    if image_url and not image_url.lower().startswith(("http://", "https://")):
        errors.append("Image URL must start with http:// or https://")

    image = existing["image"] if existing else ""
    new_file = request.files.get("image_file")
    new_image_saved = False

    if not errors:
        if new_file and new_file.filename:
            try:
                image = save_image(new_file)
                new_image_saved = True
            except ValueError as exc:
                errors.append(str(exc))
        elif image_url:
            image = image_url
        elif request.form.get("remove_image"):
            image = ""

    data = {
        "title": title,
        "description": description,
        "content": content,
        "category": category,
        "image": image,
        "new_image_saved": new_image_saved,
    }
    return data, errors


# --------------------------------------------------------------------------
# Public pages
# --------------------------------------------------------------------------
@app.route("/")
def home():
    return render_template("index.html", is_admin=session.get("is_admin", False))


@app.route("/article/<int:article_id>")
def article(article_id):
    row = get_db().execute(
        "SELECT * FROM articles WHERE id = ?", (article_id,)
    ).fetchone()
    if row is None:
        abort(404)
    if row["source_url"]:  # imported news lives on the original site
        return redirect(row["source_url"])
    return render_template(
        "article.html", a=row, is_admin=session.get("is_admin", False)
    )


def article_to_api(row):
    """Shape our own articles like NewsAPI ones so the frontend treats both alike."""
    imported = bool(row["source_url"])
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "url": row["source_url"] if imported else url_for("article", article_id=row["id"]),
        "urlToImage": row["image"] or None,
        "publishedAt": row["created_at"],
        "category": row["category"],
        "source": {"name": row["source"] if imported else "Our News"},
        "own": not imported,
    }


@app.route("/api/my-news")
def my_news():
    category = request.args.get("category", "general")
    keyword = request.args.get("q", "").strip()

    sql = "SELECT * FROM articles"
    where, params = [], []
    # Home ("general") shows everything. "ours" shows only what you uploaded.
    if category == "ours":
        where.append("source_url = ''")
    elif category in CATEGORIES and category != "general":
        where.append("category = ?")
        params.append(category)
    if keyword:
        where.append("(title LIKE ? OR description LIKE ?)")
        like = f"%{keyword}%"
        params += [like, like]
    if where:
        sql += " WHERE " + " AND ".join(where)
    # Your own stories from the last 24 hours stay on top, then newest first.
    pin_after = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    sql += (
        " ORDER BY (source_url = '' AND created_at >= ?) DESC, "
        "created_at DESC, id DESC LIMIT 60"
    )
    params.append(pin_after)

    rows = get_db().execute(sql, params).fetchall()
    return jsonify({"status": "ok", "articles": [article_to_api(r) for r in rows]})


# --------------------------------------------------------------------------
# NewsAPI proxy with a small in-memory cache (saves your daily API quota)
# --------------------------------------------------------------------------
CACHE = {}
CACHE_TTL = 600  # seconds
CACHE_MAX = 200


def newsapi_get(url, params):
    if not API_KEY:
        return {"status": "error", "message": "NEWS_API_KEY is not set in .env"}, 500

    key = (url, tuple(sorted(params.items())))
    hit = CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1], 200

    try:
        response = requests.get(
            url, params={**params, "apiKey": API_KEY}, timeout=10
        )
        data = response.json()
    except (requests.exceptions.RequestException, ValueError):
        return {"status": "error", "message": "Unable to connect to NewsAPI."}, 502

    if response.status_code != 200:
        return (
            {
                "status": "error",
                "message": data.get("message", "Something went wrong"),
            },
            response.status_code,
        )

    if len(CACHE) >= CACHE_MAX:
        CACHE.clear()
    CACHE[key] = (time.time(), data)
    return data, 200


@app.route("/news")
def news():
    category = request.args.get("category", "general")
    if category not in CATEGORIES:
        category = "general"
    if not API_KEY:  # NewsAPI is optional now; RSS import fills the feed
        return jsonify({"status": "ok", "articles": []})
    data, status = newsapi_get(
        NEWS_URL, {"country": "in", "category": category, "pageSize": 20}
    )
    return jsonify(data), status


@app.route("/search")
def search():
    keyword = request.args.get("q", "").strip()
    if not keyword:
        return (
            jsonify({"status": "error", "message": "Please enter a search keyword."}),
            400,
        )
    if not API_KEY:
        return jsonify({"status": "ok", "articles": []})
    data, status = newsapi_get(
        SEARCH_URL,
        {"q": keyword, "language": "en", "sortBy": "publishedAt", "pageSize": 20},
    )
    return jsonify(data), status


# --------------------------------------------------------------------------
# Admin: upload / edit / delete news
# --------------------------------------------------------------------------
@app.route("/admin")
@admin_required
def admin():
    rows = get_db().execute(
        "SELECT * FROM articles WHERE source_url = '' "
        "ORDER BY created_at DESC, id DESC"
    ).fetchall()
    return render_template("admin.html", articles=rows, edit=None, form={})


@app.route("/admin/add", methods=["POST"])
@admin_required
def admin_add():
    data, errors = read_article_form()
    if errors:
        for e in errors:
            flash(e, "error")
        rows = get_db().execute(
            "SELECT * FROM articles WHERE source_url = '' "
        "ORDER BY created_at DESC, id DESC"
        ).fetchall()
        return render_template("admin.html", articles=rows, edit=None, form=data), 400

    ts = now_iso()
    db = get_db()
    db.execute(
        "INSERT INTO articles (title, description, content, category, image, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            data["title"],
            data["description"],
            data["content"],
            data["category"],
            data["image"],
            ts,
            ts,
        ),
    )
    db.commit()
    flash("News published.", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/edit/<int:article_id>", methods=["GET", "POST"])
@admin_required
def admin_edit(article_id):
    db = get_db()
    row = db.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
    if row is None or row["source_url"]:  # imported news cannot be edited
        abort(404)

    if request.method == "GET":
        return render_template("admin.html", articles=[], edit=row, form=dict(row))

    data, errors = read_article_form(existing=row)
    if errors:
        for e in errors:
            flash(e, "error")
        return render_template("admin.html", articles=[], edit=row, form=data), 400

    if data["image"] != row["image"]:
        delete_local_image(row["image"])

    db.execute(
        "UPDATE articles SET title=?, description=?, content=?, category=?, "
        "image=?, updated_at=? WHERE id=?",
        (
            data["title"],
            data["description"],
            data["content"],
            data["category"],
            data["image"],
            now_iso(),
            article_id,
        ),
    )
    db.commit()
    flash("News updated.", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/delete/<int:article_id>", methods=["POST"])
@admin_required
def admin_delete(article_id):
    db = get_db()
    row = db.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
    if row is None or row["source_url"]:
        abort(404)
    delete_local_image(row["image"])
    db.execute("DELETE FROM articles WHERE id = ?", (article_id,))
    db.commit()
    flash("News deleted.", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/fetch-now", methods=["POST"])
@admin_required
def admin_fetch_now():
    if IMPORT_STATUS["running"]:
        flash("An import is already running. Please wait a minute.", "error")
    else:
        threading.Thread(
            target=run_import, args=(DB_PATH, KEEP_DAYS), daemon=True
        ).start()
        flash("Fetching latest news started. Refresh this page in about a minute.", "ok")
    return redirect(url_for("admin"))


@app.errorhandler(413)
def too_large(_e):
    return "File too large. Maximum upload size is 6 MB.", 413


init_db()

# With the debug reloader the file is loaded twice; only the real worker
# (WERKZEUG_RUN_MAIN=true) should start the background importer.
if AUTO_IMPORT and (not DEBUG or os.environ.get("WERKZEUG_RUN_MAIN") == "true"):
    start_background(DB_PATH, FEED_REFRESH_MINUTES, KEEP_DAYS)

if __name__ == "__main__":
    app.run(debug=DEBUG)
