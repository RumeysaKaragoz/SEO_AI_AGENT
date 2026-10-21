"""
Bekleyen AI önerilerini inceleme / onaylama / reddetme.

Fonksiyonlar arayüzden bağımsızdır: komut satırı (aşağıda) da, ileride bir arayüz de aynı
fonksiyonları çağırabilir. Model bu fonksiyonları ÇAĞIRAMAZ; onay kullanıcının eylemidir.
Not: 'applied' durumuna geçiş (siteye yazma) henüz yok; CMS bağlantısıyla eklenecek.
"""
import json

from database import DB_PATH, get_connection
from ai import LIMITS


def _reason_for(recommendations_json, field):
    try:
        for item in json.loads(recommendations_json or "[]"):
            if item.get("field") == field:
                return item.get("reason", "")
    except ValueError:
        pass
    return ""


def list_pending(conn):
    rows = conn.execute(
        """
        SELECT c.id, p.url, c.change_type, c.old_value, c.new_value, r.model, r.recommendations
        FROM applied_changes c
        JOIN pages p ON p.id = c.page_id
        LEFT JOIN ai_reports r ON r.id = c.ai_report_id
        WHERE c.status = 'pending'
        ORDER BY c.id
        """
    ).fetchall()
    return [
        {"id": r[0], "url": r[1], "field": r[2], "old": r[3], "new": r[4], "model": r[5],
         "reason": _reason_for(r[6], r[2])}
        for r in rows
    ]


def length_warning(field, value):
    if field not in LIMITS:
        return None
    low, high = LIMITS[field]
    if len(value) < low:
        return f"uyarı: {len(value)} karakter, önerilen en az {low}"
    if len(value) > high:
        return f"uyarı: {len(value)} karakter, önerilen en fazla {high}"
    return None


def _set_status(conn, change_id, new_status, new_value=None):
    row = conn.execute("SELECT status FROM applied_changes WHERE id = ?", (change_id,)).fetchone()
    if row is None:
        raise ValueError(f"#{change_id} numaralı değişiklik yok")
    if row[0] != "pending":
        raise ValueError(f"#{change_id} bekliyor durumunda değil (şu an: {row[0]})")
    if new_value is not None:
        new_value = " ".join(new_value.split())
        if not new_value:
            raise ValueError("Yeni metin boş olamaz")
        conn.execute("UPDATE applied_changes SET new_value = ? WHERE id = ?", (new_value, change_id))
    conn.execute("UPDATE applied_changes SET status = ? WHERE id = ?", (new_status, change_id))
    conn.commit()


def approve(conn, change_id, new_value=None):
    """Değişikliği onaylar. new_value verilirse kullanıcının kendi metni onaylanır."""
    _set_status(conn, change_id, "approved", new_value)


def reject(conn, change_id):
    _set_status(conn, change_id, "rejected")


def main(db_path=DB_PATH):
    conn = get_connection(db_path)
    try:
        pending = list_pending(conn)
        if not pending:
            print("Bekleyen öneri yok.")
            return
        print(f"{len(pending)} bekleyen öneri var.\n")
        for n, item in enumerate(pending, 1):
            print("=" * 70)
            print(f"[{n}/{len(pending)}] {item['url']}   (alan: {item['field']}, model: {item['model']})")
            print(f"  Eski : {item['old'] or '(yok)'}")
            print(f"  Yeni : {item['new']}   ({len(item['new'])} karakter)")
            if item["reason"]:
                print(f"  Neden: {item['reason']}")
            while True:
                choice = input("[o] onayla  [d] kendi metnimi yaz  [r] reddet  [a] atla  [q] çık > ").strip().lower()
                if choice == "o":
                    approve(conn, item["id"]); print("  -> onaylandı"); break
                if choice == "d":
                    text = input("  Yeni metin: ").strip()
                    warning = length_warning(item["field"], " ".join(text.split()))
                    if not text:
                        print("  Boş metin kabul edilmez."); continue
                    if warning:
                        print(f"  {warning}")
                        if input("  Yine de onaylansın mı? [e/h] > ").strip().lower() != "e":
                            continue
                    approve(conn, item["id"], text); print("  -> kendi metniniz onaylandı"); break
                if choice == "r":
                    reject(conn, item["id"]); print("  -> reddedildi"); break
                if choice == "a":
                    break
                if choice == "q":
                    return
    finally:
        conn.close()


if __name__ == "__main__":
    main()