"""MON-01 (madde 19): worker thread heartbeat, çökme-kurtarma ve "KAYIT DURDU"
durumu testleri.

Gerçek tkinter GUI'ye dokunmadan, update_gui'nin worker sağlığını nasıl
değerlendirdiğini birebir yansıtan `compute_worker_health_state` saf
fonksiyonu ile WorkerHeartbeat/RateLimiter yardımcı sınıfları test edilir;
ayrıca serial_worker'a kasıtlı bir exception attırılarak worker'ın
SESSİZCE ölmediği (tam traceback + "worker_crashed" mesajı + GUI'nin
"KAYIT DURDU" durumuna geçmesine yol açan koşullar) doğrulanır.
"""

import queue
import threading
import time

import serial

import monitor_core


def test_worker_heartbeat_starts_fresh():
    hb = monitor_core.WorkerHeartbeat()
    assert hb.seconds_since_beat() < 1.0


def test_worker_heartbeat_beat_resets_age():
    hb = monitor_core.WorkerHeartbeat()
    hb._last_beat -= 10  # 10 sn önceki bir heartbeat simüle edilir
    assert hb.seconds_since_beat() >= 10
    hb.beat()
    assert hb.seconds_since_beat() < 1.0


def test_compute_worker_health_state_alive_fresh_heartbeat_is_healthy():
    state = monitor_core.compute_worker_health_state(
        worker_alive=True, heartbeat_seconds_since_beat=0.1, restart_count=0
    )
    assert state == {
        "should_restart": False,
        "permanently_failed": False,
        "recording_stopped": False,
    }


def test_compute_worker_health_state_stale_heartbeat_stops_recording_without_restart():
    # Thread hâlâ "alive" olabilir (donmuş/takılmış) ama heartbeat atmayı
    # bırakmıştır -- zaten çalışan bir thread'i yeniden başlatmanın anlamı
    # yok, ama KAYIT DURDU gösterilmeli.
    state = monitor_core.compute_worker_health_state(
        worker_alive=True, heartbeat_seconds_since_beat=6.0, restart_count=0
    )
    assert state["should_restart"] is False
    assert state["permanently_failed"] is False
    assert state["recording_stopped"] is True


def test_compute_worker_health_state_dead_worker_triggers_restart():
    state = monitor_core.compute_worker_health_state(
        worker_alive=False, heartbeat_seconds_since_beat=0.5, restart_count=0
    )
    assert state["should_restart"] is True
    assert state["permanently_failed"] is False
    assert state["recording_stopped"] is True


def test_compute_worker_health_state_exhausted_restarts_is_permanent():
    state = monitor_core.compute_worker_health_state(
        worker_alive=False, heartbeat_seconds_since_beat=0.5,
        restart_count=monitor_core.MAX_WORKER_RESTARTS,
    )
    assert state["should_restart"] is False
    assert state["permanently_failed"] is True
    assert state["recording_stopped"] is True


def test_rate_limiter_allows_up_to_max_per_second_then_suppresses():
    limiter = monitor_core.RateLimiter(max_per_sec=5)
    now = 100.0
    results = [limiter.record(now) for _ in range(7)]
    allowed = [r[0] for r in results]
    assert allowed == [True, True, True, True, True, False, False]
    assert all(r[1] == 0 for r in results), "ayni pencerede bastirilan sayisi henuz bildirilmemeli"


def test_rate_limiter_reports_suppressed_count_on_next_window():
    limiter = monitor_core.RateLimiter(max_per_sec=2)
    for _ in range(5):
        limiter.record(100.0)  # 2 izinli, 3 bastirilan, ayni saniye penceresi

    allowed, suppressed_prev = limiter.record(101.0)  # yeni pencere
    assert allowed is True
    assert suppressed_prev == 3


class _CrashingSerial:
    """readline() her çağrıda beklenmeyen (SerialException/OSError
    OLMAYAN) bir istisna fırlatır -- gerçek bir donanım hatası değil,
    yazılımdaki öngörülmemiş bir hata senaryosunu simüle eder."""

    def readline(self):
        raise RuntimeError("simulated unexpected hardware failure")

    def close(self):
        pass


def test_serial_worker_survives_unexpected_exception_and_reports_crash(tmp_path, monkeypatch):
    """9.2.g: worker beklenmedik bir istisnayla karşılaşırsa SESSİZCE
    ölmemeli -- tam traceback events log'a yazılmalı, data_queue'ya
    "worker_crashed" mesajı konulmalı ve thread düzgünce sonlanmalı (GUI
    tarafı bunu is_alive()==False olarak görüp KAYIT DURDU'ya geçer)."""
    monkeypatch.chdir(tmp_path)

    def crashing_connect():
        return _CrashingSerial()

    data_queue = queue.Queue()
    stop_event = threading.Event()
    heartbeat = monitor_core.WorkerHeartbeat()

    worker = threading.Thread(
        target=monitor_core.serial_worker,
        args=(data_queue, stop_event),
        kwargs={"connect": crashing_connect, "reconnect_interval": 0.02, "heartbeat": heartbeat},
        daemon=True,
    )
    worker.start()
    worker.join(timeout=3.0)
    assert not worker.is_alive(), "worker, yakalanmayan bir istisnadan sonra bile thread olarak sonlanmali"

    messages = []
    while True:
        try:
            messages.append(data_queue.get_nowait())
        except queue.Empty:
            break

    crash_messages = [m for m in messages if m["type"] == "worker_crashed"]
    assert len(crash_messages) == 1, "crash tam olarak bir 'worker_crashed' mesaji uretmeli"
    assert "RuntimeError" in crash_messages[0]["traceback"]
    assert "simulated unexpected hardware failure" in crash_messages[0]["traceback"]

    events_files = list((tmp_path / "logs").glob("events_*.log"))
    assert len(events_files) == 1
    events_text = events_files[0].read_text(encoding="utf-8")
    assert "WORKER CRASH" in events_text
    assert "RuntimeError" in events_text
    assert "Izleme durduruldu" in events_text, "finally bloğu her durumda çalışmalı"

    # GUI'nin update_gui'de kullandığı TAM karar mantığı: thread artık
    # canlı değil ve heartbeat de crash anında son kez atılıp durdu ->
    # KAYIT DURDU durumuna geçmeli.
    state = monitor_core.compute_worker_health_state(
        worker_alive=worker.is_alive(),
        heartbeat_seconds_since_beat=heartbeat.seconds_since_beat(),
        restart_count=0,
    )
    assert state["recording_stopped"] is True, "ekran durumu KAYIT DURDU olmali"


def test_serial_worker_restart_attempt_is_logged(tmp_path, monkeypatch):
    """MON-01 madde 4: her yeniden başlatma denemesi events log'a düşmeli."""
    monkeypatch.chdir(tmp_path)

    def never_connects():
        raise serial.SerialException("port yok")

    data_queue = queue.Queue()
    stop_event = threading.Event()
    heartbeat = monitor_core.WorkerHeartbeat()

    worker = threading.Thread(
        target=monitor_core.serial_worker,
        args=(data_queue, stop_event),
        kwargs={
            "connect": never_connects,
            "reconnect_interval": 0.02,
            "heartbeat": heartbeat,
            "restart_attempt": 2,
        },
        daemon=True,
    )
    worker.start()
    time.sleep(0.1)
    stop_event.set()
    worker.join(timeout=2.0)
    assert not worker.is_alive()

    events_files = list((tmp_path / "logs").glob("events_*.log"))
    events_text = events_files[0].read_text(encoding="utf-8")
    assert f"WORKER YENIDEN BASLATILDI (deneme 2/{monitor_core.MAX_WORKER_RESTARTS})" in events_text
