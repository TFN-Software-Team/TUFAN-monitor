"""Y26 — ham satir yayini (GUI'deki opsiyonel terminal paneli icin).

NEDEN VAR: bir seri portu ayni anda TEK program acabilir (isletim sistemi
kisiti), bu yuzden UKS terminal-izleme ile TUFAN-Monitor GUI'si BIRLIKTE
calistirilamiyordu. Cozum, ham satirlari Monitor'un KENDI icinde gostermek —
boylece ayri bir terminal programina ihtiyac kalmaz.

Bu dosya, worker'in ham satirlari kuyruga yaydigini VE bunun kayit
davranisini ETKILEMEDIGINI dogrular.
"""

import queue
import threading

import serial

import monitor_core
from tests.test_monitor_serial import (
    ScriptedSerial,
    csv_line,
    drain_until,
    scripted_connect_factory,
)


def _run_worker(batches, wait_for, count, tmp_path, monkeypatch, timeout=5.0):
    monkeypatch.chdir(tmp_path)
    connect = scripted_connect_factory(batches)
    data_queue = queue.Queue()
    stop_event = threading.Event()

    worker = threading.Thread(
        target=monitor_core.serial_worker,
        args=(data_queue, stop_event),
        kwargs={"connect": connect, "reconnect_interval": 0.05},
        daemon=True,
    )
    worker.start()
    try:
        messages = drain_until(data_queue, wait_for, count, timeout=timeout)
    finally:
        stop_event.set()
        worker.join(timeout=2.0)
    assert not worker.is_alive()
    return messages


def test_raw_lines_are_emitted_for_csv_rows(tmp_path, monkeypatch):
    """Gecerli CSV satirlari icin ham satir mesaji da yayinlanir."""
    batch = [
        csv_line(50000, 300, 32, 780, 6283, 100),
        csv_line(50100, 310, 32, 780, 6283, 101),
    ]
    messages = _run_worker([batch], "csv", 2, tmp_path, monkeypatch)

    raw = [m["line"] for m in messages if m["type"] == "raw_line"]
    assert "CSV,50000,300,32,780,6283,100" in raw
    assert "CSV,50100,310,32,780,6283,101" in raw


def test_raw_lines_include_non_csv_traffic(tmp_path, monkeypatch):
    """Panelin terminal yerine gecebilmesi icin CSV OLMAYAN satirlar da
    (LINK durum satirlari, UKS dashboard ciktisi vb.) yayinlanmalidir —
    aksi halde kullanici baglanti olaylarini gormek icin yine terminale
    ihtiyac duyardi."""
    batch = [
        b"LINK,DOWN,12345\n",
        b"LINK,UP,12800\n",
        csv_line(50000, 300, 32, 780, 6283, 100),
    ]
    messages = _run_worker([batch], "csv", 1, tmp_path, monkeypatch)

    raw = [m["line"] for m in messages if m["type"] == "raw_line"]
    assert "LINK,DOWN,12345" in raw
    assert "LINK,UP,12800" in raw


def test_raw_lines_do_not_affect_recorded_rows(tmp_path, monkeypatch):
    """KAYIT DAVRANISI ETKILENMEZ: ham satir yayini eklendikten sonra da
    dosyaya YALNIZCA gecerli CSV satirlari yazilir; LINK satirlari ve
    aralik-disi satirlar kayda GECMEZ."""
    batch = [
        b"LINK,UP,1000\n",
        csv_line(50000, 300, 32, 780, 6283, 100),
        csv_line(50100, 99999, 32, 780, 6283, 101),  # hiz aralik disi -> reddedilir
        csv_line(50200, 300, 32, 780, 6283, 102),
    ]
    messages = _run_worker([batch], "csv", 2, tmp_path, monkeypatch)

    log_files = sorted((tmp_path / "logs").glob("telem_*.csv"))
    assert len(log_files) == 1
    data_rows = [
        ln for ln in log_files[0].read_text(encoding="utf-8").splitlines()[1:] if ln.strip()
    ]

    # Yalnizca iki GECERLI CSV satiri yazilmis olmali.
    assert len(data_rows) == 2
    assert data_rows[0].startswith("50000;")
    assert data_rows[1].startswith("50200;")

    # Ama ham satir yayininda REDDEDILEN satir da LINK satiri da gorunur —
    # panelin amaci zaten "hatta ne akiyor" sorusuna cevap vermek.
    raw = [m["line"] for m in messages if m["type"] == "raw_line"]
    assert "LINK,UP,1000" in raw
    assert "CSV,50100,99999,32,780,6283,101" in raw


def test_blank_lines_are_not_emitted(tmp_path, monkeypatch):
    """Bos/whitespace satirlar paneli kirletmemeli."""
    batch = [
        b"\n",
        b"   \r\n",
        csv_line(50000, 300, 32, 780, 6283, 100),
    ]
    messages = _run_worker([batch], "csv", 1, tmp_path, monkeypatch)

    raw = [m["line"] for m in messages if m["type"] == "raw_line"]
    assert raw == ["CSV,50000,300,32,780,6283,100"]


def test_scripted_serial_helper_still_signals_disconnect():
    """Yardimci sinifin sozlesmesi (satirlar bitince kopma) korunuyor —
    yukaridaki testler bu davranisa dayaniyor."""
    s = ScriptedSerial([])
    try:
        s.readline()
    except serial.SerialException:
        return
    raise AssertionError("ScriptedSerial bos listede SerialException firlatmali")
