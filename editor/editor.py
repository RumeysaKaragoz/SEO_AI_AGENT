"""
Düzenleme mantığı (arayüzden bağımsız). Şimdilik yalnızca okuma: canlı siteye hiçbir şey gönderilmez.
Sonraki işlevler (ortak metin, doğrulama, kaydetme) bu dosyaya eklenecek.
"""
import json

from ai import DEFAULT_MODEL, FIELD_LABELS, LIMITS, MAX_ATTEMPTS, SCHEMA, ollama_chat, validate
from review import _reason_for


def get_change(conn, change_id):
    """
    applied_changes kaydını ID ile bulur; İncele ekranının ihtiyaç duyduğu alanları döndürür.
    'current': öneri üretildiği andaki mevcut metin, 'suggestion': AI'nın önerisi.
    Kayıt yoksa ValueError.
    """
    row = conn.execute(
        """
        SELECT c.id, c.page_id, p.url, c.change_type, c.old_value, c.new_value, c.status,
               r.model, r.recommendations
        FROM applied_changes c
        JOIN pages p ON p.id = c.page_id
        LEFT JOIN ai_reports r ON r.id = c.ai_report_id
        WHERE c.id = ?
        """,
        (change_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"#{change_id} numaralı değişiklik yok")
    return {
        "id": row[0], "page_id": row[1], "url": row[2], "field": row[3],
        "current": row[4], "suggestion": row[5], "status": row[6], "model": row[7],
        "reason": _reason_for(row[8], row[3]),
    }


# ----------------------------------------------------------------------------
# Ortak metin: AI önerisi + kullanıcının isteği
# ----------------------------------------------------------------------------

EXAMPLE = (
    "Mevcut metin: Düşes Giyim\n"
    "AI önerisi: Düşes Giyim\n"
    "Kullanıcının isteği: Düşes ithal giyim olsun",
    {"value": "Düşes İthal Giyim",
     "reason": "Kullanıcının istediği 'ithal' ifadesi marka adıyla birlikte kullanıldı."},
)


def _merge_messages(field, current, suggestion, user_text, max_len, previous_error=None):
    min_len, _ = LIMITS[field]
    system = (
        "Sen Türkçe e-ticaret siteleri için çalışan deneyimli bir SEO editörüsün.\n"
        f"alan: {field}\n"
        f"Görevin, {FIELD_LABELS[field]} için tek bir son metin yazmak: kullanıcının isteğini temel al, "
        "AI önerisinin iyi yanlarını koru.\n"
        "Kurallar:\n"
        "1. Kullanıcının isteği ile AI önerisi çelişirse kullanıcının isteği geçerlidir.\n"
        "2. Yalnızca verilen metinlerdeki bilgileri kullan. Kampanya, indirim, fiyat, stok ve garanti "
        "bilgisini kullanıcı yazmadıysa ekleme.\n"
        "3. Metni normal Türkçe cümle düzeninde yaz; kelimelerin tamamını büyük harfle yazma.\n"
        f"4. Uzunluk: en az {min_len}, en fazla {max_len} karakter.\n"
        "5. Yanıtı yalnızca şu JSON biçiminde ver: {\"value\": \"son metin\", \"reason\": \"tek cümle Türkçe gerekçe\"}"
    )
    lines = [f"Mevcut metin: {current or '(yok)'}",
             f"AI önerisi: {suggestion}",
             f"Kullanıcının isteği: {user_text}"]
    if previous_error:
        lines.append(f"Önceki öneri reddedildi: {previous_error}. Kuralları dikkate alarak yeniden yaz.")
    example_user, example_answer = EXAMPLE
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": example_user},
        {"role": "assistant", "content": json.dumps(example_answer, ensure_ascii=False)},
        {"role": "user", "content": "\n".join(lines)},
    ]


def propose_merged(conn, change_id, user_text, model=DEFAULT_MODEL, brand=None, chat=ollama_chat):
    """
    Bekleyen bir değişiklik için AI önerisi ile kullanıcının isteğini birleştiren nihai metni üretir.
    Veritabanına YAZMAZ. (sonuç_sözlüğü | None, deneme_notları) döndürür.
    """
    change = get_change(conn, change_id)
    if change["status"] != "pending":
        raise ValueError(f"#{change_id} bekliyor durumunda değil (şu an: {change['status']})")
    field = change["field"]
    if field not in LIMITS:
        raise ValueError(f"'{field}' alanı için henüz öneri desteklenmiyor")
    user_text = " ".join((user_text or "").split())
    if not user_text:
        raise ValueError("Kullanıcının isteği boş olamaz")

    # Marka eki kodla eklenir (ai.propose ile aynı): modele gösterilen öneriden çıkarılır
    suffix = f" | {brand}" if brand and field == "title" else ""
    suggestion = change["suggestion"]
    if suffix and suggestion.endswith(suffix):
        suggestion = suggestion[: -len(suffix)]
    max_len = LIMITS[field][1] - len(suffix)

    page = conn.execute(
        "SELECT title, h1_text, meta_description, url FROM pages WHERE id = ?", (change["page_id"],)).fetchone()
    # Kullanıcının kendi yazdığı sayı/ifade de kaynak bilgidir (ör. "indirim" yazdıysa yasak sayılmaz)
    facts = " ".join(filter(None, [*page, change["current"], suggestion, user_text]))

    notes, previous_error = [], None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        raw = chat(model, _merge_messages(field, change["current"], suggestion, user_text,
                                          max_len, previous_error), SCHEMA)
        try:
            data = json.loads(raw)
            value, reason = data["value"], str(data.get("reason", "")).strip()
        except (ValueError, KeyError, TypeError):
            previous_error = "yanıt geçerli JSON ({\"value\", \"reason\"}) değil"
            notes.append(f"deneme {attempt}: {previous_error}")
            continue
        clean, error = validate(field, value, change["current"], facts, max_len=max_len)
        if error is None:
            return {"change_id": change_id, "field": field, "current": change["current"],
                    "ai_suggestion": change["suggestion"], "user_text": user_text,
                    "merged": clean + suffix, "reason": reason}, notes
        previous_error = error
        notes.append(f"deneme {attempt}: reddedildi — {error} (öneri: {str(value)[:80]})")
    return None, notes