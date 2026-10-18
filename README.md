# Daily News (Flask)

Latest news **automatically from RSS feeds**, plus **your own uploaded news**, in one feed.

## Features
- **Automatic news:** every 60 minutes the app reads RSS feeds (The Hindu, NDTV, Times of India, TechCrunch, Gadgets 360...) and saves headline, short summary, picture and the link to the original article. "Read More" opens the original site.
- **Your own news:** `/admin` lets you upload, edit and delete stories with an image. They get an "Our News" badge, stay on top for 24 hours, and have their own tab.
- Optional: live headlines from NewsAPI (only if you set `NEWS_API_KEY`).
- Search, categories, dark mode (remembered), auto-refresh every 5 minutes.

## Setup
```bash
pip install -r requirements.txt
cp .env.example .env      # Windows: copy .env.example .env
# open .env and fill ADMIN_PASSWORD and SECRET_KEY (NEWS_API_KEY is optional)
python app.py
```
Open http://127.0.0.1:5000. Upload news at http://127.0.0.1:5000/admin

The first automatic import starts a few seconds after the app starts.
In `/admin` you can see when it last ran, how many articles it added, which
feed failed, and press **Fetch latest news now**.

## Choose your news sources
Open `feeds.py` and add / remove lines like
`("technology", "TechCrunch", "https://techcrunch.com/feed/")`.
Category must be one of: general, technology, business, sports, science, entertainment, health.
If a site changes its feed link, that feed shows up as failed in `/admin`; the rest keep working.

## Settings (.env)
| Name | Meaning | Default |
|---|---|---|
| `AUTO_IMPORT` | `0` switches automatic import off | `1` |
| `FEED_REFRESH_MINUTES` | how often to fetch (min 10) | `60` |
| `KEEP_DAYS` | imported news older than this is removed (your uploads are never removed) | `14` |
| `FLASK_DEBUG` | `1` only on your own laptop | `0` |

## Security notes
- Never upload or commit `.env` (it is in `.gitignore`).
- Use a long random `ADMIN_PASSWORD` and `SECRET_KEY`.
- If an API key was ever pasted into code or Drive/GitHub, revoke it and make a new one.
- Respect the news sites: only a short summary and a link are stored, never the full article.

## Files
- `app.py` – routes, SQLite database (`news.db`, created automatically), uploads
- `importer.py` – reads RSS/Atom feeds and saves new articles
- `feeds.py` – the list of news sources
- `templates/` – `index.html`, `article.html`, `admin.html`, `login.html`
- `static/` – `style.css`, `script.js`, `uploads/` (your uploaded images)
