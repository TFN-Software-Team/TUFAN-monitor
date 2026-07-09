"""Seri port dayaniklilik testleri: fake-serial ile kopma/yeniden baglanma
ve replay (eski ts, artan seq) senaryolari.

Gercek pyserial/donanim kullanilmaz; monitor.serial_worker'a `connect`
parametresiyle sahte bir baglanti fabrikasi enjekte edilir.
"""

import queue
import threading
import time

import serial

import monitor
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
        target=monitor.serial_worker,
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
        target=monitor.serial_worker,
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
        target=monitor.serial_worker,
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
        target=monitor.serial_worker,
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
