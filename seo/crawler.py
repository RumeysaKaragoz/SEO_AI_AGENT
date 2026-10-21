import time
from collections import deque
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

import random

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
]
HEADERS = {"User-Agent": random.choice(USER_AGENTS)}
SKIP_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
    ".pdf", ".zip", ".rar", ".mp3", ".mp4", ".avi",
    ".css", ".js", ".xml", ".json", ".doc", ".docx", ".xls", ".xlsx",
)


def fetch_page(url, timeout=10):
    """
    Tek bir sayfayı indirir ve sonucu HER durumda bir sözlük olarak döndürür:
      {"url": ..., "status_code": int | None, "soup": BeautifulSoup | None, "error": str | None}
    Başarılı HTML sayfalarında soup dolu, error None'dır.
    """
    result = {"url": url, "status_code": None, "soup": None, "error": None}
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        result["error"] = f"{type(e).__name__}: {e}"
        print(f"Bir hata oluştu: {e} ({url})")
        return result

    result["status_code"] = response.status_code
    if response.status_code == 429:
        result["error"] = "HTTP 429 Too Many Requests"
        print(f"⚠️ Çok fazla istek atıldı (429), 15 saniye bekleniyor... ({url})")
        time.sleep(15)
    elif response.status_code != 200:
        result["error"] = f"HTTP {response.status_code}"
        print(f"Hata: Sayfaya ulaşılamadı. Status Code: {response.status_code} ({url})")
    elif "text/html" not in response.headers.get("Content-Type", ""):
        result["error"] = "HTML olmayan içerik"
    else:
        result["soup"] = BeautifulSoup(response.text, "html.parser")
    return result


def crawl(url, timeout=10):
    """Tek sayfa için eski arayüz: BeautifulSoup döndürür (hata olursa None)."""
    return fetch_page(url, timeout)["soup"]


def normalize_url(url):
    """Fragment (#...) ve sondaki '/' farklarını temizler; aynı sayfa tek URL olsun."""
    url, _ = urldefrag(url)
    return url.rstrip("/")


def is_valid_link(url, base_domain):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):   # mailto:, tel:, javascript: elenir
        return False
    if parsed.netloc != base_domain:             # sadece aynı site
        return False
    if parsed.path.lower().endswith(SKIP_EXTENSIONS):
        return False
    return True


def crawl_site(start_url, max_pages=100, delay=0.5):
    """
    start_url'den başlayıp aynı domain içindeki sayfaları max_pages kadar tarar.

    queue        : taranmayı bekleyen URL'ler (sıra)
    seen_urls    : kuyruğa bir kez eklenmiş tüm URL'ler (tekrar eklemeyi önler)
    visited_urls : kuyruktan alınıp işlenen URL'ler

    Dönüş: fetch_page sonuçlarının listesi (tarama sırasıyla). Başarısız sayfalar
    da listede yer alır (soup=None, status_code/error dolu).
    """
    start_url = normalize_url(start_url)
    base_domain = urlparse(start_url).netloc

    queue = deque([start_url])
    seen_urls = {start_url}
    visited_urls = set()
    results = []

    while queue and len(visited_urls) < max_pages:
        current_url = queue.popleft()
        visited_urls.add(current_url)

        print(f"[{len(visited_urls)}/{max_pages}] Taranıyor: {current_url}")
        result = fetch_page(current_url)
        results.append(result)

        soup = result["soup"]
        if soup is not None:
            for a in soup.find_all("a", href=True):
                link = normalize_url(urljoin(current_url, a["href"]))
                if link in seen_urls or not is_valid_link(link, base_domain):
                    continue
                seen_urls.add(link)
                queue.append(link)

        if delay:
            actual_delay = random.uniform(delay, delay * 2.0)
            time.sleep(actual_delay)

    ok = sum(1 for r in results if r["soup"] is not None)
    print(
        f"\nTarama bitti. İşlenen: {len(visited_urls)}, "
        f"başarılı: {ok}, kuyrukta kalan: {len(queue)}"
    )
    return results