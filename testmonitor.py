"""monitor.py testleri. Gerçek tarama ve model YOK: sahte tarama ve sahte öneri üretici kullanılır."""
import json
import os
import tempfile

from database import get_connection, init_db
from monitor import (diff_runs, ensure_table, load_open_issue_codes, load_run, previous_run_id, run_cycle,
                     urls_with_open_suggestions)

BASE = "https://s.com"


def page(title="Başlık Metni Yeterince Uzun", meta="Açıklama metni elli karakterden uzun olacak şekilde yazıldı.",
         h1="Başlık", chash="h1", status=200, issues=()):
    return {"title": title, "meta": meta, "h1": h1, "hash": chash, "status": status, "issues": issues}


def add_run(db_path, pages, status="completed"):
    """Sahte tarama: crawl_runs + pages + issues yazar, main.run benzeri sonuç döndürür."""
    c = get_connection(db_path)
    c.execute("INSERT OR IGNORE INTO sites (url) VALUES (?)", (BASE,))
    site_id = c.execute("SELECT id FROM sites WHERE url = ?", (BASE,)).fetchone()[0]
    run_id = c.execute("INSERT INTO crawl_runs (site_id, status) VALUES (?, ?)", (site_id, status)).lastrowid
    for path, p in pages.items():
        pid = c.execute(
            "INSERT INTO pages (crawl_run_id, url, status_code, title, meta_description, h1_text, extra_data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, BASE + path, p["status"], p["title"], p["meta"], p["h1"],
             json.dumps({"content_hash": p["hash"]}))).lastrowid
        for code in p["issues"]:
            c.execute("INSERT INTO issues (page_id, code, category, severity, message) "
                      "VALUES (?, ?, 'on-page', 'warning', ?)", (pid, code, code))
    c.commit()
    c.close()
    return {"run_id": run_id, "status": status}


def by_type(changes):
    out = {}
    for ch in changes:
        out.setdefault(ch["type"], []).append(ch)
    return out


def main():
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    init_db(path)

    run1 = {"/": page(), "/a": page(title="A sayfası başlığı yeterince uzun"), "/b": page(), "/gidecek": page(),
            "/bozulacak": page()}
    run2 = {
        "/": page(),                                                      # değişmedi
        "/a": page(title="A sayfası YENİ başlığı yeterince uzun"),         # başlık değişti
        "/b": page(chash="h2", issues=("META_DESC_TOO_SHORT",)),          # içerik değişti + yeni sorun
        "/yeni": page(issues=("TITLE_TOO_SHORT",)),                       # yeni sayfa
        "/bozulacak": page(status=404),                                   # ulaşılamaz oldu
    }                                                                     # /gidecek silindi

    # --- saf karşılaştırma: veritabanından okuyup diff
    r1, r2 = add_run(path, run1), add_run(path, run2)
    c = get_connection(path)
    assert previous_run_id(c, r2["run_id"]) == r1["run_id"] and previous_run_id(c, r1["run_id"]) is None
    changes = diff_runs(load_run(c, r1["run_id"]), load_run(c, r2["run_id"]),
                        load_open_issue_codes(c, r1["run_id"]), load_open_issue_codes(c, r2["run_id"]))
    t = by_type(changes)
    assert [x["url"] for x in t["new_page"]] == [BASE + "/yeni"], t
    assert [x["url"] for x in t["removed_page"]] == [BASE + "/gidecek"]
    assert [(x["url"], x["field"]) for x in t["field_changed"]] == [(BASE + "/a", "title")]
    assert [x["url"] for x in t["content_changed"]] == [BASE + "/b"]
    assert [(x["url"], x["new"]) for x in t["new_issue"]] == [(BASE + "/b", "META_DESC_TOO_SHORT")]  # /yeni'nin sorunu sayılmaz
    assert [x["url"] for x in t["became_unreachable"]] == [BASE + "/bozulacak"]
    assert BASE + "/" not in {x["url"] for x in changes}
    print("OK  fark algılama: yeni/silinen sayfa, başlık, içerik, yeni sorun, ulaşılamaz oldu; değişmeyen sayfa sessiz")

    # başlıktaki yalnızca boşluk farkı değişiklik sayılmaz
    a = {"/x": {"status": 200, "title": "Aynı  Başlık", "meta_description": None, "h1_text": None, "hash": "h"}}
    b = {"/x": {"status": 200, "title": "Aynı Başlık ", "meta_description": None, "h1_text": None, "hash": "h"}}
    assert diff_runs(a, b) == []
    c.close()
    print("OK  yalnızca boşluk farkı değişiklik sayılmadı")

    # --- tam tur: sahte tarama + sahte öneri üretici
    path2 = os.path.join(tempfile.mkdtemp(), "t2.db")
    init_db(path2)
    scripted = iter([run1, run1, run2])
    suggested = []
    logs = []
    crawl = lambda url, max_pages, db_path, delay: add_run(db_path, next(scripted))
    cycle = lambda **kw: run_cycle(BASE, path2, crawl=crawl, suggest=lambda rid, urls: suggested.append((rid, urls)),
                                   log=logs.append, **kw)

    s = cycle()                                    # 1. tur: geçmiş yok
    assert s["prev_run_id"] is None and "İlk tarama" in s["note"] and not suggested
    s = cycle()                                    # 2. tur: hiçbir şey değişmedi -> model çağrılmaz
    assert s["changes"] == [] and not suggested
    s = cycle()                                    # 3. tur: değişiklikler var
    assert len(s["changes"]) == 6, s["changes"]
    assert len(suggested) == 1
    # öneri yalnızca değişen/yeni sayfalar için tetiklenir (silinen ve ulaşılamaz olan hariç)
    assert suggested[0][1] == [BASE + "/a", BASE + "/b", BASE + "/yeni"], suggested
    c = get_connection(path2)
    assert c.execute("SELECT COUNT(*) FROM monitor_changes WHERE crawl_run_id = ?", (s["run_id"],)).fetchone()[0] == 6
    print("OK  tam tur: ilk tur geçmişsiz, değişmeyen sitede model çağrılmadı, değişimde yalnızca ilgili sayfalar tetiklendi")

    # bekleyen önerisi olan sayfa atlanır
    pid = c.execute("SELECT id FROM pages WHERE url = ? ORDER BY id DESC", (BASE + "/a",)).fetchone()[0]
    c.execute("INSERT INTO applied_changes (page_id, change_type, new_value, status) VALUES (?, 'title', 'x', 'pending')", (pid,))
    c.commit()
    assert urls_with_open_suggestions(c, [BASE + "/a", BASE + "/b"]) == {BASE + "/a"}
    c.close()
    print("OK  zaten bekleyen önerisi olan sayfaya yeniden öneri tetiklenmedi")

    # --- tamamlanmayan tarama karşılaştırılmaz; --no-ai modunda model tetiklenmez
    path3 = os.path.join(tempfile.mkdtemp(), "t3.db")
    init_db(path3)
    seq = iter([add_run(path3, run1), add_run(path3, run2), {"run_id": 99, "status": "failed"}])
    called = []
    crawl3 = lambda url, max_pages, db_path, delay: next(seq)
    run_cycle(BASE, path3, crawl=crawl3, suggest=lambda *a: called.append(a), log=lambda m: None)
    s = run_cycle(BASE, path3, crawl=crawl3, suggest=lambda *a: called.append(a), log=lambda m: None, use_ai=False)
    assert called == [] and len(s["changes"]) == 6
    s = run_cycle(BASE, path3, crawl=crawl3, suggest=lambda *a: called.append(a), log=lambda m: None)
    assert "tamamlanamadı" in s["note"] and called == []
    print("OK  --no-ai modunda model çağrılmadı; başarısız tarama karşılaştırılmadı")

    # --- Ollama kapalıysa tur çökmez, değişiklikler yine kayıtlı
    path4 = os.path.join(tempfile.mkdtemp(), "t4.db")
    init_db(path4)
    seq = iter([run1, run2])

    def boom(rid, urls):
        raise RuntimeError("Ollama'ya bağlanılamadı.")
    crawl4 = lambda url, max_pages, db_path, delay: add_run(db_path, next(seq))
    run_cycle(BASE, path4, crawl=crawl4, suggest=boom, log=lambda m: None)
    s = run_cycle(BASE, path4, crawl=crawl4, suggest=boom, log=lambda m: None)
    assert "Ollama" in s["note"] and len(s["changes"]) == 6
    c = get_connection(path4)
    assert c.execute("SELECT COUNT(*) FROM monitor_changes").fetchone()[0] == 6
    c.close()
    print("OK  model hatasında tur çökmedi, değişiklikler kayıtlı kaldı")

    print("\nTüm izleme testleri geçti.")


if __name__ == "__main__":
    main()
