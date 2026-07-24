"""Seri port dayaniklilik testleri: fake-serial ile kopma/yeniden baglanma
ve replay (eski ts, artan seq) senaryolari.

Gercek pyserial/donanim kullanilmaz; monitor_core.serial_worker'a `connect`
parametresiyle sahte bir baglanti fabrikasi enjekte edilir.
"""

import os
import queue
import threading
import time

import serial

import config
import monitor_core
from csv_logger import HEADER


class ScriptedSerial:
    """Verilen satirlari sirayla dondurur; tukendiginde SerialException
    firlatarak kablonun cekildigi bir port kopmasini simule eder."""

    def __init__(self, lines):
        self._lines = list(lines)
        self.closed = False

    def readline(self):
        if not self._lines:
            raise serial.SerialException("simulated cable pull")
        return self._lines.pop(0)

    def close(self):
        self.closed = True


def scripted_connect_factory(batches):
    """Her cagrida bir sonraki batch icin ScriptedSerial dondurur; batches
    tukendiginde (port hic gelmiyor) SerialException firlatir."""
    state = {"i": 0}

    def connect():
        i = state["i"]
        if i >= len(batches):
            raise serial.SerialException("port unavailable")
        state["i"] += 1
        return ScriptedSerial(batches[i])

    return connect


def csv_line(ts, speed_x10, temp, pack_dv, soc, seq):
    return f"CSV,{ts},{speed_x10},{temp},{pack_dv},{soc},{seq}\n".encode("utf-8")


def drain_until(data_queue, msg_type, count, timeout=5.0):
    """count adet msg_type mesaji gorulene kadar kuyruktan okur, gorulen
    tum mesajlari (ilgisiz tipler dahil) dondurur."""
    collected = []
    deadline = time.monotonic() + timeout
    seen = 0
    while seen < count:
        if time.monotonic() > deadline:
            raise AssertionError(f"'{msg_type}' mesaji {count} kez gorulmedi, sadece {seen} kez")
        try:
            msg = data_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        collected.append(msg)
        if msg["type"] == msg_type:
            seen += 1
    return collected


def test_serial_disconnect_reconnect_same_file_no_row_loss(tmp_path, monkeypatch):
    """9.2.g/h: port kopunca uygulama cikmaz, dosya acik kalir, port donunce
    AYNI dosyada devam eder; replay paketleri (eski ts, artan seq) yeni
    dosya actirmaz (detect_new_boot yalniz seq'e bakar)."""
    monkeypatch.chdir(tmp_path)

    batch1 = [
        csv_line(50000, 300, 32, 780, 6283, 100),
        csv_line(50100, 300, 32, 780, 6283, 101),
    ]
    # Kesinti sonrasi AKS tamponundaki paketler eski ts ile ama ARTAN seq
    # ile tekrar gonderilir (replay); son paket canli akisa yakalanmis ts.
    batch2 = [
        csv_line(49500, 300, 32, 780, 6283, 102),  # ts eski (replay)
        csv_line(49600, 300, 32, 780, 6283, 103),  # ts eski (replay)
        csv_line(50200, 300, 32, 780, 6283, 104),  # ts canli
    ]
    connect = scripted_connect_factory([batch1, batch2])

    data_queue = queue.Queue()
    stop_event = threading.Event()

    worker = threading.Thread(
        target=monitor_core.serial_worker,
        args=(data_queue, stop_event),
        kwargs={"connect": connect, "reconnect_interval": 0.05},
        daemon=True,
    )
    worker.start()

    messages = drain_until(data_queue, "csv", 5)
    stop_event.set()
    worker.join(timeout=2.0)
    assert not worker.is_alive()

    filenames = {m["name"] for m in messages if m["type"] == "filename"}
    assert len(filenames) == 1, "port kopup gelirken YENI dosya acilmamali"

    assert any(m["type"] == "port_down" for m in messages), "port kopmasi GUI'ye bildirilmeli"
    assert any(m["type"] == "port_up" for m in messages), "port donusu GUI'ye bildirilmeli"

    csv_messages = [m for m in messages if m["type"] == "csv"]
    assert len(csv_messages) == 5

    log_files = list((tmp_path / "logs").glob("telem_*.csv"))
    assert len(log_files) == 1, "tek dosya olmali (yeni boot tespit edilmedi)"

    lines = log_files[0].read_text(encoding="utf-8").splitlines()
    assert lines[0] == HEADER
    assert len(lines) == 1 + 5, "sifir satir kaybi: 5 kaydin tamami ayni dosyada olmali"

    timestamps = [line.split(";")[0] for line in lines[1:]]
    # ts deseni: kesintiden once ilerliyor, replay sirasinda geriye dusuyor,
    # sonra canli akisa yakalanip tekrar ilerliyor.
    assert timestamps == ["50000", "50100", "49500", "49600", "50200"]

    events_files = list((tmp_path / "logs").glob("events_*.log"))
    assert len(events_files) == 1
    events_content = events_files[0].read_text(encoding="utf-8")
    assert "SERI PORT KOPTU" in events_content
    assert "SERI PORT BAGLANDI" in events_content


def test_serial_never_available_does_not_crash_and_keeps_retrying(tmp_path, monkeypatch):
    """Port hic acilmiyorsa (ornegin yanlis COM), uygulama cikmaz; port_down
    mesajlari GUI'ye akmaya devam eder, dosya acik/bos kalir."""
    monkeypatch.chdir(tmp_path)

    connect = scripted_connect_factory([])  # ilk denemeden itibaren hep basarisiz

    data_queue = queue.Queue()
    stop_event = threading.Event()

    worker = threading.Thread(
        target=monitor_core.serial_worker,
        args=(data_queue, stop_event),
        kwargs={"connect": connect, "reconnect_interval": 0.02},
        daemon=True,
    )
    worker.start()

    messages = drain_until(data_queue, "port_down", 3)
    stop_event.set()
    worker.join(timeout=2.0)
    assert not worker.is_alive(), "port hic gelmese bile worker thread duzgunce sonlanmali"

    assert all(m["type"] in ("filename", "port_down") for m in messages)

    log_files = list((tmp_path / "logs").glob("telem_*.csv"))
    assert len(log_files) == 1
    assert log_files[0].read_text(encoding="utf-8").splitlines() == [HEADER]


def test_replay_ts_regression_logs_tag_without_touching_csv_schema(tmp_path, monkeypatch):
    """9.2.e: offline-buffer drenaji sirasinda ts_ms geriye gittiginde
    (seq artmaya devam ederken) events log'a zaman damgali "REPLAY?" notu
    dusulmeli; CSV dosyasi/semasi (5 kolon, tek dosya) bundan ETKILENMEMELI."""
    monkeypatch.chdir(tmp_path)

    batch = [
        csv_line(100000, 300, 32, 780, 6283, 100),
        csv_line(41000, 300, 32, 780, 6283, 101),   # replay: ts geriye gitti
        csv_line(100100, 300, 32, 780, 6283, 102),  # canli
        csv_line(42000, 300, 32, 780, 6283, 103),   # replay: ts geriye gitti
        csv_line(100200, 300, 32, 780, 6283, 104),  # canli
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

    messages = drain_until(data_queue, "csv", 5)
    stop_event.set()
    worker.join(timeout=2.0)
    assert not worker.is_alive()

    log_files = list((tmp_path / "logs").glob("telem_*.csv"))
    assert len(log_files) == 1, "REPLAY? notu yeni dosya actirmamali"
    lines = log_files[0].read_text(encoding="utf-8").splitlines()
    assert lines[0] == HEADER
    assert len(lines) == 1 + 5
    for line in lines[1:]:
        assert len(line.split(";")) == 5, "5 kolonlu sema korunmali"

    events_files = list((tmp_path / "logs").glob("events_*.log"))
    events_text = events_files[0].read_text(encoding="utf-8")
    assert events_text.count("REPLAY?") == 2, "yalniz gercekten geriye giden 2 ts icin not dusulmeli"
    assert "YENI BOOT" not in events_text, "bu senaryo gercek boot degil, replay"


def test_link_down_up_lines_not_written_to_csv(tmp_path, monkeypatch):
    """LINK,DOWN / LINK,UP satirlari 5 kolonlu CSV semasini bozmamali;
    sadece GUI/events log'a yansimali."""
    monkeypatch.chdir(tmp_path)

    batch = [
        csv_line(1000, 100, 25, 750, 9000, 1),
        b"LINK,DOWN\n",
        b"LINK,UP\n",
        csv_line(1100, 100, 25, 750, 9000, 2),
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

    assert any(m["type"] == "link_down" for m in messages)
    assert any(m["type"] == "link_up" for m in messages)

    log_files = list((tmp_path / "logs").glob("telem_*.csv"))
    lines = log_files[0].read_text(encoding="utf-8").splitlines()
    assert lines[0] == HEADER
    assert len(lines) == 1 + 2, "LINK satirlari CSV'ye satir olarak eklenmemeli"
    for line in lines[1:]:
        assert len(line.split(";")) == 5

    events_content = (tmp_path / "logs").glob("events_*.log")
    events_text = next(iter(events_content)).read_text(encoding="utf-8")
    assert "LINK,DOWN" in events_text
    assert "LINK,UP" in events_text


def test_simulate_mode_log_filenames_carry_sim_suffix(tmp_path, monkeypatch):
    """config.SERIAL_PORT == "SIMULATE" iken hem telem hem events dosyasi
    _SIM ile bitmeli — sahte veri gercek kayitla asla karismamali."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "SERIAL_PORT", "SIMULATE")

    batch = [csv_line(1000, 100, 25, 750, 9000, 1)]
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

    drain_until(data_queue, "csv", 1)
    stop_event.set()
    worker.join(timeout=2.0)
    assert not worker.is_alive()

    telem_files = list((tmp_path / "logs").glob("telem_*.csv"))
    events_files = list((tmp_path / "logs").glob("events_*.log"))
    assert len(telem_files) == 1
    assert len(events_files) == 1
    assert telem_files[0].name.endswith("_SIM.csv")
    assert events_files[0].name.endswith("_SIM.log")


def test_real_port_mode_log_filenames_have_no_sim_suffix(tmp_path, monkeypatch):
    """Gercek port modunda davranis birebir eskisi gibi olmali: dosya
    adlarinda _SIM eki OLMAMALI."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "SERIAL_PORT", "COM99")

    batch = [csv_line(1000, 100, 25, 750, 9000, 1)]
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

    drain_until(data_queue, "csv", 1)
    stop_event.set()
    worker.join(timeout=2.0)
    assert not worker.is_alive()

    telem_files = list((tmp_path / "logs").glob("telem_*.csv"))
    events_files = list((tmp_path / "logs").glob("events_*.log"))
    assert len(telem_files) == 1
    assert len(events_files) == 1
    assert not telem_files[0].name.endswith("_SIM.csv")
    assert not events_files[0].name.endswith("_SIM.log")


def test_csv_messages_carry_ts_sec_from_packet_timestamp(tmp_path, monkeypatch):
    """R2: 'csv' mesajı artık grafiğin sıralı-ekleme için kullandığı
    'ts_sec' alanını (paketin KENDİ ts_ms'i / 1000) taşımalı — varış
    zamanından (wall-clock 'ts') BAĞIMSIZ, replay/canlı fark etmeksizin."""
    monkeypatch.chdir(tmp_path)

    batch = [
        csv_line(50000, 300, 32, 780, 6283, 100),
        csv_line(49500, 300, 32, 780, 6283, 101),  # replay: eski ts
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

    csv_messages = [m for m in messages if m["type"] == "csv"]
    assert [m["ts_sec"] for m in csv_messages] == [50.0, 49.5], (
        "ts_sec, paketin KENDİ ts_ms'inden (varış sırasından DEĞİL) "
        "türetilmeli — replay paketi burada ikinci sırada gelse de ts_sec "
        "geriye gitmeli (49.5 < 50.0)"
    )


def test_new_boot_message_emitted_on_real_boot_not_on_replay(tmp_path, monkeypatch):
    """R2: GUI grafiğinin ts_ms tabanlı penceresini temizleyebilmesi için,
    'new_boot' mesajı YALNIZ gerçek yeni-boot tespitinde (seq geriye
    sıçradığında) yayınlanmalı — bir replay (ts geriye, seq ileri) BUNU
    TETİKLEMEMELİ (aksi halde grafik replay sırasında yanlışlıkla temizlenir)."""
    monkeypatch.chdir(tmp_path)

    batch = [
        csv_line(100000, 300, 32, 780, 6283, 100),
        csv_line(41000, 300, 32, 780, 6283, 101),   # replay: ts geriye, seq ileri
        csv_line(1200, 300, 32, 780, 6283, 0),        # gercek yeni boot: seq geriye
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
    assert not worker.is_alive()

    new_boot_count = sum(1 for m in messages if m["type"] == "new_boot")
    assert new_boot_count == 1, (
        f"new_boot tam olarak 1 kez (gercek boot'ta) yayinlanmali, "
        f"gorulen: {new_boot_count}"
    )


def test_simulate_mode_new_boot_second_file_also_carries_sim_suffix(tmp_path, monkeypatch):
    """Yeni-boot tespitinde serial_worker dosyayi kapatip open_log_file ile
    yeniden aciyor; SIMULATE modunda ikinci dosya da _SIM eki tasimali."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "SERIAL_PORT", "SIMULATE")

    batch = [
        csv_line(100000, 300, 32, 780, 6283, 100),
        csv_line(1200, 300, 32, 780, 6283, 0),  # seq geriye siçradi -> yeni boot
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

    filenames = [m["name"] for m in messages if m["type"] == "filename"]
    assert len(filenames) == 2, "yeni boot ikinci bir dosya actirmali (open_log_file iki kez cagrilmali)"
    assert filenames[0] != filenames[1], (
        "9.2.g: ayni saniyede uretilen ikinci dosya adi ilkiyle CAKISMAMALI "
        "(cakisirsa 'w' ile acilan ikinci dosya ilkini sessizce sifirlar)"
    )
    for name in filenames:
        # Ayni saniyede acilan ikinci dosya sayac eki alabilir
        # (telem_..._SIM_2.csv) -- _SIM'in varligi ve konumu (sayacdan
        # ONCE) asil kontrol edilen sey, tam sonek degil.
        assert "_SIM" in name and name.endswith(".csv"), "yeni boot sonrasi acilan dosya da _SIM eki tasimali"

    # Mesaj adlari degil, bizzat diskteki dosyalari dogrula: iki AYRI dosya
    # gercekten var olmali, ilk boot'un dosyasi ikinci acilis tarafindan
    # sessizce sifirlanmamis (truncate edilmemis) olmali.
    telem_files = sorted((tmp_path / "logs").glob("telem_*_SIM*.csv"))
    assert len(telem_files) == 2, "iki ayri dosya diskte olmali (SIM eki + gerekirse sayac ile)"
    assert {f.name for f in telem_files} == {os.path.basename(n) for n in filenames}
    first_file = tmp_path / filenames[0]
    first_lines = first_file.read_text(encoding="utf-8").splitlines()
    assert first_lines[0] == HEADER
    assert len(first_lines) == 1 + 1, (
        "ilk boot dosyasi, yeni boot tespit edilmeden once yazilan seq=100 "
        "kaydini icermeli; ikinci acilis tarafindan sessizce sifirlanmamis "
        "(truncate edilmemis) olmali"
    )


# --- MON-03 (madde 50/69): bozuk/aralık-dışı satırlar sessizce atılmamalı ----


def test_invalid_line_is_rejected_counted_and_logged_but_not_written_to_csv(tmp_path, monkeypatch):
    """Sayısal olmayan/eksik alanlı bir CSV satırı sessizce atılmamalı --
    sayılmalı (queue'ya 'reject' mesajı) ve HAM haliyle events log'a
    düşülmeli; jüri dosyasına YAZILMAMALI."""
    monkeypatch.chdir(tmp_path)

    batch = [
        b"CSV,1000,300,32,780\r\n",  # eksik alan -> parse_hatasi
        csv_line(1100, 300, 32, 780, 6283, 1),  # gecerli
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
    messages = drain_until(data_queue, "csv", 1)
    stop_event.set()
    worker.join(timeout=2.0)
    assert not worker.is_alive()

    reject_messages = [m for m in messages if m["type"] == "reject"]
    assert len(reject_messages) == 1
    assert reject_messages[0]["reason"] == "parse_hatasi"

    log_files = list((tmp_path / "logs").glob("telem_*.csv"))
    lines = log_files[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 + 1, "bozuk satir dosyaya YAZILMAMALI, yalniz gecerli 1 satir olmali"

    events_files = list((tmp_path / "logs").glob("events_*.log"))
    events_text = events_files[0].read_text(encoding="utf-8")
    assert "REDDEDILEN SATIR (parse_hatasi)" in events_text
    assert "CSV,1000,300,32,780" in events_text, "ham satir events log'a dusmeli"


def test_out_of_range_line_is_rejected_with_aralik_hatasi_reason(tmp_path, monkeypatch):
    """Sayısal ama aralık dışı bir alan (örneğin aşırı hız) TÜM satırı
    reddetmeli -- 'aralik_hatasi' olarak sayılmalı/loglanmalı."""
    monkeypatch.chdir(tmp_path)

    too_fast_x10 = int((config.MAX_SPEED_KMH + 50) * 10)
    batch = [
        csv_line(1000, too_fast_x10, 32, 780, 6283, 1),  # hiz aralik disi
        csv_line(1100, 300, 32, 780, 6283, 2),  # gecerli
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
    messages = drain_until(data_queue, "csv", 1)
    stop_event.set()
    worker.join(timeout=2.0)
    assert not worker.is_alive()

    reject_messages = [m for m in messages if m["type"] == "reject"]
    assert len(reject_messages) == 1
    assert reject_messages[0]["reason"] == "aralik_hatasi"

    log_files = list((tmp_path / "logs").glob("telem_*.csv"))
    lines = log_files[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 + 1

    events_files = list((tmp_path / "logs").glob("events_*.log"))
    events_text = events_files[0].read_text(encoding="utf-8")
    assert "REDDEDILEN SATIR (aralik_hatasi)" in events_text


def test_rejected_line_raw_logging_is_rate_limited_per_second(tmp_path, monkeypatch):
    """Flood'u önlemek için aynı sebep için saniyede en fazla
    REJECT_LOG_MAX_PER_SEC ham satır loglanır; aşırı sayıda hata GUI
    sayacında hâlâ görülür ama events log'unu taşırmaz."""
    monkeypatch.chdir(tmp_path)

    n_bad = monitor_core.REJECT_LOG_MAX_PER_SEC + 3
    batch = [b"CSV,bad,line,here\r\n" for _ in range(n_bad)]
    batch.append(csv_line(1000, 300, 32, 780, 6283, 1))
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
    messages = drain_until(data_queue, "csv", 1)
    stop_event.set()
    worker.join(timeout=2.0)
    assert not worker.is_alive()

    reject_messages = [m for m in messages if m["type"] == "reject"]
    assert len(reject_messages) == n_bad, "sayac RATE LIMITE BAKMAKSIZIN her reddi saymali"

    events_files = list((tmp_path / "logs").glob("events_*.log"))
    events_text = events_files[0].read_text(encoding="utf-8")
    logged_raw_count = events_text.count("REDDEDILEN SATIR (parse_hatasi): CSV,bad,line,here")
    assert logged_raw_count <= monitor_core.REJECT_LOG_MAX_PER_SEC, (
        "diske yazilan HAM satir sayisi saniyede sinirlandirilmali"
    )


def test_csv_message_carries_raw_timestamp_ms_field(tmp_path, monkeypatch):
    """MON-04/06 (madde 48, 67/68): ZAMAN kartı ve maks. ardışık zaman farkı
    göstergesi ham (tam sayı) zaman_ms değerine ihtiyaç duyar -- 'csv'
    mesajı bunu doğrudan taşımalı (ts_sec zaten bunun /1000'i olsa da)."""
    monkeypatch.chdir(tmp_path)

    batch = [csv_line(754567, 300, 32, 780, 6283, 1)]
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
    messages = drain_until(data_queue, "csv", 1)
    stop_event.set()
    worker.join(timeout=2.0)
    assert not worker.is_alive()

    csv_messages = [m for m in messages if m["type"] == "csv"]
    assert csv_messages[0]["timestamp_ms"] == 754567
