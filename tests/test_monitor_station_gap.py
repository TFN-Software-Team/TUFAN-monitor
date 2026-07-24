"""MON-16 (madde 66): yer istasyonu kesintisinde veri kaybını görünür kılma
testleri.

find_previous_session_file / read_last_timestamp_ms saf fonksiyonları ve
serial_worker'ın önceki oturum tespiti + ilk-satır boşluk hesabı test edilir.
"""

import queue
import threading
import time

from csv_logger import HEADER

import monitor_core

from tests.test_monitor_serial import csv_line, drain_until, scripted_connect_factory


# --- find_previous_session_file / read_last_timestamp_ms --------------------


def test_find_previous_session_file_returns_none_when_dir_missing(tmp_path):
    assert monitor_core.find_previous_session_file(str(tmp_path / "yok")) is None


def test_find_previous_session_file_returns_none_when_no_telem_files(tmp_path):
    (tmp_path / "events_20260101_000000.log").write_text("x", encoding="utf-8")
    assert monitor_core.find_previous_session_file(str(tmp_path)) is None


def test_find_previous_session_file_returns_most_recently_modified(tmp_path):
    old = tmp_path / "telem_20260101_000000.csv"
    new = tmp_path / "telem_20260102_000000.csv"
    old.write_text(HEADER + "\n", encoding="utf-8")
    time.sleep(0.05)
    new.write_text(HEADER + "\n", encoding="utf-8")

    result = monitor_core.find_previous_session_file(str(tmp_path))
    assert result == str(new)


def test_find_previous_session_file_separates_sim_from_real(tmp_path):
    real = tmp_path / "telem_20260101_000000.csv"
    sim = tmp_path / "telem_20260102_000000_SIM.csv"
    real.write_text(HEADER + "\n", encoding="utf-8")
    time.sleep(0.05)
    sim.write_text(HEADER + "\n", encoding="utf-8")

    assert monitor_core.find_previous_session_file(str(tmp_path), suffix="") == str(real)
    assert monitor_core.find_previous_session_file(str(tmp_path), suffix="_SIM") == str(sim)


def test_read_last_timestamp_ms_reads_final_line(tmp_path):
    path = tmp_path / "telem_x.csv"
    path.write_text(HEADER + "\n1000;30.0;32;78.0;5000\n2000;31.0;32;78.0;4999\n", encoding="utf-8")
    assert monitor_core.read_last_timestamp_ms(str(path)) == 2000


def test_read_last_timestamp_ms_empty_file_returns_none(tmp_path):
    path = tmp_path / "telem_x.csv"
    path.write_text(HEADER + "\n", encoding="utf-8")
    assert monitor_core.read_last_timestamp_ms(str(path)) is None


def test_read_last_timestamp_ms_missing_file_returns_none(tmp_path):
    assert monitor_core.read_last_timestamp_ms(str(tmp_path / "yok.csv")) is None


def test_read_last_timestamp_ms_malformed_last_line_returns_none(tmp_path):
    path = tmp_path / "telem_x.csv"
    path.write_text(HEADER + "\nbozuk;satir\n", encoding="utf-8")
    assert monitor_core.read_last_timestamp_ms(str(path)) is None


# --- serial_worker entegrasyonu ----------------------------------------------


def test_worker_detects_previous_session_and_reports_station_gap(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    # Önceki oturumun dosyası: AKS'in ts_ms'i 100000'de bırakılmış.
    previous = logs_dir / "telem_20260101_000000.csv"
    previous.write_text(HEADER + "\n100000;30.0;32;78.0;5000\n", encoding="utf-8")

    # Yeni oturumun ilk satırı 112400 -- yani 12.4 sn'lik bir bosluk, AKS
    # tarafinda tamponlanmamis (ayni AKS boot'u, ts_ms kesintisiz ilerlemis).
    batch = [csv_line(112400, 300, 32, 780, 6283, 1)]
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

    prev_session_msgs = [m for m in messages if m["type"] == "previous_session"]
    assert len(prev_session_msgs) == 1
    assert prev_session_msgs[0]["last_ts_ms"] == 100000

    gap_msgs = [m for m in messages if m["type"] == "station_gap"]
    assert len(gap_msgs) == 1
    assert round(gap_msgs[0]["gap_sec"], 1) == 12.4

    events_files = list(logs_dir.glob("events_*.log"))
    events_text = events_files[0].read_text(encoding="utf-8")
    assert "Onceki oturum" in events_text and "son zaman_ms: 100000" in events_text
    assert "YER ISTASYONU KESINTISI: 12.4 sn veri kaybi" in events_text

    # Yeni dosya (bu oturumun) hâlâ TEK ve geçerli olmalı; önceki dosyaya
    # dokunulmamış olmalı (kanıt bütünlüğü).
    assert previous.read_text(encoding="utf-8") == HEADER + "\n100000;30.0;32;78.0;5000\n"
    new_files = [f for f in logs_dir.glob("telem_*.csv") if f != previous]
    assert len(new_files) == 1


def test_worker_does_not_report_gap_when_new_session_ts_is_lower(tmp_path, monkeypatch):
    """Yeni oturumun ilk zaman_ms'i ÖNCEKİ oturumun son değerinden KÜÇÜKSE,
    bu muhtemelen gerçek bir AKS yeniden-boot'udur (yer istasyonu kesintisi
    DEĞİL) -- yanlış/negatif bir 'kesinti' raporu ÜRETİLMEMELİ."""
    monkeypatch.chdir(tmp_path)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    previous = logs_dir / "telem_20260101_000000.csv"
    previous.write_text(HEADER + "\n100000;30.0;32;78.0;5000\n", encoding="utf-8")

    batch = [csv_line(500, 300, 32, 780, 6283, 1)]  # ts_ms KUCUK -- gercek yeni boot ihtimali
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

    assert not any(m["type"] == "station_gap" for m in messages)


def test_worker_no_previous_session_produces_no_gap_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    batch = [csv_line(1000, 300, 32, 780, 6283, 1)]
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

    assert not any(m["type"] in ("station_gap", "previous_session") for m in messages)
