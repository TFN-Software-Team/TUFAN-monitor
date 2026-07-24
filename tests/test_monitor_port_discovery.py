"""MON-09 (madde 86): COM portu otomatik keşfi testleri.

Gerçek pyserial donanımı kullanılmaz -- serial.Serial ve list_ports.comports
sahtelenir. open_serial_connection()'ın fallback mantığı ile serial_worker'ın
bulunan portları loglama/queue mesajlarını test eder.
"""

import queue
import threading
import time

import pytest
import serial

import config
import monitor_core


class _FakePortInfo:
    def __init__(self, device):
        self.device = device


def _set_fake_ports(monkeypatch, devices):
    monkeypatch.setattr(
        monitor_core.list_ports, "comports", lambda: [_FakePortInfo(d) for d in devices]
    )


class _FakeSerial:
    """serial.Serial yerine geçer -- yalnız verilen port(lar) için başarılı
    "açılır", diğerleri SerialException fırlatır."""

    def __init__(self, port, baud, timeout=2):
        if port not in _FakeSerial.openable_ports:
            raise serial.SerialException(f"could not open port {port}")
        self.port = port
        self.baudrate = baud

    openable_ports = set()

    def readline(self):
        return b""

    def close(self):
        pass


# --- open_serial_connection() fallback mantığı ------------------------------


def test_open_serial_connection_uses_configured_port_when_it_opens(monkeypatch):
    monkeypatch.setattr(config, "SERIAL_PORT", "COM5")
    monkeypatch.setattr(config, "AUTO_DISCOVER_PORT", True)
    monkeypatch.setattr(serial, "Serial", _FakeSerial)
    _FakeSerial.openable_ports = {"COM5", "COM7"}
    _set_fake_ports(monkeypatch, ["COM5", "COM7"])

    ser = monitor_core.open_serial_connection()
    assert ser.port == "COM5"


def test_open_serial_connection_falls_back_to_discovered_port(monkeypatch):
    monkeypatch.setattr(config, "SERIAL_PORT", "COM5")
    monkeypatch.setattr(config, "AUTO_DISCOVER_PORT", True)
    monkeypatch.setattr(serial, "Serial", _FakeSerial)
    _FakeSerial.openable_ports = {"COM7"}  # COM5 (config) acilamiyor, COM7 aciliyor
    _set_fake_ports(monkeypatch, ["COM5", "COM7"])

    ser = monitor_core.open_serial_connection()
    assert ser.port == "COM7"


def test_open_serial_connection_raises_when_no_port_opens(monkeypatch):
    monkeypatch.setattr(config, "SERIAL_PORT", "COM5")
    monkeypatch.setattr(config, "AUTO_DISCOVER_PORT", True)
    monkeypatch.setattr(serial, "Serial", _FakeSerial)
    _FakeSerial.openable_ports = set()  # hicbiri acilmiyor
    _set_fake_ports(monkeypatch, ["COM5", "COM7"])

    with pytest.raises(serial.SerialException):
        monitor_core.open_serial_connection()


def test_open_serial_connection_does_not_fall_back_when_auto_discover_disabled(monkeypatch):
    monkeypatch.setattr(config, "SERIAL_PORT", "COM5")
    monkeypatch.setattr(config, "AUTO_DISCOVER_PORT", False)
    monkeypatch.setattr(serial, "Serial", _FakeSerial)
    _FakeSerial.openable_ports = {"COM7"}  # COM7 acilabilir ama AUTO_DISCOVER kapali
    _set_fake_ports(monkeypatch, ["COM5", "COM7"])

    with pytest.raises(serial.SerialException):
        monitor_core.open_serial_connection()


# --- serial_worker entegrasyonu: port_down/port_up mesajları ---------------


def test_port_down_message_carries_available_ports(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _set_fake_ports(monkeypatch, ["COM3", "COM9"])

    def never_connects():
        raise serial.SerialException("port yok")

    data_queue = queue.Queue()
    stop_event = threading.Event()

    worker = threading.Thread(
        target=monitor_core.serial_worker,
        args=(data_queue, stop_event),
        kwargs={"connect": never_connects, "reconnect_interval": 0.02},
        daemon=True,
    )
    worker.start()

    msg = None
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            m = data_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        if m["type"] == "port_down":
            msg = m
            break
    stop_event.set()
    worker.join(timeout=2.0)

    assert msg is not None
    assert msg["available_ports"] == ["COM3", "COM9"]

    events_files = list((tmp_path / "logs").glob("events_*.log"))
    events_text = events_files[0].read_text(encoding="utf-8")
    assert "SERI PORT BULUNAMADI" in events_text
    assert "COM3" in events_text and "COM9" in events_text


def test_port_up_message_carries_actual_port_from_serial_object(tmp_path, monkeypatch):
    """connect() bir .port özniteliği taşıyan bir nesne dönerse (gerçek
    serial.Serial gibi), serial_worker bunu config.SERIAL_PORT yerine
    kullanmalı -- otomatik keşifle farklı bir porta düşülmüş olabilir."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "SERIAL_PORT", "COM5")

    class _StubSerial:
        def __init__(self, port):
            self.port = port
            self._done = False

        def readline(self):
            if self._done:
                raise serial.SerialException("done")
            self._done = True
            return b""

        def close(self):
            pass

    def connect():
        return _StubSerial("COM7")  # config COM5 degil -- otomatik bulunmus gibi

    data_queue = queue.Queue()
    stop_event = threading.Event()

    worker = threading.Thread(
        target=monitor_core.serial_worker,
        args=(data_queue, stop_event),
        kwargs={"connect": connect, "reconnect_interval": 0.02},
        daemon=True,
    )
    worker.start()

    import time as _time
    port_up_msg = None
    deadline = _time.monotonic() + 3.0
    while _time.monotonic() < deadline:
        try:
            m = data_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        if m["type"] == "port_up":
            port_up_msg = m
            break
    stop_event.set()
    worker.join(timeout=2.0)

    assert port_up_msg is not None
    assert port_up_msg["port"] == "COM7"

    events_files = list((tmp_path / "logs").glob("events_*.log"))
    events_text = events_files[0].read_text(encoding="utf-8")
    assert "SERI PORT OTOMATIK BULUNDU" in events_text
    assert "COM7" in events_text
