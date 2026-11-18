"""
Yerel LLM (Ollama) ile başlık / meta description önerileri üretir.

Model YALNIZCA öneri üretir. Her öneri kodla doğrulanır ve applied_changes tablosuna
'pending' olarak yazılır. Onay (approved) ve uygulama, review.py'deki açık kullanıcı
eylemleriyle yapılır; model durumu değiştiremez.

Kullanım:
    python ai.py --limit 5 --model qwen2.5:7b --brand "Marka Adı"
"""
import argparse
import json
import re

import requests

from context import build_context
from site_profile import EXAMPLE_KEY_FOR_FIELD, profile_for_run
from database import DB_PATH, get_connection, init_db

OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "qwen2.5:7b"
MAX_ATTEMPTS = 2

# Önerinin uyması gereken uzunluklar (rules.py'deki eşiklerle aynı)
LIMITS = {"title": (10, 55), "meta_description": (50, 160)}
FIELD_LABELS = {"title": "sayfa başlığı (<title>)", "meta_description": "meta description"}

# Hangi sorun kodu hangi alanın yeniden yazılmasını gerektirir
FIELD_FOR_CODE = {
    "TITLE_MISSING": "title",
    "TITLE_TOO_SHORT": "title",
    "TITLE_TOO_LONG": "title",
    "DUPLICATE_TITLE": "title",
    "META_DESC_MISSING": "meta_description",
    "META_DESC_TOO_SHORT": "meta_description",
    "META_DESC_TOO_LONG": "meta_description",
    "DUPLICATE_META_DESC": "meta_description",
}

# Optimizasyon adayı olmayan sayfalar (üye/sepet/ödeme): AI bunlara öneri üretmez.
# Türkçe e-ticaret sitelerinde yaygın yol başlangıçları; gerekirse genişletin.
SKIP_PATH_PREFIXES = ("hesabim", "sepet", "uye", "giris", "odeme", "kayit")

# Kaynak bilgide geçmiyorsa önerinin içinde olmaması gereken (uydurma riski taşıyan) ifadeler
PROMO_WORDS = ("indirim", "ücretsiz", "kampanya", "garanti", "bedava", "en ucuz", "fırsat", "%")

SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string"}, "reason": {"type": "string"}},
    "required": ["value", "reason"],
}


def tr_lower(text):
    """Türkçe'ye uygun küçük harf (İ -> i, I -> ı)."""
    return (text or "").replace("İ", "i").replace("I", "ı").lower()


# ----------------------------------------------------------------------------
# Ollama
# ----------------------------------------------------------------------------

def ollama_chat(model, messages, schema, timeout=300, url=OLLAMA_URL):
    """Ollama'ya sohbet isteği gönderir, modelin JSON metnini döndürür."""
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "format": schema,  # çıktıyı bu JSON şemasına zorlar
        "options": {"temperature": 0.3, "num_ctx": 4096},
    }
    try:
        response = requests.post(url, json=payload, timeout=timeout)
    except requests.ConnectionError:
        raise RuntimeError(
            "Ollama'ya bağlanılamadı. Ollama'nın çalıştığından emin olun "
            "(Windows'ta sistem tepsisindeki Ollama simgesi veya `ollama serve`)."
        )
    except requests.Timeout:
        raise RuntimeError(f"Ollama {timeout} sn içinde yanıt vermedi (model çok yavaş olabilir).")
    if response.status_code != 200:
        raise RuntimeError(
            f"Ollama hatası ({response.status_code}): {response.text[:200]}. "
            f"Model kurulu mu? `ollama pull {model}`"
        )
    return response.json()["message"]["content"]


# ----------------------------------------------------------------------------
# Doğrulama
# ----------------------------------------------------------------------------

_FOLD = str.maketrans("ıüşöçğ", "iusocg")
_SEPARATORS = " -–—|:·•,"


def _fold(text):
    """Küçük harfe çevirir ve Türkçe karakterleri ASCII karşılıklarına indirger (1'e 1, uzunluk korunur).
    Küçük modeller markayı 'DÜSES BUTIK' gibi bozuk yazabildiği için eşleştirme buna dayanıklı olmalı."""
    return tr_lower(text).translate(_FOLD)


def strip_brand(value, brand):
    """Model markayı yine de yazdıysa çıkarır ('Üst Giyim - Marka' -> 'Üst Giyim'); kod kendisi ekler."""
    folded, target = _fold(value), _fold(brand)
    while target and target in folded:
        i = folded.index(target)
        value = value[:i] + " " + value[i + len(target):]
        folded = _fold(value)
    return " ".join(value.split()).strip(_SEPARATORS)


COPY_RUN = 5  # örnek/mevcut metinle bu kadar ardışık kelime ortaksa "kopya" sayılır
_WORD = re.compile(r"\w+")


def _words(text):
    return _WORD.findall(_fold(text))


def copied_phrase(text, avoid):
    """text, avoid içindeki metinlerden biriyle COPY_RUN ardışık kelime paylaşıyorsa o diziyi (text'teki
    yazımıyla) döndürür."""
    words = _words(text)
    shown = _WORD.findall(text)
    if len(shown) != len(words):  # beklenmez; yine de güvenli tarafta kal
        shown = words
    other_grams = set()
    for other in avoid:
        other_words = _words(other)
        other_grams.update(tuple(other_words[i:i + COPY_RUN]) for i in range(len(other_words) - COPY_RUN + 1))
    for i in range(len(words) - COPY_RUN + 1):
        if tuple(words[i:i + COPY_RUN]) in other_grams:
            return " ".join(shown[i:i + COPY_RUN])
    return None


def validate(field, value, old_value, facts, suffix="", banned=(), avoid=()):
    """
    Önerilen metni kontrol eder. (temiz_metin, None) ya da (None, hata_mesajı) döndürür.
    facts : sayfada gerçekten yazan bilgiler (başlık, H1, eski meta, URL) — uydurmayı yakalamak için.
    suffix: koda ait ek (örn. ' | Marka'). Uzunluk ve "mevcutla aynı mı" kontrolü NİHAİ metne
            (öneri + ek) göre yapılır; döndürülen metin ekSİZdir.
    banned: kullanılmak istenmeyen ifadeler (site profilinden); sayfada geçse bile reddedilir.
    avoid : sahibin örnek metinleri; bunlardan birebir kopyalanan ifade reddedilir (her sayfaya aynı
            cümlenin girmesi "tekrar eden açıklama" sorununu yeniden üretir).
    """
    if not isinstance(value, str):
        return None, "öneri metin değil"
    text = " ".join(value.split()).strip(" \"'“”‘’")
    full = text + suffix
    min_len, max_len = LIMITS[field]

    if not text:
        return None, "öneri boş"
    if "<" in text or ">" in text:
        return None, "öneri HTML etiketi içeriyor"
    if len(full) < min_len:
        return None, f"çok kısa ({len(full)} karakter, en az {min_len} olmalı)"
    if len(full) > max_len:
        return None, f"çok uzun ({len(full)} karakter, en fazla {max_len} olmalı)"
    if old_value and tr_lower(" ".join(old_value.split())) == tr_lower(full):
        return None, "mevcut metinle aynı"

    letters = [c for c in text if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.6:
        return None, "çok fazla büyük harf kullanılmış; yalnızca cümle başları ve özel isimler büyük harfle başlamalı"

    folded_text = _fold(text)
    for phrase in banned:
        if phrase and _fold(phrase) in folded_text:
            return None, f"kullanılmak istenmeyen bir ifade içeriyor: '{phrase}'"

    words = _words(text)
    if words and any(words == _words(other) for other in avoid):
        return None, "örnek metinlerden birinin aynısı; bu sayfaya özgü yazılmalı"
    copied = copied_phrase(text, avoid)
    if copied:
        return None, f"örnek veya mevcut metinden birebir ifade kopyalanmış ('{copied}'); bu sayfaya özgü, kendi cümlelerinle yaz"

    fact_numbers = set(re.findall(r"\d+", facts))
    extra = [n for n in re.findall(r"\d+", text) if n not in fact_numbers]
    if extra:
        return None, f"sayfada olmayan sayı içeriyor: {', '.join(extra)}"

    lowered_facts = tr_lower(facts)
    for word in PROMO_WORDS:
        if word in tr_lower(text) and word not in lowered_facts:
            return None, f"sayfada olmayan bir vaat/ifade içeriyor: '{word}'"
    return text, None


# ----------------------------------------------------------------------------
# Prompt
# ----------------------------------------------------------------------------

def _path(url):
    return re.sub(r"^https?://[^/]+", "", url).split("?")[0].strip("/")


def is_skipped_page(url):
    first_segment = _path(url).split("/")[0]
    return first_segment in SKIP_PATH_PREFIXES


def page_type(url):
    # Türkçe e-ticaret sitelerinde yaygın yol kalıpları
    path = _path(url)
    if not path:
        return "ana sayfa"
    if path.startswith("urun/"):
        return "ürün sayfası"
    if path.startswith("kategori/"):
        return "kategori sayfası"
    return "diğer sayfa"


def content_excerpt(page, size=1200):
    """
    Sayfa metni menü gibi tekrarlayan kısımlarla başlıyor; metni H1'in geçtiği yerden itibaren
    keseriz. H1 yoksa güvenilir bir kesit çıkarılamaz, None döner.
    """
    text, h1 = page["content_text"] or "", page["h1_text"]
    if not h1:
        return None
    idx = text.find(h1)
    return text[idx: idx + size] if idx >= 0 else None


# Küçük modeller örnekten çok daha iyi öğrenir: gerçek istekten önce bir örnek konuşma verilir.
# Örnekteki "1204" sayısı gerçek sayfalarda geçmez; model örneği kopyalarsa sayı doğrulaması reddeder.
EXAMPLES = {
    "title": (
        "Sayfa türü: ürün sayfası\n"
        "URL: https://ornek.com/urun/yuksek-bel-kot-pantolon-1204-mavi-38\n"
        "Mevcut başlık: YÜKSEK BEL KOT PANTOLON 1204\n"
        "H1: YÜKSEK BEL KOT PANTOLON 1204 38 - Mavi\n"
        "Mevcut meta description: yüksek bel kot pantolon 1204\n"
        "Sayfa metninden kesit: (güvenilir kesit yok)\n"
        "Tespit edilen sorunlar: Başlık tamamen büyük harfle yazılmış.",
        {"value": "Yüksek Bel Kot Pantolon 1204 Mavi",
         "reason": "Başlık normal yazım düzenine çevrildi ve renk bilgisi eklendi."},
    ),
    "meta_description": (
        "Sayfa türü: ürün sayfası\n"
        "URL: https://ornek.com/urun/yuksek-bel-kot-pantolon-1204-mavi-38\n"
        "Mevcut başlık: YÜKSEK BEL KOT PANTOLON 1204\n"
        "H1: YÜKSEK BEL KOT PANTOLON 1204 38 - Mavi\n"
        "Mevcut meta description: yüksek bel kot pantolon 1204\n"
        "Sayfa metninden kesit: (güvenilir kesit yok)\n"
        "Tespit edilen sorunlar: Meta description çok kısa.",
        {"value": "Yüksek bel kot pantolon 1204 modelini mavi renk seçeneğiyle inceleyin ve hemen sipariş verin.",
         "reason": "Ürün adı ve renk kullanıldı, sayfada olmayan bilgi eklenmedi."},
    ),
}


def build_messages(field, page, issues, max_len, brand_suffix, previous_error=None, context_text="",
                   banned=()):
    min_len = max(1, LIMITS[field][0] - len(brand_suffix or ""))
    # Not: istemde TAMAMEN BÜYÜK HARFLİ kelime kullanılmaz; model bu tonu yanıta taşıyabiliyor.
    system = (
        "Sen Türkçe e-ticaret siteleri için çalışan deneyimli bir SEO editörüsün.\n"
        f"alan: {field}\n"
        f"Görevin, bu sayfanın {FIELD_LABELS[field]} alanı için yeni bir metin yazmak.\n"
        "Kurallar:\n"
        "1. Yalnızca sana verilen bilgileri kullan. Malzeme, özellik, kampanya, indirim, fiyat, "
        "stok ve garanti bilgisi ekleme.\n"
        "2. Metni normal Türkçe cümle düzeninde yaz: yalnızca cümle başları ve özel isimler büyük harfle "
        "başlasın. Kelimelerin tamamını büyük harfle yazma.\n"
        "3. Ürün kodlarını (ör. 1204) olduğu gibi koru. Fiyat yazma.\n"
        f"4. Uzunluk: en az {min_len}, en fazla {max_len} karakter.\n"
        "5. Yanıtı yalnızca şu JSON biçiminde ver: {\"value\": \"yeni metin\", \"reason\": \"tek cümle Türkçe gerekçe\"}"
    )
    rule_no = 6
    if brand_suffix:
        system += f"\n{rule_no}. Marka adını ekleme, kod kendisi ekleyecek."
        rule_no += 1
    if banned:
        system += f"\n{rule_no}. Şu ifadeleri hiçbir biçimde kullanma: {', '.join(banned)}."

    lines = [
        f"Sayfa türü: {page_type(page['url'])}",
        f"URL: {page['url']}",
        f"Mevcut başlık: {page['title'] or '(yok)'}",
        f"H1: {page['h1_text'] or '(yok)'}",
        f"Mevcut meta description: {page['meta_description'] or '(yok)'}",
    ]
    excerpt = content_excerpt(page)
    lines.append(f"Sayfa metninden kesit: {excerpt}" if excerpt else "Sayfa metninden kesit: (güvenilir kesit yok)")
    if context_text:
        lines.append(context_text)
    lines.append("Tespit edilen sorunlar: " + " | ".join(i["message"] for i in issues))
    if previous_error:
        lines.append(f"Önceki öneri reddedildi: {previous_error}. Kuralları dikkate alarak yeniden yaz.")

    example_user, example_answer = EXAMPLES[field]
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": example_user},
        {"role": "assistant", "content": json.dumps(example_answer, ensure_ascii=False)},
        {"role": "user", "content": "\n".join(lines)},
    ]


# ----------------------------------------------------------------------------
# Öneri üretimi
# ----------------------------------------------------------------------------

def propose(field, page, issues, chat, model, brand=None, context=None, banned=(), avoid=()):
    """
    Bir alan için öneri üretir. (öneri_sözlüğü | None, deneme_notları) döndürür.
    context: context.build_context sonucu ({"text", "extra_facts"}); yoksa bağlamsız çalışır.
    """
    suffix = f" | {brand}" if brand and field == "title" else ""
    max_len = LIMITS[field][1] - len(suffix)
    old_value = page[field]
    context = context or {"text": "", "extra_facts": ""}
    # Sitenin mevcut başlıklarındaki marka eki bağlamda kalırsa model onu taklit eder; marka kodla eklenir
    context_text = context["text"].replace(suffix, "") if suffix else context["text"]
    facts = " ".join(filter(None, [page["title"], page["h1_text"], page["meta_description"], page["url"],
                                   context["extra_facts"]]))

    # Yalnızca sahibin örnek metinleri kopya kontrolüne girer. MEVCUT metin girmez: kısa bir açıklamayı
    # genişletirken eski kelimeleri korumak doğaldır. Örnek başlıklardaki marka eki kodla eklendiği için atılır.
    avoid_texts = [strip_brand(a, brand) if suffix else a for a in avoid]

    notes, previous_error = [], None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        messages = build_messages(field, page, issues, max_len, suffix, previous_error, context_text, banned)
        raw = chat(model, messages, SCHEMA)
        try:
            data = json.loads(raw)
            value, reason = data["value"], str(data.get("reason", "")).strip()
        except (ValueError, KeyError, TypeError):
            previous_error = "yanıt geçerli JSON ({\"value\", \"reason\"}) değil"
            notes.append(f"deneme {attempt}: {previous_error}")
            continue

        if suffix and isinstance(value, str):
            value = strip_brand(value, brand)  # marka kodla eklenir; model yazdıysa tekrar etmesin
        clean, error = validate(field, value, old_value, facts, suffix=suffix, banned=banned, avoid=avoid_texts)
        if error is None:
            return {"field": field, "old": old_value, "new": clean + suffix, "reason": reason}, notes
        previous_error = error
        notes.append(f"deneme {attempt}: reddedildi — {error} (öneri: {str(value)[:80]})")
    return None, notes


def _latest_run_id(conn):
    row = conn.execute(
        "SELECT id FROM crawl_runs WHERE status = 'completed' ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        raise RuntimeError("Tamamlanmış bir tarama bulunamadı. Önce main.py ile site taranmalı.")
    return row[0]


def generate_suggestions(db_path=DB_PATH, run_id=None, limit=5, model=DEFAULT_MODEL,
                         brand=None, chat=ollama_chat, discard_pending=False, discard_rejected=False, only_urls=None):
    """discard_pending / discard_rejected: bu taramadaki BEKLEYEN ve/veya REDDEDİLMİŞ önerileri silip
    yeniden üretir. Reddedilmiş kayıt silinmedikçe o alan yeniden üretilmez. Onaylanmış ve
    uygulanmış kayıtlara hiçbir zaman dokunulmaz."""
    init_db(db_path)
    conn = get_connection(db_path)
    conn.row_factory = None
    try:
        run_id = run_id or _latest_run_id(conn)
        statuses = [st for st, on in (("pending", discard_pending), ("rejected", discard_rejected)) if on]
        if statuses:
            deleted = conn.execute(
                f"DELETE FROM applied_changes WHERE status IN ({','.join('?' * len(statuses))}) AND page_id IN "
                "(SELECT id FROM pages WHERE crawl_run_id = ?)", (*statuses, run_id)).rowcount
            conn.commit()
            print(f"{deleted} kayıt silindi ({', '.join(statuses)}), yeniden üretilecek.")
        profile = profile_for_run(conn, run_id)
        brand = brand or profile["brand_name"] or None  # --brand verilmediyse site profilindeki marka
        banned = profile["banned_phrases"]
        placeholders = ",".join("?" * len(FIELD_FOR_CODE))
        rows = conn.execute(
            f"""
            SELECT p.id, p.url, p.title, p.meta_description, p.h1_text, p.content_text,
                   i.id, i.code, i.message
            FROM issues i JOIN pages p ON p.id = i.page_id
            WHERE p.crawl_run_id = ? AND i.status = 'open' AND i.code IN ({placeholders})
            ORDER BY p.id, i.id
            """,
            (run_id, *FIELD_FOR_CODE),
        ).fetchall()

        pages = {}
        for pid, url, title, meta, h1, text, issue_id, code, message in rows:
            if is_skipped_page(url):
                continue
            # "Aynı başlık/açıklama" bulgusunda yeniden yazılacak olan kopyadır, ana sayfa değil
            if code.startswith("DUPLICATE_") and page_type(url) == "ana sayfa":
                continue
            entry = pages.setdefault(pid, {
                "page": {"id": pid, "url": url, "title": title, "meta_description": meta,
                         "h1_text": h1, "content_text": text},
                "fields": {},
            })
            entry["fields"].setdefault(FIELD_FOR_CODE[code], []).append(
                {"id": issue_id, "code": code, "message": message})

        summary = {"run_id": run_id, "pages": 0, "proposed": 0, "failed": 0}
        for entry in pages.values():
            if summary["pages"] >= limit:
                break
            page = entry["page"]
            if only_urls is not None and page["url"] not in only_urls:
                continue
            todo = {
                field: issues for field, issues in entry["fields"].items()
                if conn.execute("SELECT 1 FROM applied_changes WHERE page_id = ? AND change_type = ?",
                                (page["id"], field)).fetchone() is None
            }
            if not todo:
                continue

            summary["pages"] += 1
            print(f"[{summary['pages']}/{limit}] {page['url']}")
            accepted, report_lines = [], []
            for field, issues in todo.items():
                ctx = build_context(conn, run_id, page, field, [i["code"] for i in issues], FIELD_FOR_CODE,
                                    profile)
                proposal, notes = propose(field, page, issues, chat, model, brand, ctx, banned,
                                          avoid=profile[EXAMPLE_KEY_FOR_FIELD[field]])
                report_lines.extend(f"[{field}] {n}" for n in notes)
                if proposal is None:
                    summary["failed"] += 1
                    report_lines.append(f"[{field}] geçerli öneri üretilemedi")
                    print(f"    {field}: öneri üretilemedi")
                else:
                    summary["proposed"] += 1
                    proposal["issue_id"] = issues[0]["id"]
                    proposal["issue_codes"] = [i["code"] for i in issues]
                    accepted.append(proposal)
                    report_lines.append(f"[{field}] öneri: {proposal['new']}")
                    print(f"    {field}: {proposal['new']}")

            cursor = conn.execute(
                "INSERT INTO ai_reports (crawl_run_id, page_id, model, recommendations, report_content) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, page["id"], model,
                 json.dumps(accepted, ensure_ascii=False), "\n".join(report_lines)),
            )
            report_id = cursor.lastrowid
            for p in accepted:
                conn.execute(
                    "INSERT INTO applied_changes "
                    "(page_id, issue_id, ai_report_id, change_type, old_value, new_value, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                    (page["id"], p["issue_id"], report_id, p["field"], p["old"], p["new"]),
                )
            conn.commit()  # her sayfadan sonra kaydet: yarıda kesilirse iş kaybolmaz
        return summary
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Yerel AI ile başlık/meta description önerileri üretir.")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--run-id", type=int, default=None, help="Varsayılan: son tamamlanan tarama")
    parser.add_argument("--limit", type=int, default=5, help="En fazla kaç sayfa için öneri üretilsin")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--brand", default=None, help="Başlıkların sonuna ' | MARKA' eklenir (varsayılan: site profilindeki marka adı)")
    parser.add_argument("--discard-pending", action="store_true",
                        help="Bu taramadaki bekleyen önerileri silip yeniden üretir (onaylılara dokunmaz)")
    parser.add_argument("--discard-rejected", action="store_true",
                        help="Bu taramadaki REDDEDİLMİŞ önerileri de silip yeniden üretir")
    args = parser.parse_args()

    summary = generate_suggestions(args.db, args.run_id, args.limit, args.model, args.brand,
                                   discard_pending=args.discard_pending,
                                   discard_rejected=args.discard_rejected)
    print(f"\nTarama #{summary['run_id']}: {summary['pages']} sayfa işlendi, "
          f"{summary['proposed']} öneri kaydedildi (bekliyor), {summary['failed']} öneri üretilemedi.")
    if summary["proposed"]:
        print("Önerileri incelemek için: python review.py")


if __name__ == "__main__":
    main()