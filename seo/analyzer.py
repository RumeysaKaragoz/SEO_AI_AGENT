from crawler import crawl
from main import soup

def analyze(soup):
    issues=[]
    if soup is None:
        print("Analiz yapılamıyor çünkü sayfa içeriği alınamadı.")
        return

    title_tag=soup.find("title")
    if not title_tag:
        issues.append("Eksik: Sayfada <title> etiketi bulunamadı.")
    elif len(title_tag.text)>55:
        issues.append("Uyarı: <title> etiketi 55 karakterden uzun.")
    elif len(title_tag.text)<10:
        issues.append("Uyarı: <title> etiketi 10 karakterden kısa.")
        
    h1_tags=soup.find_all("h1")
    if len(h1_tags)==0:
        issues.append("Eksik: Sayfada <h1> etiketi bulunamadı.")
    elif len(h1_tags)>1:
        issues.append("Uyarı: Sayfada birden fazla <h1> etiketi bulunuyor.")
        
    images=soup.find_all("img")
    missing_alt=[img for img in images if not img.get("alt")]
    for img in missing_alt:
        issues.append(f"Uyarı: <img> etiketi için alt metin eksik. (src: {img.get('src')})")
        
    return issues
                