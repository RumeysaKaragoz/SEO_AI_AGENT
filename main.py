import os

from seo.crawler import crawl
from seo.analyzer import analyze

if __name__ == "__main__":
    target_url = input("Analiz etmek istediğiniz web sayfasının URL'sini girin:")
    
    print(f"Analiz edilen URL: {target_url}")
    print("Site Taranıyor...")
    soup = crawl(target_url)
    if soup:
        print("Analiz ediliyor...")
        reports = analyze(soup)
        report_content = f"URL: {target_url}\nTarih: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\nSEO Analiz Sonuçları:\n"
        if reports:
            print("SEO Analiz Sonuçları:")
            for report in reports:
                print(f"- {report}")
                report_content += f"- {report}\n"
        else:
         success_msg = "Analiz tamamlandı. Sayfa SEO açısından iyi durumda."
         print(success_msg)
         report_content += f"- {success_msg}\n"
        os.makedirs("reports", exist_ok=True)
        safe_filename = target_url.replace("https://", "").replace("http://", "").replace("/", "_") + ".txt"
        file_path = os.path.join("reports", safe_filename)
        with open(file_path, "w", encoding="utf-8") as report_file:
          report_file.write(report_content)
        print(f"\n[Bilgi] Rapor başarıyla kaydedildi: {file_path}")  
    else:
        print("Site taranırken bir hata oluştu.")