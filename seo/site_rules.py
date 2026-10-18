"""
Site geneli kurallar: tek bir sayfanın HTML'inden anlaşılamayan, tüm taramaya
(veya sitenin kök dosyalarına) bakan kontroller.
"""
import re
from collections import defaultdict

import requests

from seo.crawler import HEADERS
from seo.rules import make_issue


# ------------------------- robots.txt ve sitemap -------------------------

def _is_robots(response):
    if "text/html" in response.headers.get("Content-Type", "").lower():
        return False  # birçok site var olmayan her adrese 200 + HTML döndürür (soft 404)
    return bool(re.search(r"(?im)^\s*(user-agent|disallow|allow|sitemap)\s*:", response.text))


def _is_sitemap(response):
    if response.url.lower().endswith(".gz"):
        return True  # sıkıştırılmış sitemap: içeriği açmıyoruz, varlığı yeterli
    head = response.text.lstrip()[:500].lower()
    return "<urlset" in head or "<sitemapindex" in head


def _probe(url, validator, timeout=10):
    """
    (durum, metin) döndürür. durum:
      True  -> dosya var ve geçerli
      False -> yok (404/410) ya da geçerli bir dosya değil
      None  -> belirlenemedi (ağ hatası, 403, 5xx...). Bu durumda sorun RAPORLANMAZ.
    """
    try:
        response = requests.get(url, headers=HEADERS, timeout=timeout)
    except requests.RequestException:
        return None, ""
    if response.status_code in (404, 410):
        return False, ""
    if response.status_code != 200:
        return None, ""
    if validator(response):
        return True, response.text
    return False, ""


def check_site(origin):
    """origin: https://example.com — robots.txt ve sitemap'i kontrol eder."""
    issues = []

    robots_url = f"{origin}/robots.txt"
    state, robots_text = _probe(robots_url, _is_robots)
    if state is False:
        issues.append(make_issue("ROBOTS_TXT_MISSING", "technical",
                                 f"Site geneli: {robots_url} bulunamadı veya geçerli bir robots.txt değil."))

    declared = re.findall(r"(?im)^\s*sitemap\s*:\s*(\S+)", robots_text) if state else []
    candidates = declared[:3] or [f"{origin}/sitemap.xml"]
    states = [_probe(u, _is_sitemap)[0] for u in candidates]
    if True not in states and None not in states:
        if declared:
            message = "Site geneli: robots.txt'te belirtilen sitemap erişilemiyor veya geçersiz: " + ", ".join(candidates)
        else:
            message = f"Site geneli: {origin}/sitemap.xml bulunamadı ve robots.txt'te Sitemap satırı yok."
        issues.append(make_issue("SITEMAP_MISSING", "technical", message))
    return issues


# ------------------------- tekrar eden içerik -------------------------

def _groups(pages, key_fn):
    groups = defaultdict(list)
    for page in pages:
        key = key_fn(page["data"])
        if key:
            groups[key].append(page["url"])
    return [urls for urls in groups.values() if len(urls) > 1]


def _norm(text):
    return " ".join((text or "").lower().split())


_DUPLICATE_RULES = (
    ("DUPLICATE_TITLE", lambda d: _norm(d.get("title")),
     "Aynı <title> başka sayfalarda da kullanılıyor"),
    ("DUPLICATE_META_DESC", lambda d: _norm(d.get("meta_description")),
     "Aynı meta description başka sayfalarda da kullanılıyor"),
    ("DUPLICATE_CONTENT", lambda d: d.get("content_hash"),
     "Bu sayfanın içeriği başka sayfalarla birebir aynı"),
)


def check_duplicates(pages):
    """
    pages: [{"url": ..., "data": extract_page_data sonucu}, ...] (yalnızca başarılı sayfalar)
    Dönüş: {url: [sorunlar]} — her kopya sayfa için, diğer URL'leri de içeren bir bulgu.
    """
    result = {}
    for code, key_fn, label in _DUPLICATE_RULES:
        for urls in _groups(pages, key_fn):
            for url in urls:
                others = [u for u in urls if u != url]
                shown = ", ".join(others[:3])
                if len(others) > 3:
                    shown += f" (+{len(others) - 3} sayfa daha)"
                result.setdefault(url, []).append(
                    make_issue(code, "content", f"{label}: {shown}"))
    return result