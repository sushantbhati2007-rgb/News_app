"""
News sources that are imported automatically.

Each line is:  (category, "Source name", "RSS feed URL")

- category must be one of: general, technology, business, sports,
  science, entertainment, health
- To add a source, add a line. To remove one, delete its line.
- If a feed stops working (the site changed its link), the Admin page
  shows which one failed, and the rest keep working.
"""

FEEDS = [
    # Home / general
    ("general", "The Hindu", "https://www.thehindu.com/news/national/feeder/default.rss"),
    ("general", "NDTV", "https://feeds.feedburner.com/ndtvnews-top-stories"),
    ("general", "Times of India", "https://timesofindia.indiatimes.com/rssfeedstopstories.cms"),
    # Technology
    ("technology", "The Hindu", "https://www.thehindu.com/sci-tech/technology/feeder/default.rss"),
    ("technology", "TechCrunch", "https://techcrunch.com/feed/"),
    ("technology", "Gadgets 360", "https://feeds.feedburner.com/gadgets360-latest"),
    # Business
    ("business", "The Hindu", "https://www.thehindu.com/business/feeder/default.rss"),
    ("business", "Times of India", "https://timesofindia.indiatimes.com/rssfeeds/1898055.cms"),
    # Sports
    ("sports", "The Hindu", "https://www.thehindu.com/sport/feeder/default.rss"),
    ("sports", "Times of India", "https://timesofindia.indiatimes.com/rssfeeds/4719148.cms"),
    # Science
    ("science", "The Hindu", "https://www.thehindu.com/sci-tech/science/feeder/default.rss"),
    # Entertainment
    ("entertainment", "The Hindu", "https://www.thehindu.com/entertainment/feeder/default.rss"),
    ("entertainment", "Times of India", "https://timesofindia.indiatimes.com/rssfeeds/1081479906.cms"),
    # Health
    ("health", "The Hindu", "https://www.thehindu.com/sci-tech/health/feeder/default.rss"),
]
