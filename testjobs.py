"""jobs.py testleri: gerçek tarama/model yok; kısa sahte işler."""
import time

from jobs import JobManager


def wait(job, timeout=10):
    end = time.time() + timeout
    while job.state == "running" and time.time() < end:
        time.sleep(0.02)
    assert job.state != "running", "iş bitmedi"


def main():
    jm = JobManager()

    def ok():
        print("birinci satır")
        print("ikinci satır")
        return {"adet": 2}
    job = jm.start("deneme", "Deneme", ok)
    wait(job)
    assert job.state == "done" and job.result == {"adet": 2} and job.lines == ["birinci satır", "ikinci satır"], job.snapshot()
    print("OK  iş çalıştı, print çıktısı günlüğe düştü, sonuç kaydedildi")

    def boom():
        print("başladı")
        raise ValueError("kötü şey")
    job = jm.start("deneme", "Hata", boom)
    wait(job)
    assert job.state == "failed" and "kötü şey" in job.error and job.lines == ["başladı"]
    print("OK  hata: iş 'failed' oldu, mesaj saklandı, uygulama çökmedi")

    def slow():
        for i in range(500):
            print(f"adım {i}")
            time.sleep(0.02)
    job = jm.start("deneme", "Yavaş", slow)
    try:
        jm.start("deneme", "İkinci", ok)
    except RuntimeError as e:
        assert "Yavaş" in str(e)
    else:
        raise AssertionError("ikinci iş aynı anda başladı")
    time.sleep(0.1)
    jm.cancel(job.id)
    wait(job)
    assert job.state == "cancelled" and len(job.lines) < 500
    print("OK  tek seferde bir iş; iptal bir sonraki yazımda işi durdurdu")

    job = jm.start("deneme", "Yeni", ok)   # önceki bitti: yenisi başlayabilir
    wait(job)
    assert job.state == "done" and jm.get(job.id) is job
    print("OK  iş bittikten sonra yenisi başlayabiliyor")
    print("\nTüm iş altyapısı testleri geçti.")


if __name__ == "__main__":
    main()
