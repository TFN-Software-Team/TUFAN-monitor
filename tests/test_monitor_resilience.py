"""MON-13 (madde 109): üç küçük dayanıklılık düzeltmesi testleri.

1. detect_new_boot() ölü kod temizliği -- ayrıca tests/test_csv_logger.py
   içinde test_detect_new_boot_equal_seq_is_not_a_new_boot ile kilitlendi.
2. Yeni-boot geçişinde dosya açma sırası: ÖNCE yeni dosyayı aç, YALNIZ
   başarılı olursa eskisini kapat -- açma başarısız olursa ESKİ dosyada
   veri kaybı olmadan devam edilmeli (çift exception YOK).
3. Aynı (seq, zaman_ms) ikilisi tekrar gelirse dosyaya İKİNCİ kez
   yazılmamalı (dedup), sayaç GUI'ye bildirilmeli.
"""

import queue
import threading

import monitor_core
from csv_logger import HEADER

from tests.test_monitor_serial import csv_line, drain_until, scripted_connect_factory


# --- RecentKeyDedup (saf sınıf) ----------------------------------------------


def test_recent_key_dedup_flags_repeated_key():
    dedup = monitor_core.RecentKeyDedup(max_size=200)
    assert dedup.is_duplicate((1, 1000)) is False
    assert dedup.is_duplicate((1, 1000)) is True


def test_recent_key_dedup_distinct_keys_are_not_duplicates():
    dedup = monitor_core.RecentKeyDedup(max_size=200)
    assert dedup.is_duplicate((1, 1000)) is False
    assert dedup.is_duplicate((2, 1000)) is False
    assert dedup.is_duplicate((1, 1100)) is False


def test_recent_key_dedup_evicts_oldest_beyond_max_size():
    dedup = monitor_core.RecentKeyDedup(max_size=3)
    assert dedup.is_duplicate((1, 100)) is False
    assert dedup.is_duplicate((2, 200)) is False
    assert dedup.is_duplicate((3, 300)) is False
    assert dedup.is_duplicate((4, 400)) is False  # (1,100) pencereden düşer, pencere=[2,3,4]

    # (1,100) artık pencerede değil -- tekrar "yeni" sayılır (bilinçli
    # sınırlı-pencere davranışı, madde 109/3: "son N satır"); bu ekleme
    # sırasıyla en eskiyi ((2,200)) pencereden düşürür -> pencere=[3,4,1].
    assert dedup.is_duplicate((1, 100)) is False
    assert dedup.is_duplicate((3, 300)) is True
    assert dedup.is_duplicate((4, 400)) is True
    assert dedup.is_duplicate((2, 200)) is False


# --- serial_worker entegrasyonu: dedup ---------------------------------------


def test_duplicate_seq_and_timestamp_line_is_not_written_twice(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    batch = [
        csv_line(1000, 300, 32, 780, 6283, 1),
        csv_line(1000, 300, 32, 780, 6283, 1),  # birebir tekrar (seq VE ts aynı)
        csv_line(1100, 300, 32, 780, 6283, 2),
    ]
    connect = scripted_connect_factory([batch])

    data_queue = queue.Queue()
    stop_event = threading.Event()

    worker = threading.Thread(
        target=monitor_core.serial_worker,
        args=(data_queue, stop_event),
        kwargs={"connect": connect, "reconnect_interval": 0.02},
        daemon=True,
    )
    worker.start()
    messages = drain_until(data_queue, "csv", 2)
    stop_event.set()
    worker.join(timeout=2.0)
    assert not worker.is_alive()

    dedup_messages = [m for m in messages if m["type"] == "dedup"]
    assert len(dedup_messages) == 1
    assert dedup_messages[0]["count"] == 1

    log_files = list((tmp_path / "logs").glob("telem_*.csv"))
    lines = log_files[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 + 2, "tekrar eden satir IKINCI kez yazilmamali"

    csv_messages = [m for m in messages if m["type"] == "csv"]
    assert len(csv_messages) == 2, "tekrar eden satir GUI'ye de IKINCI bir 'csv' olarak gitmemeli"


# --- serial_worker entegrasyonu: yeni-boot dosya açma sırası -----------------


def test_new_boot_file_rotation_failure_keeps_writing_to_old_file(tmp_path, monkeypatch):
    """MON-13 madde 2: yeni-boot geçişinde yeni dosya AÇILAMAZSA, eski
    dosyada (veri kaybı olmadan) kayda devam edilmeli -- worker ÇÖKMEMELİ,
    ikinci bir exception fırlatılmamalı."""
    monkeypatch.chdir(tmp_path)

    call_count = {"n": 0}
    real_open_log_file = monitor_core.open_log_file

    def flaky_open_log_file():
        call_count["n"] += 1
        if call_count["n"] == 2:
            # new-boot geçişindeki (ikinci) çağrı başarısız olsun.
            raise OSError("simulated disk full during rotation")
        return real_open_log_file()

    monkeypatch.setattr(monitor_core, "open_log_file", flaky_open_log_file)

    batch = [
        csv_line(100000, 300, 32, 780, 6283, 100),
        csv_line(1200, 300, 32, 780, 6283, 0),  # seq geriye sicradi -> yeni boot
        csv_line(1300, 300, 32, 780, 6283, 1),  # rotasyon basarisiz olsa da devam
    ]
    connect = scripted_connect_factory([batch])

    data_queue = queue.Queue()
    stop_event = threading.Event()

    worker = threading.Thread(
        target=monitor_core.serial_worker,
        args=(data_queue, stop_event),
        kwargs={"connect": connect, "reconnect_interval": 0.02},
        daemon=True,
    )
    worker.start()
    messages = drain_until(data_queue, "csv", 3)
    stop_event.set()
    worker.join(timeout=2.0)
    assert not worker.is_alive(), "rotasyon basarisiz olsa bile worker duzgunce sonlanmali (COKMEMELI)"

    assert not any(m["type"] == "worker_crashed" for m in messages), (
        "dosya rotasyonu basarisizligi worker'i COKERTMEMELI"
    )

    # new_boot GUI mesaji yine de gitmeli (AKS gercekten yeniden boot etti,
    # dosya rotasyonu basarisiz olsa bile grafik penceresi temizlenmeli).
    assert any(m["type"] == "new_boot" for m in messages)

    # Tek dosya olmali (rotasyon basarisiz oldugu icin ikinci dosya YOK) ve
    # UCU satirin da (seq=100, seq=0/yeni-boot, seq=1) TAMAMI bu TEK dosyada
    # olmali -- veri kaybi YOK.
    log_files = list((tmp_path / "logs").glob("telem_*.csv"))
    assert len(log_files) == 1, "rotasyon basarisiz oldugundan ikinci dosya ACILMAMALI"
    lines = log_files[0].read_text(encoding="utf-8").splitlines()
    assert lines[0] == HEADER
    assert len(lines) == 1 + 3, "rotasyon basarisiz olsa da HICBIR satir kaybolmamali"

    events_files = list((tmp_path / "logs").glob("events_*.log"))
    events_text = events_files[0].read_text(encoding="utf-8")
    assert "ESKI dosyada devam ediliyor" in events_text
