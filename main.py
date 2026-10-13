import os
from datetime import datetime
from urllib.parse import urlparse
from seo.crawler import crawl_site
from seo.analyzer import check_seo

MAX_PAGES = 100


def build_report(target_url, pages):
    """Tüm sayfaların analiz sonuçlarını tek bir rapor metnine dönüştürür."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sections = []
    pages_with_issues = 0
    total_issues = 0

    for url, soup in pages.items():
        issues = check_seo(soup)
        if issues:
            pages_with_issues += 1
            total_issues += len(issues)
        lines = [f"Sayfa: {url}"]
        if issues:
            lines.extend(f"- {issue}" for issue in issues)
        else:
            lines.append("- Sorun bulunamadı.")
        sections.append("\n".join(lines))

    header = (
        f"URL: {target_url}\n"
        f"Tarih: {now}\n\n"
        f"ÖZET\n"
        f"- Analiz edilen sayfa: {len(pages)}\n"
        f"- Sorun bulunan sayfa: {pages_with_issues}\n"
        f"- Toplam sorun: {total_issues}\n"
    )
    separator = "\n\n" + "=" * 60 + "\n\n"
    return header + separator + separator.join(sections) + "\n"


if __name__ == "__main__":
    target_url = input("Analiz etmek istediğiniz web sayfasının URL'sini girin: ").strip()
    if not target_url.startswith(("http://", "https://")):
        target_url = "https://" + target_url

    print(f"Analiz edilen URL: {target_url}")
    print(f"Site taranıyor (en fazla {MAX_PAGES} sayfa)...")
    pages = crawl_site(target_url, max_pages=MAX_PAGES)

    if pages:
        print("Analiz ediliyor...")
        report_content = build_report(target_url, pages)

        os.makedirs("reports", exist_ok=True)
        domain = urlparse(target_url).netloc.replace(":", "_")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path = os.path.join("reports", f"{domain}_{timestamp}.txt")
        with open(file_path, "w", encoding="utf-8") as report_file:
            report_file.write(report_content)

        print(report_content)
        print(f"[Bilgi] Rapor başarıyla kaydedildi: {file_path}")
    else:
        print("Site taranırken bir hata oluştu.")