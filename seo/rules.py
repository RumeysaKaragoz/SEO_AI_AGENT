import requests

# Her kural fonksiyonu bir sorun listesi döndürür. Sorun = make_issue(...) sözlüğü:
#   code     : makine tarafından okunur kısa kod (issues.code)
#   category : on-page / technical / social / structured-data ...
#   severity : "critical" | "warning" | "info"
#   message  : kullanıcıya gösterilen açıklama


def make_issue(code, category, severity, message):
    return {"code": code, "category": category, "severity": severity, "message": message}


def check_title(soup):
    issues = []
    title_tag = soup.find("title")
    if not title_tag:
        issues.append(make_issue("TITLE_MISSING", "on-page", "critical",
                                 "Sayfada <title> etiketi bulunamadı."))
        return issues
    length = len(title_tag.get_text(strip=True))
    if length > 55:
        issues.append(make_issue("TITLE_TOO_LONG", "on-page", "warning",
                                 f"<title> etiketi 55 karakterden uzun ({length})."))
    elif length < 10:
        issues.append(make_issue("TITLE_TOO_SHORT", "on-page", "warning",
                                 f"<title> etiketi 10 karakterden kısa ({length})."))
    return issues


def check_headings(soup):
    issues = []
    h1_count = len(soup.find_all("h1"))
    if h1_count == 0:
        issues.append(make_issue("H1_MISSING", "on-page", "critical",
                                 "Sayfada <h1> etiketi bulunamadı."))
    elif h1_count > 1:
        issues.append(make_issue("H1_MULTIPLE", "on-page", "warning",
                                 f"Sayfada birden fazla <h1> etiketi bulunuyor ({h1_count})."))
    return issues


def check_images(soup):
    issues = []
    for img in soup.find_all("img"):
        if not img.get("alt"):
            issues.append(make_issue("IMG_ALT_MISSING", "on-page", "warning",
                                     f"<img> etiketi için alt metin eksik. (src: {img.get('src')})"))
    return issues


def check_links(soup):
    issues = []
    for link in soup.find_all("a"):
        href = link.get("href")
        if not href:
            issues.append(make_issue("LINK_HREF_MISSING", "on-page", "warning",
                                     "<a> etiketi için href özniteliği eksik."))
        elif href.startswith("#"):
            issues.append(make_issue("LINK_HREF_INVALID", "on-page", "warning",
                                     f"<a> etiketi için geçersiz href özniteliği (href: {href})"))
    return issues


def check_meta_description(soup):
    issues = []
    meta = soup.find("meta", attrs={"name": "description"})
    if not meta:
        issues.append(make_issue("META_DESC_MISSING", "on-page", "warning",
                                 "Sayfada <meta name='description'> etiketi bulunamadı."))
        return issues
    length = len(meta.get("content", "").strip())
    if length > 160:
        issues.append(make_issue("META_DESC_TOO_LONG", "on-page", "warning",
                                 f"<meta name='description'> etiketi 160 karakterden uzun ({length})."))
    elif length < 50:
        issues.append(make_issue("META_DESC_TOO_SHORT", "on-page", "warning",
                                 f"<meta name='description'> etiketi 50 karakterden kısa ({length})."))
    return issues


# --- Aşağıdakiler henüz analyzer.CHECKS listesinde değil (literatür taramasından sonra eklenecek) ---

def check_meta_keywords(soup):
    issues = []
    meta = soup.find("meta", attrs={"name": "keywords"})
    if not meta:
        issues.append(make_issue("META_KEYWORDS_MISSING", "on-page", "info",
                                 "Sayfada <meta name='keywords'> etiketi bulunamadı."))
    elif len([k for k in meta.get("content", "").split(",") if k.strip()]) > 10:
        issues.append(make_issue("META_KEYWORDS_TOO_MANY", "on-page", "info",
                                 "<meta name='keywords'> etiketi 10 kelimeden uzun."))
    return issues


def check_canonical(soup):
    issues = []
    if not soup.find("link", rel="canonical"):
        issues.append(make_issue("CANONICAL_MISSING", "technical", "warning",
                                 "Sayfada <link rel='canonical'> etiketi bulunamadı."))
    return issues


def check_robots_txt(soup):
    # TODO: robots.txt sitenin kökündeki ayrı bir dosyadır; HTML <link> etiketine bakmak yanlış.
    issues = []
    if not soup.find("link", rel="robots"):
        issues.append(make_issue("ROBOTS_LINK_MISSING", "technical", "info",
                                 "Sayfada <link rel='robots'> etiketi bulunamadı."))
    return issues


def check_sitemap(soup):
    # TODO: sitemap.xml sitenin kökündeki ayrı bir dosyadır; HTML <link> etiketine bakmak yanlış.
    issues = []
    if not soup.find("link", rel="sitemap"):
        issues.append(make_issue("SITEMAP_LINK_MISSING", "technical", "info",
                                 "Sayfada <link rel='sitemap'> etiketi bulunamadı."))
    return issues


def check_open_graph(soup):
    issues = []
    for prop in ("og:title", "og:description", "og:image"):
        if not soup.find("meta", attrs={"property": prop}):
            issues.append(make_issue(f"OG_{prop[3:].upper()}_MISSING", "social", "info",
                                     f"Sayfada <meta property='{prop}'> etiketi bulunamadı."))
    return issues


def check_twitter_cards(soup):
    issues = []
    for name in ("twitter:card", "twitter:title", "twitter:description", "twitter:image"):
        if not soup.find("meta", attrs={"name": name}):
            issues.append(make_issue(f"TWITTER_{name[8:].upper()}_MISSING", "social", "info",
                                     f"Sayfada <meta name='{name}'> etiketi bulunamadı."))
    return issues


def check_schema_markup(soup):
    issues = []
    if not soup.find("script", attrs={"type": "application/ld+json"}):
        issues.append(make_issue("SCHEMA_MISSING", "structured-data", "info",
                                 "Sayfada <script type='application/ld+json'> etiketi bulunamadı."))
    return issues


def check_broken_links(soup):
    issues = []
    for link in soup.find_all("a"):
        href = link.get("href")
        if href and not href.startswith("#"):
            try:
                response = requests.head(href, allow_redirects=True, timeout=5)
                if response.status_code >= 400:
                    issues.append(make_issue("LINK_BROKEN", "technical", "warning",
                                             f"Kırık link bulundu. (href: {href}, Status Code: {response.status_code})"))
            except requests.RequestException as e:
                issues.append(make_issue("LINK_BROKEN", "technical", "warning",
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

def check_duplicate_content(soup):
    return []

def check_internationalization(soup):
    return []