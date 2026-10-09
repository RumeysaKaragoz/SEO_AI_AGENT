from crawler import crawl
from analyzer import analyze

if __name__ == "__main__":
    target_url = input("Analiz etmek istediğiniz web sayfasının URL'sini girin:")
    
    print(f"Analiz edilen URL: {target_url}")
    print("Site Taranıyor...")
    url = crawl(target_url)
    if url:
        print("Analiz ediliyor...")
        reports = analyze(url)
        if reports:
            print("SEO Analiz Sonuçları:")
            for report in reports:
                print(f"- {report}")
        else:
            print("Analiz tamamlandı. Sayfa SEO açısından iyi durumda.")
    else:
        print("Site taranırken bir hata oluştu.")