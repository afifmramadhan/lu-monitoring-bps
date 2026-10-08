import feedparser
import trafilatura
from urllib.parse import urlsplit


def collect_news(feeds, limit=200, keyword=''):
    results = []
    seen = set()
    terms = [t.strip().lower() for t in keyword.split(',') if t.strip()]
    for feed_url in feeds:
        feed = feedparser.parse(feed_url)
        for e in feed.entries:
            url = e.get('link', '')
            if not url or url in seen or urlsplit(url).scheme not in ('http', 'https'):
                continue
            seen.add(url)
            title = e.get('title', '')
            description = e.get('summary', '')
            if terms and not any(t in (title + ' ' + description).lower() for t in terms):
                continue
            try:
                downloaded = trafilatura.fetch_url(url)
                body = trafilatura.extract(downloaded) if downloaded else ''
            except Exception:
                body = ''
            text = body or (title + '\n' + description)
            if not text.strip():
                continue
            results.append({'source':'news','title':title,'url':url,'text':text[:15000],
                            'published':e.get('published','')})
            if len(results) >= limit:
                return results
    return results
