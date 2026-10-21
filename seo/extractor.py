"""Sayfadan HAM veri çıkarır (pages tablosuna yazılacak alanlar). Kural yargısı içermez."""
import hashlib

from seo.rules import content_images

CONTENT_LIMIT = 5000      # content_text için en fazla karakter
MIN_HASH_LENGTH = 50      # bundan kısa metinlerde içerik karşılaştırması anlamsız (boş/yükleniyor sayfaları)
_SKIP_PARENTS = {"script", "style", "noscript", "title"}


def extract_page_data(soup):
    title_tag = soup.find("title")
    meta_desc = soup.find("meta", attrs={"name": "description"})
    h1_tags = soup.find_all("h1")
    images = content_images(soup)  # check_images ile aynı tanım (<noscript> piksel görselleri hariç)

    body = soup.body or soup
    parts = (
        s.strip() for s in body.find_all(string=True) if s.parent.name not in _SKIP_PARENTS
    )
    full_text = " ".join(p for p in parts if p)
    content_hash = None
    if len(full_text) >= MIN_HASH_LENGTH:
        content_hash = hashlib.sha1(full_text.lower().encode("utf-8")).hexdigest()

    return {
        "title": title_tag.get_text(strip=True) if title_tag else None,
        "meta_description": meta_desc.get("content", "").strip() if meta_desc else None,
        "h1_count": len(h1_tags),
        "h1_text": h1_tags[0].get_text(strip=True) if h1_tags else None,
        "image_count": len(images),
        # check_images ile aynı tanım: alt özniteliği hiç yok (alt="" sayılmaz)
        "images_missing_alt": sum(1 for img in images if img.get("alt") is None),
        "content_text": full_text[:CONTENT_LIMIT],
        "content_hash": content_hash,  # veritabanında pages.extra_data (JSON) içinde saklanır
    }