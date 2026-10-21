import requests

# ---------------------------------------------------------------------------
# Önem dereceleri TEK yerde: kodu değiştirmeden buradan ayarlanır.
#   critical : sayfanın aranabilirliğini doğrudan bozan eksik
#   warning  : düzeltilmesi önerilen, etkisi olası sorun
#   info     : iyileştirme fırsatı / en iyi uygulama
# ---------------------------------------------------------------------------
SEVERITY_BY_CODE = {
    "PAGE_CONTENT_MISSING": "critical",
    "TITLE_MISSING": "critical",
    "TITLE_TOO_LONG": "warning",
    "TITLE_TOO_SHORT": "warning",
    "META_DESC_MISSING": "warning",
    "META_DESC_TOO_LONG": "info",
    "META_DESC_TOO_SHORT": "info",
    "H1_MISSING": "warning",
    "H1_MULTIPLE": "info",
    "IMG_ALT_MISSING": "warning",
    "LINK_HREF_MISSING": "info",
    "LINK_NOT_CRAWLABLE": "info",
    "CANONICAL_MISSING": "warning",
    "OG_MISSING": "info",
    "TWITTER_CARD_MISSING": "info",
    "SCHEMA_MISSING": "info",
    "META_KEYWORDS_MISSING": "info",
    "META_KEYWORDS_TOO_MANY": "info",
    "LINK_BROKEN": "warning",
    # site geneli kurallar (site_rules.py)
    "ROBOTS_TXT_MISSING": "info",
    "SITEMAP_MISSING": "warning",
    "DUPLICATE_TITLE": "warning",
    "DUPLICATE_META_DESC": "info",
    "DUPLICATE_CONTENT": "warning",
}


def make_issue(code, category, message):
    """Sorun sözlüğü üretir. Önem derecesi SEVERITY_BY_CODE tablosundan gelir."""
    return {
        "code": code,
        "category": category,
        "severity": SEVERITY_BY_CODE[code],
        "message": message,
    }


# --------------------------- sayfa bazlı kurallar ---------------------------

def check_title(soup):
    issues = []
    title_tag = soup.find("title")
    if not title_tag:
        issues.append(make_issue("TITLE_MISSING", "on-page",
                                 "Sayfada <title> etiketi bulunamadı."))
        return issues
    length = len(title_tag.get_text(strip=True))
    if length > 55:
        issues.append(make_issue("TITLE_TOO_LONG", "on-page",
                                 f"<title> etiketi 55 karakterden uzun ({length}); arama sonuçlarında kesilebilir."))
    elif length < 10:
        issues.append(make_issue("TITLE_TOO_SHORT", "on-page",
                                 f"<title> etiketi 10 karakterden kısa ({length})."))
    return issues


def check_meta_description(soup):
    issues = []
    meta = soup.find("meta", attrs={"name": "description"})
    if not meta:
        issues.append(make_issue("META_DESC_MISSING", "on-page",
                                 "Sayfada <meta name='description'> etiketi bulunamadı."))
        return issues
    length = len(meta.get("content", "").strip())
    if length > 160:
        issues.append(make_issue("META_DESC_TOO_LONG", "on-page",
                                 f"<meta name='description'> etiketi 160 karakterden uzun ({length}); kesilebilir."))
    elif length < 50:
        issues.append(make_issue("META_DESC_TOO_SHORT", "on-page",
                                 f"<meta name='description'> etiketi 50 karakterden kısa ({length})."))
    return issues


def check_headings(soup):
    issues = []
    h1_count = len(soup.find_all("h1"))
    if h1_count == 0:
        issues.append(make_issue("H1_MISSING", "on-page",
                                 "Sayfada <h1> etiketi bulunamadı."))
    elif h1_count > 1:
        issues.append(make_issue("H1_MULTIPLE", "on-page",
                                 f"Sayfada birden fazla <h1> etiketi bulunuyor ({h1_count})."))
    return issues


def content_images(soup):
    """
    Sayfa görselleri; <noscript> içindekiler (Facebook/Google izleme pikselleri gibi) hariç.
    Bunlar ziyaretçiye gösterilen içerik görseli değildir, alt metin gerektirmez.
    """
    return [img for img in soup.find_all("img") if img.find_parent("noscript") is None]


def check_images(soup):
    """
    Yalnızca alt özniteliği HİÇ OLMAYAN görseller işaretlenir.
    alt="" geçerlidir: dekoratif görseller için doğru kullanımdır (WCAG/HTML),
    bu yüzden hata sayılmaz.
    """
    issues = []
    for img in content_images(soup):
        if img.get("alt") is None:
            issues.append(make_issue("IMG_ALT_MISSING", "on-page",
                                     f"<img> etiketi için alt özniteliği eksik. (src: {img.get('src')})"))
    return issues


def check_links(soup):
    """
    Sayfa içi bağlantılar (#bolum) geçerlidir, işaretlenmez. İşaretlenenler:
      - href'i olmayan/boş <a>
      - href="#" veya javascript: ile başlayan, tarayıcıların izleyemeyeceği linkler
    Aynı sayfadaki tekrarlar tek bulguda sayılarak toplanır (gürültüyü azaltmak için).
    """
    missing = 0
    not_crawlable = 0
    for link in soup.find_all("a"):
        href = (link.get("href") or "").strip()
        if not href:
            missing += 1
        elif href == "#" or href.lower().startswith("javascript:"):
            not_crawlable += 1

    issues = []
    if missing:
        issues.append(make_issue("LINK_HREF_MISSING", "on-page",
                                 f"{missing} adet <a> etiketinde href özniteliği eksik veya boş."))
    if not_crawlable:
        issues.append(make_issue("LINK_NOT_CRAWLABLE", "on-page",
                                 f"{not_crawlable} adet <a> etiketi href=\"#\" veya javascript: kullanıyor; "
                                 "arama motorları bu bağlantıları izleyemez."))
    return issues


def check_canonical(soup):
    issues = []
    if not soup.find("link", rel="canonical"):
        issues.append(make_issue("CANONICAL_MISSING", "technical",
                                 "Sayfada <link rel='canonical'> etiketi bulunamadı."))
    return issues


def check_open_graph(soup):
    """Eksik og: etiketleri tek bulguda listelenir."""
    required = ("og:title", "og:description", "og:image")
    missing = [p for p in required if not soup.find("meta", attrs={"property": p})]
    if not missing:
        return []
    return [make_issue("OG_MISSING", "social",
                       "Open Graph etiketleri eksik: " + ", ".join(missing))]


def check_twitter_cards(soup):
    """
    Yalnızca twitter:card aranır; başlık/açıklama/görsel Open Graph etiketlerine
    geri dönüşle (fallback) kullanılabildiği için ayrıca zorunlu tutulmaz.
    """
    if soup.find("meta", attrs={"name": "twitter:card"}):
        return []
    return [make_issue("TWITTER_CARD_MISSING", "social",
                       "Sayfada <meta name='twitter:card'> etiketi bulunamadı.")]


def check_schema_markup(soup):
    if soup.find("script", attrs={"type": "application/ld+json"}):
        return []
    return [make_issue("SCHEMA_MISSING", "structured-data",
                       "Sayfada yapılandırılmış veri (<script type='application/ld+json'>) bulunamadı.")]


# ------------- yazılı ama bilerek analyzer.CHECKS içinde OLMAYAN kurallar -------------

def check_meta_keywords(soup):
    """Google meta keywords'ü sıralamada kullanmadığı için varsayılan olarak çalıştırılmaz."""
    issues = []
    meta = soup.find("meta", attrs={"name": "keywords"})
    if not meta:
        issues.append(make_issue("META_KEYWORDS_MISSING", "on-page",
                                 "Sayfada <meta name='keywords'> etiketi bulunamadı."))
    elif len([k for k in meta.get("content", "").split(",") if k.strip()]) > 10:
        issues.append(make_issue("META_KEYWORDS_TOO_MANY", "on-page",
                                 "<meta name='keywords'> etiketi 10 kelimeden uzun."))
    return issues


def check_broken_links(soup):
    """Her link için ağ isteği yaptığından yavaştır; varsayılan olarak çalıştırılmaz."""
    issues = []
    for link in soup.find_all("a"):
        href = link.get("href")
        if href and href.startswith(("http://", "https://")):
            try:
                response = requests.head(href, allow_redirects=True, timeout=5)
                if response.status_code >= 400:
                    issues.append(make_issue("LINK_BROKEN", "technical",
                                             f"Kırık link bulundu. (href: {href}, Status Code: {response.status_code})"))
            except requests.RequestException as e:
                issues.append(make_issue("LINK_BROKEN", "technical",
                                         f"Kırık link bulundu. (href: {href}, Hata: {e})"))
    return issues


# --- Henüz uygulanmamış iskeletler ---

def check_page_speed(soup):
    return []

def check_mobile_friendly(soup):
    return []

def check_ssl_certificate(soup):
    return []

def check_redirects(soup):
    return []

def check_canonicalization(soup):
    return []

def check_internationalization(soup):
    return []