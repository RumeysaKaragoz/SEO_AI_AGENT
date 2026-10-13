import time
from collections import deque
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
SKIP_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
    ".pdf", ".zip", ".rar", ".mp3", ".mp4", ".avi",
    ".css", ".js", ".xml", ".json", ".doc", ".docx", ".xls", ".xlsx",
)


def crawl(url, timeout=10):
    """Tek bir sayfayı indirip BeautifulSoup nesnesi döndürür (hata olursa None)."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=timeout)
        if response.status_code != 200:
            print(f"Hata: Sayfaya ulaşılamadı. Status Code: {response.status_code} ({url})")
            return None
        if "text/html" not in response.headers.get("Content-Type", ""):
            return None  # HTML olmayan içerikleri atla
        return BeautifulSoup(response.text, "html.parser")
    except requests.RequestException as e:
        print(f"Bir hata oluştu: {e} ({url})")
        return None


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

    Dönüş: {url: soup} sözlüğü (başarıyla indirilen sayfalar)
    """
    start_url = normalize_url(start_url)
    base_domain = urlparse(start_url).netloc

    queue = deque([start_url])   # list yerine deque: baştan çıkarma O(1)
    seen_urls = {start_url}
    visited_urls = set()
    pages = {}

    while queue and len(visited_urls) < max_pages:
        current_url = queue.popleft()
        visited_urls.add(current_url)

        print(f"[{len(visited_urls)}/{max_pages}] Taranıyor: {current_url}")
        soup = crawl(current_url)
        if soup is None:
            continue
        pages[current_url] = soup

        for a in soup.find_all("a", href=True):
            link = normalize_url(urljoin(current_url, a["href"]))
            if link in seen_urls or not is_valid_link(link, base_domain):
                continue
            seen_urls.add(link)
            queue.append(link)

        time.sleep(delay)  # siteyi yormamak için kısa bekleme

    print(
        f"\nTarama bitti. İşlenen: {len(visited_urls)}, "
        f"başarılı: {len(pages)}, kuyrukta kalan: {len(queue)}"
    )
    return pages
