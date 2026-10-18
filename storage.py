"""Analiz sonuçlarını veritabanına yazan yardımcı fonksiyonlar."""
from urllib.parse import urlparse


def site_origin(url):
    """https://example.com/blog/x -> https://example.com"""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def get_or_create_site(connection, site_url):
    connection.execute("INSERT OR IGNORE INTO sites (url) VALUES (?)", (site_url,))
    return connection.execute("SELECT id FROM sites WHERE url = ?", (site_url,)).fetchone()[0]


def start_crawl_run(connection, site_id):
    cursor = connection.execute(
        "INSERT INTO crawl_runs (site_id, status) VALUES (?, 'running')", (site_id,)
    )
    return cursor.lastrowid


def finish_crawl_run(connection, run_id, status, pages_crawled, report_path=None):
    connection.execute(
        "UPDATE crawl_runs SET status = ?, pages_crawled = ?, report_path = ?, "
        "ended_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, pages_crawled, report_path, run_id),
    )


def save_page(connection, run_id, result, data=None):
    """
    result: crawler.fetch_page sonucu (url, status_code, error)
    data  : extractor.extract_page_data sonucu (erişilemeyen sayfalarda None)
    """
    data = data or {}
    cursor = connection.execute(
        """
        INSERT INTO pages (
            crawl_run_id, url, status_code, error_message,
            title, meta_description, h1_count, h1_text,
            image_count, images_missing_alt, content_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id, result["url"], result["status_code"], result["error"],
            data.get("title"), data.get("meta_description"),
            data.get("h1_count"), data.get("h1_text"),
            data.get("image_count"), data.get("images_missing_alt"),
            data.get("content_text"),
        ),
    )
    return cursor.lastrowid


def save_issues(connection, page_id, issues):
    connection.executemany(
        "INSERT INTO issues (page_id, code, category, severity, message) VALUES (?, ?, ?, ?, ?)",
        [(page_id, i["code"], i["category"], i["severity"], i["message"]) for i in issues],
    )