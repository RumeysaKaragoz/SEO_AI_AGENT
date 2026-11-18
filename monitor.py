"""
Sürekli izleme: siteyi yeniden tarar, bir önceki tamamlanmış taramayla karşılaştırır, değişiklikleri
kaydeder ve YALNIZCA değişen/yeni sayfalar için AI önerisi üretilmesini tetikler.

Model yine yalnızca öneri üretir (applied_changes'a 'pending' yazılır). Onay ve siteye yazma
kullanıcının eylemidir; bu dosya canlı siteye hiçbir şey göndermez.

Kullanım:
    python monitor.py --url https://www.duses.tr                 -> bir kez tara + karşılaştır
    python monitor.py --url https://www.duses.tr --every 24      -> her 24 saatte bir tekrarla
    python monitor.py --url https://www.duses.tr --no-ai         -> yalnızca değişiklikleri algıla

Not: bu dosya kendi tablosunu (monitor_changes) kendisi oluşturur; database.py'ye dokunmaz.
"""
import argparse
import inspect
import json
import time
from datetime import datetime

from database import DB_PATH, get_connection, init_db

TRACKED_FIELDS = {"title": "Başlık", "meta_description": "Meta description", "h1_text": "H1"}
# Bu tür değişiklikler öneri üretimini tetikler
TRIGGER_TYPES = {"new_page", "field_changed", "content_changed", "new_issue"}

MONITOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS monitor_changes (
    id INTEGER PRIMARY KEY,
    crawl_run_id INTEGER NOT NULL,
    prev_run_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    change_type TEXT NOT NULL,   -- new_page | removed_page | field_changed | content_changed | new_issue | became_unreachable | recovered
    field TEXT,
    old_value TEXT,
    new_value TEXT,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (crawl_run_id) REFERENCES crawl_runs (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_monitor_run ON monitor_changes (crawl_run_id);
"""


def ensure_table(conn):
    conn.executescript(MONITOR_SCHEMA)
    conn.commit()


# ----------------------------------------------------------------------------
# Karşılaştırma (veritabanından okur, yalnızca monitor_changes'a yazar)
# ----------------------------------------------------------------------------

def previous_run_id(conn, run_id):
    """Aynı sitenin, run_id'den önceki son TAMAMLANMIŞ taraması (yoksa None)."""
    row = conn.execute(
        "SELECT p.id FROM crawl_runs p, crawl_runs c WHERE c.id = ? AND p.site_id = c.site_id "
        "AND p.status = 'completed' AND p.id < c.id ORDER BY p.id DESC LIMIT 1", (run_id,)).fetchone()
    return row[0] if row else None


def _hash_of(extra_data):
    try:
        return (json.loads(extra_data) or {}).get("content_hash") if extra_data else None
    except ValueError:
        return None


def load_run(conn, run_id):
    rows = conn.execute(
        "SELECT url, status_code, title, meta_description, h1_text, extra_data FROM pages "
        "WHERE crawl_run_id = ?", (run_id,)).fetchall()
    return {r[0]: {"status": r[1], "title": r[2], "meta_description": r[3], "h1_text": r[4],
                   "hash": _hash_of(r[5])} for r in rows}


def load_open_issue_codes(conn, run_id):
    rows = conn.execute(
        "SELECT p.url, i.code FROM issues i JOIN pages p ON p.id = i.page_id "
        "WHERE p.crawl_run_id = ? AND i.status = 'open'", (run_id,)).fetchall()
    out = {}
    for url, code in rows:
        out.setdefault(url, set()).add(code)
    return out


def _norm(text):
    return " ".join((text or "").split())


def diff_runs(old, new, old_issues=None, new_issues=None):
    """
    old/new: load_run sonuçları. Dönüş: [{"url", "type", "field", "old", "new"}].
    Yalnızca iki taramada da ulaşılabilir (200) olan sayfaların alanları karşılaştırılır.
    """
    changes = []
    add = lambda url, typ, field=None, o=None, n=None: changes.append(
        {"url": url, "type": typ, "field": field, "old": o, "new": n})

    for url, page in new.items():
        before = old.get(url)
        if before is None:
            if page["status"] == 200:
                add(url, "new_page")
            continue
        was_ok, is_ok = before["status"] == 200, page["status"] == 200
        if was_ok and not is_ok:
            add(url, "became_unreachable", o=str(before["status"]), n=str(page["status"]))
            continue
        if not was_ok and is_ok:
            add(url, "recovered", o=str(before["status"]), n=str(page["status"]))
            continue
        if not is_ok:
            continue
        for field in TRACKED_FIELDS:
            if _norm(before[field]) != _norm(page[field]):
                add(url, "field_changed", field, before[field], page[field])
        if before["hash"] and page["hash"] and before["hash"] != page["hash"]:
            add(url, "content_changed")

    for url, before in old.items():
        if url not in new and before["status"] == 200:
            add(url, "removed_page")

    if old_issues is not None and new_issues is not None:
        for url, codes in new_issues.items():
            if url in old:  # yeni sayfanın sorunları zaten 'new_page' ile raporlanır
                for code in sorted(codes - old_issues.get(url, set())):
                    add(url, "new_issue", n=code)
    return changes


def record_changes(conn, run_id, prev_id, changes):
    conn.executemany(
        "INSERT INTO monitor_changes (crawl_run_id, prev_run_id, url, change_type, field, old_value, new_value) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(run_id, prev_id, c["url"], c["type"], c["field"], c["old"], c["new"]) for c in changes])
    conn.commit()


def urls_with_open_suggestions(conn, urls):
    """Zaten bekleyen/onaylı önerisi olan adresler: aynı sayfaya üst üste öneri yığılmasın."""
    if not urls:
        return set()
    marks = ",".join("?" * len(urls))
    rows = conn.execute(
        f"SELECT DISTINCT p.url FROM applied_changes c JOIN pages p ON p.id = c.page_id "
        f"WHERE c.status IN ('pending', 'approved') AND p.url IN ({marks})", list(urls)).fetchall()
    return {r[0] for r in rows}


# ----------------------------------------------------------------------------
# Öneri tetikleme
# ----------------------------------------------------------------------------

def default_suggest(db_path, run_id, urls, model, brand, limit):
    """ai.generate_suggestions'ı yalnızca verilen adresler için çalıştırır."""
    import ai
    if "only_urls" not in inspect.signature(ai.generate_suggestions).parameters:
        raise RuntimeError("ai.py henüz 'only_urls' parametresini desteklemiyor (README'deki 3 satırlık yama gerekli)")
    return ai.generate_suggestions(db_path, run_id=run_id, limit=limit, model=model, brand=brand,
                                   only_urls=set(urls))


# ----------------------------------------------------------------------------
# Bir izleme turu
# ----------------------------------------------------------------------------

def run_cycle(target_url, db_path=DB_PATH, max_pages=100, delay=0.5, model=None, brand=None, limit=20,
              use_ai=True, crawl=None, suggest=None, log=print):
    """
    crawl  : main.run uyumlu çağrılabilir (varsayılan main.run); testte sahtesi verilir.
    suggest: (run_id, urls) -> özet; varsayılan default_suggest.
    Dönüş: {"run_id", "prev_run_id", "changes", "suggested_urls", "note"}
    """
    init_db(db_path)
    if crawl is None:
        from main import run as crawl
    result = crawl(target_url, max_pages=max_pages, db_path=db_path, delay=delay)
    run_id = result["run_id"]
    summary = {"run_id": run_id, "prev_run_id": None, "changes": [], "suggested_urls": [], "note": ""}
    if result["status"] != "completed":
        summary["note"] = "Tarama tamamlanamadı; karşılaştırma yapılmadı."
        log(summary["note"])
        return summary

    conn = get_connection(db_path)
    try:
        ensure_table(conn)
        prev_id = previous_run_id(conn, run_id)
        summary["prev_run_id"] = prev_id
        if prev_id is None:
            summary["note"] = "İlk tarama: karşılaştırılacak geçmiş yok. Sonraki turda değişiklikler algılanacak."
            log(summary["note"])
            return summary

        old, new = load_run(conn, prev_id), load_run(conn, run_id)
        changes = diff_runs(old, new, load_open_issue_codes(conn, prev_id), load_open_issue_codes(conn, run_id))
        record_changes(conn, run_id, prev_id, changes)
        summary["changes"] = changes

        counts = {}
        for c in changes:
            counts[c["type"]] = counts.get(c["type"], 0) + 1
        log(f"Tarama #{run_id} / önceki #{prev_id}: {len(changes)} değişiklik "
            f"({', '.join(f'{k}: {v}' for k, v in sorted(counts.items())) or 'yok'})")
        if len(new) >= max_pages:
            log(f"  uyarı: tarama {max_pages} sayfa sınırına ulaştı; 'removed_page' bulguları sınırdan "
                f"kaynaklanıyor olabilir.")

        if not use_ai:
            return summary
        urls = sorted({c["url"] for c in changes if c["type"] in TRIGGER_TYPES})
        skipped = urls_with_open_suggestions(conn, urls)
        urls = [u for u in urls if u not in skipped]
        if skipped:
            log(f"  {len(skipped)} sayfa atlandı (zaten bekleyen/onaylı önerisi var).")
        if not urls:
            return summary
        summary["suggested_urls"] = urls
    finally:
        conn.close()

    try:
        if suggest is not None:
            suggest(run_id, urls)
        else:
            default_suggest(db_path, run_id, urls, model, brand, limit)
    except RuntimeError as e:  # Ollama kapalı vb.: izleme sonucu yine de kayıtlı kalır
        summary["note"] = f"Öneri üretilemedi: {e}"
        log(summary["note"])
    return summary


def main():
    parser = argparse.ArgumentParser(description="Siteyi izler: tara, karşılaştır, değişenler için öneri üret.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--every", type=float, default=0, help="Saat cinsinden aralık; 0 = bir kez çalış")
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--limit", type=int, default=20, help="Tur başına en fazla kaç sayfaya öneri")
    parser.add_argument("--model", default=None)
    parser.add_argument("--brand", default=None)
    parser.add_argument("--no-ai", action="store_true", help="Yalnızca değişiklikleri algıla")
    args = parser.parse_args()

    url = args.url if args.url.startswith(("http://", "https://")) else "https://" + args.url
    if args.model is None:
        from ai import DEFAULT_MODEL
        args.model = DEFAULT_MODEL

    while True:
        print(f"\n=== İzleme turu: {datetime.now():%Y-%m-%d %H:%M:%S} ===")
        try:
            run_cycle(url, args.db, args.max_pages, model=args.model, brand=args.brand,
                      limit=args.limit, use_ai=not args.no_ai)
        except KeyboardInterrupt:
            raise
        except Exception as e:  # tek bir turun hatası döngüyü öldürmesin
            print(f"Tur başarısız: {type(e).__name__}: {e}")
        if not args.every:
            break
        print(f"Sonraki tur {args.every} saat sonra. Durdurmak için Ctrl+C.")
        try:
            time.sleep(args.every * 3600)
        except KeyboardInterrupt:
            print("Durduruldu.")
            break


if __name__ == "__main__":
    main()
