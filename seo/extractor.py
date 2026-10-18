"""Sayfadan HAM veri çıkarır (pages tablosuna yazılacak alanlar). Kural yargısı içermez."""

CONTENT_LIMIT = 5000  # content_text için en fazla karakter
_SKIP_PARENTS = {"script", "style", "noscript", "title"}


def extract_page_data(soup):
    title_tag = soup.find("title")
    meta_desc = soup.find("meta", attrs={"name": "description"})
    h1_tags = soup.find_all("h1")
    images = soup.find_all("img")

    body = soup.body or soup
    parts = (
        s.strip() for s in body.find_all(string=True) if s.parent.name not in _SKIP_PARENTS
    )
    text = " ".join(p for p in parts if p)

    return {
        "title": title_tag.get_text(strip=True) if title_tag else None,
        "meta_description": meta_desc.get("content", "").strip() if meta_desc else None,
        "h1_count": len(h1_tags),
        "h1_text": h1_tags[0].get_text(strip=True) if h1_tags else None,
        "image_count": len(images),
        # check_images ile aynı tanım: alt yok ya da boş
        "images_missing_alt": sum(1 for img in images if not img.get("alt")),
        "content_text": text[:CONTENT_LIMIT],
    }