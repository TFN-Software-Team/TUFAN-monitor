"""MON-14 (madde 85): senkron klasör (OneDrive vb.) uyarısı testleri."""

import queue
import threading

import config
import monitor_core

from tests.test_monitor_serial import csv_line, drain_until, scripted_connect_factory


# --- detect_cloud_sync_folder (saf fonksiyon) --------------------------------


def test_detect_cloud_sync_folder_detects_onedrive():
    path = "C:\\Users\\ravzanur\\OneDrive\\Belgeler\\TUFAN-Monitor\\logs"
    assert monitor_core.detect_cloud_sync_folder(path) == "OneDrive"


def test_detect_cloud_sync_folder_detects_dropbox():
    path = "C:\\Users\\ravzanur\\Dropbox\\logs"
    assert monitor_core.detect_cloud_sync_folder(path) == "Dropbox"


def test_detect_cloud_sync_folder_detects_google_drive():
    path = "C:\\Users\\ravzanur\\Google Drive\\logs"
    assert monitor_core.detect_cloud_sync_folder(path) == "Google Drive"


def test_detect_cloud_sync_folder_detects_icloud():
    path = "C:\\Users\\ravzanur\\iCloudDrive\\logs"
    assert monitor_core.detect_cloud_sync_folder(path) == "iCloud"


def test_detect_cloud_sync_folder_case_insensitive():
    path = "C:\\Users\\ravzanur\\onedrive\\logs"
    assert monitor_core.detect_cloud_sync_folder(path) == "OneDrive"


def test_detect_cloud_sync_folder_returns_none_for_plain_path():
    path = "C:\\Users\\ravzanur\\Desktop\\TUFAN-Monitor\\logs"
    assert monitor_core.detect_cloud_sync_folder(path) is None


# --- serial_worker entegrasyonu ----------------------------------------------


def test_worker_warns_but_does_not_block_when_output_dir_is_in_onedrive(tmp_path, monkeypatch):
    fake_onedrive_logs = tmp_path / "OneDrive" / "logs"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "OUTPUT_DIR", str(fake_onedrive_logs))

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
    assert not worker.is_alive(), "bulut senkron klasoru uyarisi worker'i ENGELLEMEMELI"

    warnings = [m for m in messages if m["type"] == "cloud_sync_warning"]
    assert len(warnings) == 1
    assert warnings[0]["service"] == "OneDrive"

    # Kayıt normal şekilde devam etmeli -- uyarı ENGELLEMEZ.
    log_files = list(fake_onedrive_logs.glob("telem_*.csv"))
    assert len(log_files) == 1
    assert len(log_files[0].read_text(encoding="utf-8").splitlines()) == 1 + 1

    events_files = list(fake_onedrive_logs.glob("events_*.log"))
    events_text = events_files[0].read_text(encoding="utf-8")
    assert "bulut senkron klasorunde (OneDrive)" in events_text


def test_worker_no_warning_for_plain_output_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "OUTPUT_DIR", "logs")

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

    assert not any(m["type"] == "cloud_sync_warning" for m in messages)
