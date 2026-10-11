from seo.crawler import crawl
def first_check(soup):
    issues=[]
    if soup is None:
        print("Analiz yapılamıyor çünkü sayfa içeriği alınamadı.")
        return ["Sayfa içeriği alınamadı."]
    return issues
def check_title(soup):
    issues = []
    title_tag=soup.find("title")
    if not title_tag:
        issues.append("Eksik: Sayfada <title> etiketi bulunamadı.")
    elif len(title_tag.text)>55:
        issues.append("Uyarı: <title> etiketi 55 karakterden uzun.")
    elif len(title_tag.text)<10:
        issues.append("Uyarı: <title> etiketi 10 karakterden kısa.")

    return issues

def check_headings(soup):
    issues = []
    h1_tags=soup.find_all("h1")
    if len(h1_tags)==0:
        issues.append("Eksik: Sayfada <h1> etiketi bulunamadı.")
    elif len(h1_tags)>1:
        issues.append("Uyarı: Sayfada birden fazla <h1> etiketi bulunuyor.")
    return issues
   
def check_images(soup):
    issues = []     
    images=soup.find_all("img")
    missing_alt=[img for img in images if not img.get("alt")]
    for img in missing_alt:
        issues.append(f"Uyarı: <img> etiketi için alt metin eksik. (src: {img.get('src')})")
        
    return issues

def check_links(soup):
    issues = []
    links=soup.find_all("a")
    for link in links:
        href=link.get("href")
        if not href:
            issues.append("Uyarı: <a> etiketi için href özniteliği eksik.")
        elif href.startswith("#"):
            issues.append(f"Uyarı: <a> etiketi için geçersiz href özniteliği (src: {href})")
    return issues
def check_meta_description(soup):
    issues = []
    meta_description=soup.find("meta", attrs={"name": "description"})
    if not meta_description:
        issues.append("Eksik: Sayfada <meta name='description'> etiketi bulunamadı.")
    elif len(meta_description.get("content", ""))>160:
        issues.append("Uyarı: <meta name='description'> etiketi 160 karakterden uzun.")
    elif len(meta_description.get("content", ""))<50:
        issues.append("Uyarı: <meta name='description'> etiketi 50 karakterden kısa.")
    return issues
def check_meta_keywords(soup):
    issues = []
    meta_keywords=soup.find("meta", attrs={"name": "keywords"})
    if not meta_keywords:
        issues.append("Eksik: Sayfada <meta name='keywords'> etiketi bulunamadı.")
    elif len(meta_keywords.get("content", ""))>10:
        issues.append("Uyarı: <meta name='keywords'> etiketi 10 kelimeden uzun.")
    return issues
def check_canonical(soup):
    issues = []
    canonical_link=soup.find("link", attrs={"rel": "canonical"})
    if not canonical_link:
        issues.append("Eksik: Sayfada <link rel='canonical'> etiketi bulunamadı.")
    return issues
def check_robots_txt(soup):
    issues = []
    robots_link=soup.find("link", attrs={"rel": "robots"})
    if not robots_link:
        issues.append("Eksik: Sayfada <link rel='robots'> etiketi bulunamadı.")
    return issues
def check_sitemap(soup):
    issues = []
    sitemap_link=soup.find("link", attrs={"rel": "sitemap"})
    if not sitemap_link:
        issues.append("Eksik: Sayfada <link rel='sitemap'> etiketi bulunamadı.")
    return issues
def check_open_graph(soup):
    issues = []
    og_title=soup.find("meta", attrs={"property": "og:title"})
    if not og_title:
        issues.append("Eksik: Sayfada <meta property='og:title'> etiketi bulunamadı.")
    og_description=soup.find("meta", attrs={"property": "og:description"})
    if not og_description:
        issues.append("Eksik: Sayfada <meta property='og:description'> etiketi bulunamadı.")
    og_image=soup.find("meta", attrs={"property": "og:image"})
    if not og_image:
        issues.append("Eksik: Sayfada <meta property='og:image'> etiketi bulunamadı.")
    return issues
def check_twitter_cards(soup):
    issues = []
    twitter_card=soup.find("meta", attrs={"name": "twitter:card"})
    if not twitter_card:
        issues.append("Eksik: Sayfada <meta name='twitter:card'> etiketi bulunamadı.")
    twitter_title=soup.find("meta", attrs={"name": "twitter:title"})
    if not twitter_title:
        issues.append("Eksik: Sayfada <meta name='twitter:title'> etiketi bulunamadı.")
    twitter_description=soup.find("meta", attrs={"name": "twitter:description"})
    if not twitter_description:
        issues.append("Eksik: Sayfada <meta name='twitter:description'> etiketi bulunamadı.")
    twitter_image=soup.find("meta", attrs={"name": "twitter:image"})
    if not twitter_image:
        issues.append("Eksik: Sayfada <meta name='twitter:image'> etiketi bulunamadı.")
    return issues
def check_schema_markup(soup):
    issues = []
    schema_script=soup.find("script", attrs={"type": "application/ld+json"})
    if not schema_script:
        issues.append("Eksik: Sayfada <script type='application/ld+json'> etiketi bulunamadı.")
    return issues
def check_page_speed(soup):
    issues = []
    # Page speed kontrolü için bir API veya kütüphane kullanılabilir. --- IGNORE ---
    return issues
def check_mobile_friendly(soup):
    issues = []
    # Mobil uyumluluk kontrolü için bir API veya kütüphane kullanılabilir. --- IGNORE ---
    return issues
def check_ssl_certificate(soup):
    issues = []
    # SSL sertifikası kontrolü için bir API veya kütüphane kullanılabilir. --- IGNORE ---
    return issues
def check_broken_links(soup):
    issues = []
    links=soup.find_all("a")
    for link in links:
        href=link.get("href")
        if href and not href.startswith("#"):
            try:
                response=requests.head(href, allow_redirects=True, timeout=5)
                if response.status_code>=400:
                    issues.append(f"Uyarı: Kırık link bulundu. (href: {href}, Status Code: {response.status_code})")
            except requests.RequestException as e:
                issues.append(f"Uyarı: Kırık link bulundu. (href: {href}, Hata: {e})")
    return issues
def check_redirects(soup):
    issues = []
    # Yönlendirme kontrolü için bir API veya kütüphane kullanılabilir. --- IGNORE ---
    return issues
def check_canonicalization(soup):
    issues = []
    # Canonicalization kontrolü için bir API veya kütüphane kullanılabilir. --- IGNORE ---
    return issues
def check_duplicate_content(soup):
    issues = []
    # Duplicate content kontrolü için bir API veya kütüphane kullanılabilir. --- IGNORE ---
    return issues
def check_internationalization(soup):
    issues = []
    # Uluslararasılaştırma kontrolü için bir API veya kütüphane kullanılabilir. --- IGNORE ---
    return issues