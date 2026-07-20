"""SERIAL_PORT icin komut satiri (--port) ve otomatik port tespiti testleri.

Saf fonksiyonlar (argparse ayristirma, oncelik cozumu, mesaj bicimlendirme)
gercek donanim/pyserial list_ports veya tkinter gerektirmeden test edilir.
"""

import sys

import monitor


def test_parse_args_no_port_defaults_to_none():
    args = monitor.parse_args([])
    assert args.port is None


def test_parse_args_port_flag_is_captured():
    args = monitor.parse_args(["--port", "COM5"])
    assert args.port == "COM5"


def test_resolve_serial_port_cli_overrides_config():
    assert monitor.resolve_serial_port("COM5", "SIMULATE") == "COM5"
    assert monitor.resolve_serial_port("COM5", "COM3") == "COM5"


def test_resolve_serial_port_falls_back_to_config_when_cli_not_given():
    assert monitor.resolve_serial_port(None, "SIMULATE") == "SIMULATE"
    assert monitor.resolve_serial_port(None, "COM3") == "COM3"


def test_format_port_list_message_lists_devices():
    message = monitor.format_port_list_message(["COM3", "COM5"])
    assert "COM3" in message
    assert "COM5" in message


def test_format_port_list_message_empty_list_says_not_found():
    message = monitor.format_port_list_message([])
    assert "bulunamadi" in message.lower()


def test_list_available_ports_wraps_pyserial_list_ports(monkeypatch):
    class FakePortInfo:
        def __init__(self, device):
            self.device = device

    monkeypatch.setattr(
        monitor.list_ports,
        "comports",
        lambda: [FakePortInfo("COM3"), FakePortInfo("COM7")],
    )

    assert monitor.list_available_ports() == ["COM3", "COM7"]


def test_main_applies_cli_port_override_to_config(monkeypatch):
    # main() argparse + resolve_serial_port + config.SERIAL_PORT atamasini
    # yapar; tkinter mainloop'u gercekten calistirmadan bu ezme davranisini
    # dogrulamak icin tk.Tk/MonitorApp/mainloop mock'lanir.
    monkeypatch.setattr(monitor.config, "SERIAL_PORT", "SIMULATE")
    monkeypatch.setattr(sys, "argv", ["monitor.py", "--port", "COM9"])

    created = {}

    class FakeRoot:
        def protocol(self, *a, **k):
            pass

        def after(self, *a, **k):
            pass

        def mainloop(self, *a, **k):
            pass

    class FakeApp:
        def __init__(self, root):
            created["root"] = root

    monkeypatch.setattr(monitor.tk, "Tk", lambda: FakeRoot())
    monkeypatch.setattr(monitor, "MonitorApp", FakeApp)

    monitor.main()

    assert monitor.config.SERIAL_PORT == "COM9"
    assert "root" in created


def test_main_lists_ports_when_no_cli_port_and_config_is_simulate(monkeypatch, capsys):
    monkeypatch.setattr(monitor.config, "SERIAL_PORT", "SIMULATE")
    monkeypatch.setattr(sys, "argv", ["monitor.py"])
    monkeypatch.setattr(monitor, "list_available_ports", lambda: ["COM4"])

    class FakeRoot:
        def protocol(self, *a, **k):
            pass

        def after(self, *a, **k):
            pass

        def mainloop(self, *a, **k):
            pass

    class FakeApp:
        def __init__(self, root):
            pass

    monkeypatch.setattr(monitor.tk, "Tk", lambda: FakeRoot())
    monkeypatch.setattr(monitor, "MonitorApp", FakeApp)

    monitor.main()

    out = capsys.readouterr().out
    assert "COM4" in out
    assert "--port" in out


def test_main_does_not_list_ports_when_cli_port_given(monkeypatch, capsys):
    monkeypatch.setattr(monitor.config, "SERIAL_PORT", "SIMULATE")
    monkeypatch.setattr(sys, "argv", ["monitor.py", "--port", "COM5"])
    listed = {"called": False}

    def fake_list_ports():
        listed["called"] = True
        return []

    monkeypatch.setattr(monitor, "list_available_ports", fake_list_ports)

    class FakeRoot:
        def protocol(self, *a, **k):
            pass

        def after(self, *a, **k):
            pass

        def mainloop(self, *a, **k):
            pass

    class FakeApp:
        def __init__(self, root):
            pass

    monkeypatch.setattr(monitor.tk, "Tk", lambda: FakeRoot())
    monkeypatch.setattr(monitor, "MonitorApp", FakeApp)

    monitor.main()

    assert listed["called"] is False
