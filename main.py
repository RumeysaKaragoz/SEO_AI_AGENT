import os
from datetime import datetime
from urllib.parse import urlparse

from database import DB_PATH, get_connection, init_db
from seo.analyzer import check_seo
from seo.crawler import crawl_site
from seo.extractor import extract_page_data
from storage import (
    finish_crawl_run,
    get_or_create_site,
    save_issues,
    save_page,
    site_origin,
    start_crawl_run,
)

MAX_PAGES = 100
SEVERITY_LABELS = {"critical": "KRİTİK", "warning": "UYARI", "info": "BİLGİ"}


def build_report(target_url, entries):
    """
    entries: [{"url", "status_code", "error", "issues"}, ...]
    Analiz tekrar ÇALIŞTIRILMAZ; veritabanına yazılan sonuçların aynısı rapora basılır.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    analyzed = [e for e in entries if e["error"] is None]
    failed = [e for e in entries if e["error"] is not None]
    pages_with_issues = sum(1 for e in analyzed if e["issues"])
    total_issues = sum(len(e["issues"]) for e in analyzed)

    sections = []
    for e in entries:
        lines = [f"Sayfa: {e['url']}"]
        if e["error"] is not None:
            lines.append(f"- Erişilemedi: {e['error']}")
        elif e["issues"]:
            lines.extend(
                f"- [{SEVERITY_LABELS[i['severity']]}] {i['message']}" for i in e["issues"]
            )
        else:
            lines.append("- Sorun bulunamadı.")
        sections.append("\n".join(lines))

    header = (
        f"URL: {target_url}\n"
        f"Tarih: {now}\n\n"
        f"ÖZET\n"
        f"- Analiz edilen sayfa: {len(analyzed)}\n"
        f"- Erişilemeyen sayfa: {len(failed)}\n"
        f"- Sorun bulunan sayfa: {pages_with_issues}\n"
        f"- Toplam sorun: {total_issues}\n"
    )
    separator = "\n\n" + "=" * 60 + "\n\n"
    return header + separator + separator.join(sections) + "\n"


def run(target_url, max_pages=MAX_PAGES, db_path=DB_PATH, reports_dir="reports", delay=0.5):
    """Tarar, analiz eder; sonuçları hem veritabanına hem TXT raporuna yazar."""
    init_db(db_path)
    connection = get_connection(db_path)
    try:
        site_id = get_or_create_site(connection, site_origin(target_url))
        run_id = start_crawl_run(connection, site_id)
        connection.commit()

        try:
            results = crawl_site(target_url, max_pages=max_pages, delay=delay)

            # Her sayfa için analiz BİR kez çalışır; sonuç hem DB'ye hem rapora gider.
            entries = []
            for result in results:
                soup = result["soup"]
                data = extract_page_data(soup) if soup is not None else None
                issues = check_seo(soup) if soup is not None else []
                page_id = save_page(connection, run_id, result, data)
                save_issues(connection, page_id, issues)
                entries.append({
                    "url": result["url"],
                    "status_code": result["status_code"],
                    "error": result["error"],
                    "issues": issues,
                })
            connection.commit()

            report_content = build_report(target_url, entries)
            os.makedirs(reports_dir, exist_ok=True)
            domain = urlparse(target_url).netloc.replace(":", "_")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_path = os.path.join(reports_dir, f"{domain}_{timestamp}_run{run_id}.txt")
            with open(report_path, "w", encoding="utf-8") as report_file:
                report_file.write(report_content)

            crawled = sum(1 for e in entries if e["error"] is None)
            status = "completed" if crawled > 0 else "failed"
            finish_crawl_run(connection, run_id, status, crawled, report_path)
            connection.commit()
        except Exception:
            connection.rollback()
            finish_crawl_run(connection, run_id, "failed", 0)
            connection.commit()
            raise

        return {"run_id": run_id, "status": status, "report_path": report_path,
                "report": report_content}
    finally:
        connection.close()


if __name__ == "__main__":
    target_url = input("Analiz etmek istediğiniz web sayfasının URL'sini girin: ").strip()
    if not target_url.startswith(("http://", "https://")):
        target_url = "https://" + target_url

    print(f"Analiz edilen URL: {target_url}")
    print(f"Site taranıyor (en fazla {MAX_PAGES} sayfa)...")
    summary = run(target_url)

    print(summary["report"])
    if summary["status"] == "completed":
        print(f"[Bilgi] Rapor kaydedildi: {summary['report_path']}")
        print(f"[Bilgi] Veritabanı kaydı: tarama #{summary['run_id']}")
    else:
        print("Site taranırken bir hata oluştu; hiçbir sayfa analiz edilemedi.")