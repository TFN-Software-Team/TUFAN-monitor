"""MON-02 (madde 20): ikincil (yedek) kayıt yolu testleri.

BACKUP_OUTPUT_DIR ayarlandığında birincil dosyaya yazılan her satırın
AYNEN ikincil bir dosyaya da yazıldığını, ikincil yazma HATASININ birincili
ASLA etkilemediğini ve GUI'ye durum mesajları (backup_status) gittiğini
doğrular. Gerçek pyserial/donanım kullanılmaz (bkz. test_monitor_serial.py
ile aynı ScriptedSerial deseni).
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
    def __init__(self, lines):
        self._lines = list(lines)

    def readline(self):
        if not self._lines:
            raise serial.SerialException("simulated cable pull")
        return self._lines.pop(0)

    def close(self):
        pass


def csv_line(ts, speed_x10, temp, pack_dv, soc, seq):
    return f"CSV,{ts},{speed_x10},{temp},{pack_dv},{soc},{seq}\n".encode("utf-8")


def drain_until(data_queue, msg_type, count, timeout=5.0):
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


# --- open_backup_log_file (saf/dosya-seviyesi) -------------------------------


def test_open_backup_log_file_returns_none_when_not_configured(monkeypatch):
    monkeypatch.setattr(config, "BACKUP_OUTPUT_DIR", None)
    f, result = monitor_core.open_backup_log_file("telem_20260101_000000.csv")
    assert f is None
    assert result is None


def test_open_backup_log_file_creates_file_with_same_basename(tmp_path, monkeypatch):
    backup_dir = tmp_path / "usb"
    monkeypatch.setattr(config, "BACKUP_OUTPUT_DIR", str(backup_dir))
    primary = tmp_path / "logs" / "telem_20260101_000000.csv"

    f, path = monitor_core.open_backup_log_file(str(primary))
    assert f is not None
    f.close()

    assert os.path.basename(path) == "telem_20260101_000000.csv"
    assert (backup_dir / "telem_20260101_000000.csv").read_text(encoding="utf-8") == HEADER + "\n"


def test_open_backup_log_file_returns_error_message_on_failure_without_raising(tmp_path, monkeypatch):
    # BACKUP_OUTPUT_DIR bir DOSYA olarak var (klasör değil) -- os.makedirs
    # bunun üzerine dizin oluşturamaz; açma HATA vermeli ama exception
    # DIŞARI SIZMAMALI (fonksiyon hiçbir zaman raise etmez).
    blocked_path = tmp_path / "not_a_dir"
    blocked_path.write_text("engel dosyasi", encoding="utf-8")
    monkeypatch.setattr(config, "BACKUP_OUTPUT_DIR", str(blocked_path))

    f, result = monitor_core.open_backup_log_file("telem_x.csv")
    assert f is None
    assert result is not None


# --- serial_worker entegrasyonu ----------------------------------------------


def test_backup_file_mirrors_primary_when_configured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    backup_dir = tmp_path / "usb_backup"
    monkeypatch.setattr(config, "BACKUP_OUTPUT_DIR", str(backup_dir))

    batch = [
        csv_line(1000, 300, 32, 780, 6283, 1),
        csv_line(1100, 305, 33, 781, 6280, 2),
    ]

    def connect():
        return ScriptedSerial(batch)

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

    primary_files = list((tmp_path / "logs").glob("telem_*.csv"))
    assert len(primary_files) == 1
    backup_files = list(backup_dir.glob("telem_*.csv"))
    assert len(backup_files) == 1
    assert primary_files[0].name == backup_files[0].name, "yedek dosya adi birincille AYNI olmali"
    assert primary_files[0].read_text(encoding="utf-8") == backup_files[0].read_text(encoding="utf-8")

    backup_status = [m for m in messages if m["type"] == "backup_status"]
    assert any(m["active"] is True for m in backup_status), "GUI'ye yedek AKTIF bildirilmeli"


class _FailingAfterNWrites:
    """İlk `fail_after` write() çağrısından sonra her yazmada hata verir --
    gerçek bir USB'nin kayıt sırasında çıkarılmasını simüle eder."""

    def __init__(self, fail_after=1):
        self.calls = 0
        self.fail_after = fail_after
        self.closed = False

    def write(self, data):
        self.calls += 1
        if self.calls > self.fail_after:
            raise OSError("simulated backup write failure (USB cikarildi)")

    def flush(self):
        pass

    def close(self):
        self.closed = True


def test_backup_write_failure_does_not_affect_primary_recording(tmp_path, monkeypatch):
    """MON-02 madde 3: ikincil yazma HATASI birincil kaydı ASLA etkilemez."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "BACKUP_OUTPUT_DIR", "usb_backup")

    failing_backup = _FailingAfterNWrites(fail_after=1)
    monkeypatch.setattr(
        monitor_core, "open_backup_log_file",
        lambda primary: (failing_backup, "usb_backup/telem_x.csv"),
    )

    batch = [
        csv_line(1000, 300, 32, 780, 6283, 1),
        csv_line(1100, 305, 33, 781, 6280, 2),
        csv_line(1200, 310, 34, 782, 6270, 3),
    ]

    def connect():
        return ScriptedSerial(batch)

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

    primary_files = list((tmp_path / "logs").glob("telem_*.csv"))
    lines = primary_files[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 + 3, "yedek yazma hatasi birincil kaydi ETKILEMEMELI"

    backup_status = [m for m in messages if m["type"] == "backup_status"]
    assert any(m["active"] is False and "detail" in m for m in backup_status), (
        "yedek yazma hatasi GUI'ye kucuk bir uyari olarak bildirilmeli"
    )

    events_files = list((tmp_path / "logs").glob("events_*.log"))
    events_text = events_files[0].read_text(encoding="utf-8")
    assert "YEDEK KAYIT YAZMA HATASI" in events_text


def test_no_backup_configured_produces_no_backup_status_messages(tmp_path, monkeypatch):
    """BACKUP_OUTPUT_DIR ayarlanmamışken (varsayılan) hiçbir 'backup_status'
    mesajı üretilmemeli -- mevcut (yedeksiz) davranış birebir korunmalı."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "BACKUP_OUTPUT_DIR", None)

    batch = [csv_line(1000, 300, 32, 780, 6283, 1)]
    connect = lambda: ScriptedSerial(batch)

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

    assert not any(m["type"] == "backup_status" for m in messages)
