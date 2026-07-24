"""SERIAL_PORT icin komut satiri (--port/--no-gui) ve otomatik port tespiti
testleri.

Saf fonksiyonlar (argparse ayristirma, oncelik cozumu, mesaj bicimlendirme)
gercek donanim/pyserial list_ports veya tkinter gerektirmeden test edilir.

MON-10 (madde 87): monitor.py artik tkinter/matplotlib'i MODUL SEVIYESINDE
degil, yalniz `run_gui()` cagrildiginda (bkz. monitor._load_gui_dependencies)
yukler. main()'i gercek tkinter'e dokunmadan test etmek icin
`_load_gui_dependencies` NO-OP'a cevrilir ve `monitor.tk` sahte bir modulle
degistirilir (bkz. _patch_gui).
"""

import os
import sys

import pytest

import config
import monitor


class _FakeTkModule:
    """monitor.tk'nin yerini alir -- yalniz main()/run_gui()'nin cagirdigi
    tk.Tk() icin bir sahte kok dondurur."""

    def __init__(self, root_factory):
        self.Tk = root_factory


def _patch_gui(monkeypatch, root_factory, app_factory=None):
    """MON-10: run_gui() tkinter'i LAZY yukler; testte gercek tkinter'e
    dokunmadan mock'lamak icin _load_gui_dependencies NO-OP'a cevrilir
    (boylece monitor.tk'yi gercek modulle EZMEZ) ve monitor.tk sahte bir
    modulle degistirilir."""
    monkeypatch.setattr(monitor, "_load_gui_dependencies", lambda: None)
    monkeypatch.setattr(monitor, "tk", _FakeTkModule(root_factory))
    if app_factory is not None:
        monkeypatch.setattr(monitor, "MonitorApp", app_factory)


class _FakeRoot:
    def protocol(self, *a, **k):
        pass

    def after(self, *a, **k):
        pass

    def mainloop(self, *a, **k):
        pass


def test_parse_args_no_port_defaults_to_none():
    args = monitor.parse_args([])
    assert args.port is None


def test_parse_args_port_flag_is_captured():
    args = monitor.parse_args(["--port", "COM5"])
    assert args.port == "COM5"


def test_parse_args_no_gui_flag_defaults_to_false():
    args = monitor.parse_args([])
    assert args.no_gui is False


def test_parse_args_no_gui_flag_is_captured():
    args = monitor.parse_args(["--no-gui"])
    assert args.no_gui is True


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
    # dogrulamak icin GUI mock'lanir (bkz. _patch_gui).
    monkeypatch.setattr(monitor.config, "SERIAL_PORT", "SIMULATE")
    monkeypatch.setattr(sys, "argv", ["monitor.py", "--port", "COM9"])

    created = {}

    class FakeApp:
        def __init__(self, root):
            created["root"] = root

    _patch_gui(monkeypatch, lambda: _FakeRoot(), FakeApp)

    monitor.main()

    assert monitor.config.SERIAL_PORT == "COM9"
    assert "root" in created


def test_main_lists_ports_when_no_cli_port_and_config_is_simulate(monkeypatch, capsys):
    monkeypatch.setattr(monitor.config, "SERIAL_PORT", "SIMULATE")
    monkeypatch.setattr(sys, "argv", ["monitor.py"])
    monkeypatch.setattr(monitor, "list_available_ports", lambda: ["COM4"])

    class FakeApp:
        def __init__(self, root):
            pass

    _patch_gui(monkeypatch, lambda: _FakeRoot(), FakeApp)

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

    class FakeApp:
        def __init__(self, root):
            pass

    _patch_gui(monkeypatch, lambda: _FakeRoot(), FakeApp)

    monitor.main()

    assert listed["called"] is False


# --- MON-12 (madde 84): OUTPUT_DIR yazılabilirlik ön-kontrolü --------------


def test_check_output_dir_writable_creates_dir_and_leaves_no_probe_file(tmp_path):
    target = tmp_path / "kayit"
    monitor.check_output_dir_writable(str(target))
    assert target.is_dir()
    assert list(target.iterdir()) == [], "prob dosyası kalıcı olarak bırakılmamalı"


def test_check_output_dir_writable_raises_runtime_error_when_path_is_blocked(tmp_path):
    # OUTPUT_DIR olarak bir DOSYA yolu verilirse (klasör değil), os.makedirs
    # başarısız olur -- exception dışarı SIZMAMALI, net bir RuntimeError'a
    # dönüştürülmeli.
    blocked = tmp_path / "not_a_dir"
    blocked.write_text("engel dosyası", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Kayıt klasörüne yazılamıyor"):
        monitor.check_output_dir_writable(str(blocked))


def test_main_aborts_before_starting_gui_when_output_dir_not_writable(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(monitor.config, "SERIAL_PORT", "SIMULATE_TEST")
    monkeypatch.setattr(sys, "argv", ["monitor.py"])

    def fail_check(directory):
        raise RuntimeError(f"Kayıt klasörüne yazılamıyor: {directory} (simulated)")

    monkeypatch.setattr(monitor, "check_output_dir_writable", fail_check)

    tk_created = {"called": False}
    _patch_gui(monkeypatch, lambda: tk_created.update(called=True))

    with pytest.raises(SystemExit) as exc_info:
        monitor.main()

    assert exc_info.value.code == 1
    assert tk_created["called"] is False, "yazılamayan bir klasörle GUI hiç başlatılmamalı"
    assert "BAŞLATILAMADI" in capsys.readouterr().out


def test_main_prints_full_output_dir_path_on_successful_start(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(monitor.config, "SERIAL_PORT", "SIMULATE_TEST")
    monkeypatch.setattr(config, "OUTPUT_DIR", "logs")
    monkeypatch.setattr(sys, "argv", ["monitor.py"])

    class FakeApp:
        def __init__(self, root):
            pass

    _patch_gui(monkeypatch, lambda: _FakeRoot(), FakeApp)

    monitor.main()

    out = capsys.readouterr().out
    assert os.path.abspath("logs") in out


# --- MON-10 (madde 87): headless (--no-gui) modu ----------------------------


def test_main_no_gui_never_touches_gui_dependencies(monkeypatch):
    """--no-gui verildiginde main() GUI'yi (tk.Tk/MonitorApp) HIC cagirmamali
    -- yalniz run_headless() cagirmali."""
    monkeypatch.setattr(monitor.config, "SERIAL_PORT", "SIMULATE_TEST")
    monkeypatch.setattr(sys, "argv", ["monitor.py", "--no-gui"])

    gui_loaded = {"called": False}
    headless_called = {"called": False}

    monkeypatch.setattr(monitor, "_load_gui_dependencies", lambda: gui_loaded.update(called=True))
    monkeypatch.setattr(monitor, "run_headless", lambda: headless_called.update(called=True))

    monitor.main()

    assert headless_called["called"] is True
    assert gui_loaded["called"] is False, "--no-gui GUI bagimliliklarini HIC yuklememeli"


def test_main_falls_back_to_headless_when_gui_import_fails(monkeypatch, capsys):
    """GUI import'u (tkinter/matplotlib eksik) basarisiz olursa main()
    CIKMAMALI -- otomatik olarak headless moda dusmeli, bunu NET yazmali."""
    monkeypatch.setattr(monitor.config, "SERIAL_PORT", "SIMULATE_TEST")
    monkeypatch.setattr(sys, "argv", ["monitor.py"])

    def failing_load():
        raise ImportError("No module named 'tkinter'")

    headless_called = {"called": False}
    monkeypatch.setattr(monitor, "_load_gui_dependencies", failing_load)
    monkeypatch.setattr(monitor, "run_headless", lambda: headless_called.update(called=True))

    monitor.main()  # ÇIKMAMALI (sys.exit/exception fırlatmamalı)

    assert headless_called["called"] is True
    out = capsys.readouterr().out
    assert "HEADLESS" in out.upper()
