"""
Onaylanan değişiklikleri siteye uygulama katmanı. Model buraya ERİŞEMEZ; yalnızca kullanıcı eylemiyle çalışır.

Şimdilik siteye hiçbir istek gönderilmez. İki yol var:
  1. Elle: export_csv() onaylananları CSV yapar; sen sitenin yönetim panelinde uygularsın,
     sonra mark_applied() ile "uygulandı" diye işaretlersin.
  2. Otomatik (ileride): sitenin API'sine göre bir Publisher yazılır, apply_change() onu çağırır.
     Başarısızlıkta kayıt 'failed' olur; yeniden denenebilir.

Durum geçişleri: approved -> applied | failed | pending, failed -> applied | failed | pending.
Not: apply_change içinde site güncellendikten hemen sonra süreç ölürse kayıt 'approved' kalır;
yeniden uygulamak zararsızdır (alan aynı değere ayarlanır).
Geri alma ('reverted') henüz yok; eski değer old_value'da saklı.

Kullanım:
    python apply_changes.py        ->  onaylanan_degisiklikler.csv dosyasını yazar
"""
import csv
import io

from ai import FIELD_LABELS
from database import DB_PATH, get_connection

CSV_PATH = "onaylanan_degisiklikler.csv"


class PublishError(Exception):
    """Siteye yazma başarısız oldu (mesaj kullanıcıya gösterilir)."""


class Publisher:
    """Sitenin bir değişikliği kabul etme yolu. change: {"id", "url", "field", "old", "new"}."""

    def apply(self, change):
        raise NotImplementedError("Publisher.apply yazılmalı; başarısızlıkta PublishError fırlatın")


def _changes(conn, where, params=()):
    rows = conn.execute(
        f"""
        SELECT c.id, p.url, c.change_type, c.old_value, c.new_value, c.status
        FROM applied_changes c JOIN pages p ON p.id = c.page_id
        WHERE {where} ORDER BY c.id
        """, params).fetchall()
    return [{"id": r[0], "url": r[1], "field": r[2], "old": r[3], "new": r[4], "status": r[5]} for r in rows]


def list_unapplied(conn):
    """Henüz uygulanmamış onaylı değişiklikler: approved + (yeniden denenebilecek) failed."""
    return _changes(conn, "c.status IN ('approved', 'failed')")


def _transition(conn, change_id, allowed, new_status):
    rows = _changes(conn, "c.id = ?", (change_id,))
    if not rows:
        raise ValueError(f"#{change_id} numaralı değişiklik yok")
    if rows[0]["status"] not in allowed:
        raise ValueError(f"#{change_id} {' veya '.join(allowed)} durumunda değil (şu an: {rows[0]['status']})")
    conn.execute(
        "UPDATE applied_changes SET status = ?, "
        "applied_at = CASE WHEN ? = 'applied' THEN CURRENT_TIMESTAMP ELSE applied_at END WHERE id = ?",
        (new_status, new_status, change_id))
    conn.commit()
    return rows[0]


def mark_applied(conn, change_id):
    """Elle uygulanan değişikliği 'applied' yapar (siteye hiçbir şey göndermez)."""
    _transition(conn, change_id, ("approved", "failed"), "applied")


def reopen(conn, change_id):
    """Onaylı (veya başarısız) değişikliği yeniden düzenlemek için 'pending'e geri alır. Siteye hiçbir şey göndermez;
    'applied' kayıtlar geri alınamaz (site zaten değişti)."""
    _transition(conn, change_id, ("approved", "failed"), "pending")


def apply_change(conn, change_id, publisher):
    """Onaylı (veya daha önce başarısız) değişikliği publisher ile uygular. {"id", "status", "error"} döndürür."""
    rows = _changes(conn, "c.id = ?", (change_id,))
    if not rows:
        raise ValueError(f"#{change_id} numaralı değişiklik yok")
    change = rows[0]
    if change["status"] not in ("approved", "failed"):
        raise ValueError(f"#{change_id} approved veya failed durumunda değil (şu an: {change['status']})")
    try:
        publisher.apply(change)
    except PublishError as e:
        _transition(conn, change_id, ("approved", "failed"), "failed")
        return {"id": change_id, "status": "failed", "error": str(e)}
    _transition(conn, change_id, ("approved", "failed"), "applied")
    return {"id": change_id, "status": "applied", "error": None}


def _safe(value):
    """Excel/Sheets'te formül olarak çalışmasın (= + - @ ile başlayan hücreler)."""
    value = value or ""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def export_csv(changes):
    """Türkçe Excel için ';' ayraçlı CSV metni (dosyaya/yanıta yazarken başına BOM eklenir)."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["No", "Sayfa", "Alan", "Eski metin", "Yeni metin", "Yeni karakter"])
    for c in changes:
        writer.writerow([c["id"], c["url"], FIELD_LABELS.get(c["field"], c["field"]),
                         _safe(c["old"]), _safe(c["new"]), len(c["new"] or "")])
    return out.getvalue()


def main(db_path=DB_PATH, path=CSV_PATH):
    conn = get_connection(db_path)
    try:
        changes = list_unapplied(conn)
    finally:
        conn.close()
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(export_csv(changes))
    print(f"{len(changes)} uygulanmamış değişiklik yazıldı: {path}")


if __name__ == "__main__":
    main()
