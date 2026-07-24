"""MON-10 (madde 87): headless (--no-gui) kayıt modu testleri.

monitor_core.run_headless() tkinter/matplotlib'e hiç dokunmadan çalışır --
bu dosyanın kendisi de yalnızca monitor_core'u import eder.
"""

import _thread
import threading
import time

import monitor_core


# --- format_headless_status_line (saf fonksiyon) ----------------------------


def test_format_headless_status_line_connected():
    line = monitor_core.format_headless_status_line(
        packet_count=42, last_ts_ms=12345, max_gap_sec=2.4,
        port_connected=True, link_connected=True,
    )
    assert "42" in line
    assert "12345" in line
    assert "2.4" in line
    assert "BAĞLI" in line


def test_format_headless_status_line_link_down():
    line = monitor_core.format_headless_status_line(
        packet_count=0, last_ts_ms=None, max_gap_sec=0.0,
        port_connected=True, link_connected=False,
    )
    assert "--" in line  # last_ts_ms yok
    assert "KOPUK" in line
    assert "SERİ PORT KOPUK" not in line


def test_format_headless_status_line_port_down():
    line = monitor_core.format_headless_status_line(
        packet_count=0, last_ts_ms=None, max_gap_sec=0.0,
        port_connected=False, link_connected=False,
    )
    assert "SERİ PORT KOPUK" in line


# --- run_headless() entegrasyonu (serial_worker mock'lanır) -----------------


def _fake_worker_factory(messages):
    """serial_worker yerine gecer -- verilen mesajlari kuyruga koyup
    stop_event set edilene kadar bekler (gercek worker'in "port acik kalir"
    davranisini taklit eder)."""

    def fake_worker(data_queue, stop_event, heartbeat=None, restart_attempt=0):
        if heartbeat is not None:
            heartbeat.beat()
        for msg in messages:
            data_queue.put(msg)
        stop_event.wait()

    return fake_worker


def _interrupt_main_after(delay_sec):
    def _do_interrupt():
        time.sleep(delay_sec)
        _thread.interrupt_main()

    t = threading.Thread(target=_do_interrupt, daemon=True)
    t.start()
    return t


def test_run_headless_processes_csv_messages_and_prints_status(monkeypatch, capsys):
    """run_headless()'in kendi mesaj işleme + periyodik durum basma
    mantığını, gerçek serial_worker'ı mock'layarak izole test eder."""
    messages = [
        {
            "type": "csv", "ts": 0.0, "ts_sec": 1.0, "timestamp_ms": 1000,
            "speed_kmh": 10, "temp_c": 30, "voltage_v": 78.0, "soc_percent": 50,
            "energy_wh": 100,
        },
        {
            "type": "csv", "ts": 0.0, "ts_sec": 2.0, "timestamp_ms": 2000,
            "speed_kmh": 11, "temp_c": 30, "voltage_v": 78.0, "soc_percent": 49,
            "energy_wh": 99,
        },
    ]
    monkeypatch.setattr(monitor_core, "serial_worker", _fake_worker_factory(messages))

    _interrupt_main_after(0.7)
    monitor_core.run_headless(status_interval_sec=0.05)

    out = capsys.readouterr().out
    assert "Headless" in out
    assert "satır: 2" in out
    assert "son zaman_ms: 2000" in out
    assert "durum: BAĞLI" in out
    assert "Ctrl+C alındı" in out


def test_run_headless_reports_stale_link_status_before_any_data(monkeypatch, capsys):
    """Hiç 'csv' mesajı gelmeden (port hâlâ bağlanamamış) durum yazdırılırsa
    'SERİ PORT KOPUK' görünmeli -- '--' (henüz veri yok) ile birlikte."""

    def fake_worker(data_queue, stop_event, heartbeat=None, restart_attempt=0):
        if heartbeat is not None:
            heartbeat.beat()
        data_queue.put({"type": "port_down", "ts": 0.0, "available_ports": []})
        stop_event.wait()

    monkeypatch.setattr(monitor_core, "serial_worker", fake_worker)

    _interrupt_main_after(0.7)
    monitor_core.run_headless(status_interval_sec=0.05)

    out = capsys.readouterr().out
    assert "SERİ PORT KOPUK" in out
    assert "son zaman_ms: --" in out
