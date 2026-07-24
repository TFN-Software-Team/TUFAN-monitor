"""TUFAN Izleme Merkezi - saf mantik + donanim/dosya G-C (GUI-siz).

MON-11 (madde 95): monitor.py modul seviyesinde tkinter ve matplotlib
import ettigi icin, bu iki bagimliliktan biri eksik olan bir ortamda TUM
testler (saf fonksiyon testleri dahil) COLLECT HATASI veriyordu. Bu modul
GUI/donanim-ekrani gerektirmeyen TUM mantigi (seri okuma worker'i, dosya
I/O, saf yardimci fonksiyonlar) barindirir -- yalnizca pyserial (projenin
zaten temel bir bagimliligi, telemetri okumak icin kacinilmaz) ve stdlib
kullanir. monitor.py bu modulden import eder; DAVRANIS DEGISMEZ, bu saf
bir tasimadir.
"""

import bisect
import os
import queue
import threading
import time
import traceback
from collections import deque

import serial
from serial.tools import list_ports

import config
from csv_logger import (
    HEADER,
    detect_new_boot,
    format_event_line,
    format_record,
    is_replay_ts,
    make_events_log_filename,
    make_log_filename,
    parse_csv_line,
    parse_csv_line_verbose,
)

RECONNECT_INTERVAL_SEC = 2.0  # 9.2.h: port kopunca 2 sn'de bir yeniden bağlanma denenir

# MON-01 (madde 19): worker "ana döngü" en fazla bu kadar süre heartbeat
# atmadan sessiz kalırsa (veya thread bizzat ölmüşse) GUI "KAYIT DURDU"
# durumuna geçer -- bu, "KOPUK" (link/port kopması, worker hâlâ canlı)
# rozetinden GÖRSEL OLARAK FARKLI bir durumdur.
WORKER_HEARTBEAT_TIMEOUT_SEC = 5.0
MAX_WORKER_RESTARTS = 3
REJECT_LOG_MAX_PER_SEC = 5  # MON-03: aynı red sebebi için saniyede en fazla bu kadar ham satır loglanır


class WorkerHeartbeat:
    """serial_worker ile ana thread arasında paylaşılan canlılık zaman
    damgası. Worker her döngü turunda beat() çağırır; GUI seconds_since_beat()
    ile en son ne zaman atıldığını okur. Basit bir kilit yeterli -- tek yazar
    (worker), tek okuyucu (GUI) var."""

    def __init__(self):
        self._lock = threading.Lock()
        self._last_beat = time.monotonic()

    def beat(self):
        with self._lock:
            self._last_beat = time.monotonic()

    def seconds_since_beat(self):
        with self._lock:
            return time.monotonic() - self._last_beat


class RecentKeyDedup:
    """MON-13 (madde 109/3): son `max_size` (seq, zaman_ms) ikilisini tutar;
    aynı ikili tekrar gelirse (örn. AKS tarafında yanlışlıkla iki kez
    gönderilen bir paket) bunu bildirir -- çağıran taraf bu durumda satırı
    dosyaya İKİNCİ kez YAZMAMALIDIR. Saf/hafif -- I/O yapmaz, doğrudan test
    edilebilir."""

    def __init__(self, max_size=200):
        self._order = deque(maxlen=max_size)
        self._seen = set()

    def is_duplicate(self, key):
        """key daha önce görüldüyse True döner (ve pencereyi değiştirmez).
        Görülmediyse key'i pencereye ekler (gerekirse en eski girdiyi atarak)
        ve False döner."""
        if key in self._seen:
            return True
        if len(self._order) == self._order.maxlen:
            oldest = self._order[0]
            self._seen.discard(oldest)
        self._order.append(key)
        self._seen.add(key)
        return False


class RateLimiter:
    """MON-03: bir saniyelik kaydırmasız pencerede en fazla `max_per_sec`
    olaya izin verir; aşımları bir sonraki pencereye kadar sayar (flood
    önleme). Saf/hafif -- I/O yapmaz, `record()` çağıranı bilgilendirir."""

    def __init__(self, max_per_sec=REJECT_LOG_MAX_PER_SEC):
        self.max_per_sec = max_per_sec
        self._window = None
        self._count_in_window = 0
        self._suppressed_in_window = 0

    def record(self, now_sec):
        """Döner: (allowed, suppressed_in_prev_window). `allowed` bu olayın
        loglanabileceğini belirtir; `suppressed_in_prev_window` > 0 ise bir
        önceki pencerede bastırılan olay sayısıdır (bir kez bildirilir)."""
        window = int(now_sec)
        suppressed_prev = 0
        if window != self._window:
            suppressed_prev = self._suppressed_in_window
            self._window = window
            self._count_in_window = 0
            self._suppressed_in_window = 0

        if self._count_in_window < self.max_per_sec:
            self._count_in_window += 1
            return True, suppressed_prev

        self._suppressed_in_window += 1
        return False, suppressed_prev


def compute_worker_health_state(worker_alive, heartbeat_seconds_since_beat, restart_count,
                                 max_restarts=MAX_WORKER_RESTARTS,
                                 heartbeat_timeout_sec=WORKER_HEARTBEAT_TIMEOUT_SEC):
    """MON-01: worker canlılığına ve heartbeat tazeliğine göre GUI'nin
    "KAYIT DURDU" durumunda olup olmadığına ve yeniden başlatma gerekip
    gerekmediğine karar veren saf fonksiyon -- tkinter'e dokunmaz, doğrudan
    test edilebilir.

    Döner: {"should_restart": bool, "permanently_failed": bool,
            "recording_stopped": bool}
    """
    permanently_failed = False
    should_restart = False
    if not worker_alive:
        if restart_count < max_restarts:
            should_restart = True
        else:
            permanently_failed = True

    stale = heartbeat_seconds_since_beat > heartbeat_timeout_sec
    recording_stopped = permanently_failed or (not worker_alive) or stale

    return {
        "should_restart": should_restart,
        "permanently_failed": permanently_failed,
        "recording_stopped": recording_stopped,
    }

# R2 (9.2.e/9.2.h): grafik çizgisi, henüz replay ile doldurulmamış >5 sn'lik
# bir boşlukta KESİLİR (düz-çizgi ile "veri var" yanılsaması verilmez) —
# _refresh_interval_indicator'daki aynı 5.0 sn eşiğiyle tutarlı.
GRAPH_GAP_BREAK_SEC = 5.0


def insert_sorted_point(history, point):
    """(ts_sec, value) noktasini history'e ts_sec'e göre SIRALI ekler
    (bisect.insort) — geliş sırası yerine zaman damgasına göre; böylece geç
    gelen replay noktaları (eski ts_sec) grafik üzerinde doğru (geçmiş)
    konuma yerleşir, sona değil. Saf fonksiyon — tkinter/matplotlib'e
    dokunmaz, doğrudan test edilebilir."""
    bisect.insort(history, point)


def trim_history_window(history, window_sec):
    """SIRALI (ts_sec, value) listesinden, en yeni ts_sec'ten window_sec'ten
    daha eskiye düşen noktaları atar. Pencere en yeni BİLİNEN telemetri
    zaman damgasına göredir (duvar-saati "şimdi"ye göre DEĞİL) — bir kesinti
    sırasında telemetri ts'si ilerlemese de (offline örnekleme sürer ama
    henüz replay edilmediyse grafik güncellenmez) pencere sabit kalır;
    replay geldiğinde en yeni ts ilerler ve pencere onunla birlikte kayar."""
    if not history:
        return history
    cutoff = history[-1][0] - window_sec
    return [(t, v) for t, v in history if t >= cutoff]


def build_line_with_gaps(history, gap_threshold_sec=GRAPH_GAP_BREAK_SEC):
    """SIRALI (ts_sec, value) listesinden matplotlib çizgi verisi üretir;
    ardışık iki nokta arasındaki ts farkı gap_threshold_sec'i aşarsa aralarına
    bir (NaN, NaN) kırılma noktası eklenir — matplotlib bu noktada çizgiyi
    ÇİZMEZ (boşluk görünür kalır). Replay paketleri geldikçe insert_sorted_point
    ile araya yeni noktalar girer, sonraki çağrıda boşluk küçülür/dolar. Saf
    fonksiyon — doğrudan test edilebilir."""
    xs: list[float] = []
    ys: list[float] = []
    prev_ts = None
    for ts_sec, value in history:
        if prev_ts is not None and (ts_sec - prev_ts) > gap_threshold_sec:
            xs.append(float("nan"))
            ys.append(float("nan"))
        xs.append(ts_sec)
        ys.append(value)
        prev_ts = ts_sec
    return xs, ys


def format_timestamp_ms(timestamp_ms):
    """MON-04 (madde 48): zaman_ms'i insan-okunur "dakika:saniye.milisaniye"
    biçimine çevirir (örn. 754567 -> "12:34.567"). Saf fonksiyon -- tkinter'e
    dokunmaz, doğrudan test edilebilir."""
    total_ms = int(timestamp_ms)
    minutes, rem_ms = divmod(total_ms, 60_000)
    seconds, millis = divmod(rem_ms, 1000)
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def max_consecutive_gap_sec(sorted_timestamps_ms):
    """MON-06 (madde 67/68, 9.2.h): SIRALI (artan) zaman_ms listesindeki en
    büyük ARDIŞIK farkı saniye cinsinden döner -- varış sırasına değil,
    paketin KENDİ zaman damgasına göre. 2'den az elemanlı listede 0.0 döner.
    Saf fonksiyon -- doğrudan test edilebilir."""
    if len(sorted_timestamps_ms) < 2:
        return 0.0
    max_gap_ms = 0
    for i in range(1, len(sorted_timestamps_ms)):
        gap = sorted_timestamps_ms[i] - sorted_timestamps_ms[i - 1]
        if gap > max_gap_ms:
            max_gap_ms = gap
    return max_gap_ms / 1000.0


def compute_stale_display(last_packet_time, now, stale_threshold_sec):
    """MON-05 (madde 49): son geçerli satırdan bu yana geçen süreye göre
    kartların "bayat" sayılıp sayılmayacağına ve gösterilecek alt yazı
    mesajına karar veren saf fonksiyon -- tkinter'e dokunmaz, doğrudan test
    edilebilir. Döner: (is_stale: bool, message: str)."""
    if last_packet_time is None:
        return True, ""
    elapsed = now - last_packet_time
    if elapsed > stale_threshold_sec:
        return True, f"son veri: {elapsed:.1f} sn önce"
    return False, ""


def truncate_path_for_display(path, max_len=60):
    """MON-08 (madde 108): uzun bir dosya yolunu ORTADAN kısaltır (...) --
    başı (sürücü/üst klasörler) ve sonu (dosya adı) korunur, böylece hem
    bağlam hem de tam dosya adı görünür kalır. Saf fonksiyon -- doğrudan
    test edilebilir."""
    if len(path) <= max_len:
        return path
    keep = max(max_len - 3, 0)
    head_len = keep // 2
    tail_len = keep - head_len
    return path[:head_len] + "..." + path[-tail_len:]


def _simulate_suffix():
    """SIMULATE modunda sahte veri dosyalarını gerçek kayıttan ayırmak için
    dosya adı eki. Tek noktadan çözülür: open_log_file/open_events_log
    çağıran her yer (ilk açılış ve yeni-boot sonrası yeniden açılış dahil)
    bunu otomatik alır."""
    return "_SIM" if config.SERIAL_PORT == "SIMULATE" else ""


# MON-14 (madde 85): bilinen bulut senkron klasörü adları -- kayıt sırasında
# dosya kilidi/senkron gecikmesi riski taşırlar (bkz. detect_cloud_sync_folder).
CLOUD_SYNC_FOLDER_MARKERS = ("OneDrive", "Dropbox", "Google Drive", "iCloud")


def detect_cloud_sync_folder(path):
    """MON-14 (madde 85): verilen yolun (mutlak hali) bilinen bir bulut
    senkron klasörü İÇİNDE olup olmadığını kontrol eder. Eşleşirse servis
    adını, eşleşmezse None döner. Saf fonksiyon -- doğrudan test edilebilir,
    hiçbir şeyi ENGELLEMEZ (yalnız çağıran tarafın uyarı basmasını sağlar)."""
    abs_path = os.path.abspath(path)
    for marker in CLOUD_SYNC_FOLDER_MARKERS:
        if marker.lower() in abs_path.lower():
            return marker
    return None


def check_output_dir_writable(directory):
    """MON-12 (madde 84): config.OUTPUT_DIR gerçekten YAZILABİLİR mi diye
    açılışta kontrol eder. Klasör oluşturulamıyorsa veya içine yazılamıyorsa
    (izin/disk sorunu) net bir mesajla RuntimeError fırlatır -- ÇAĞIRAN TARAF
    (main) bunu yakalayıp uygulamayı BAŞLATMADAN net bir hatayla sonlandırır;
    sessizce başka bir klasöre yazmaya ASLA düşülmez."""
    try:
        os.makedirs(directory, exist_ok=True)
        probe_path = os.path.join(directory, ".tufan_write_test")
        with open(probe_path, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe_path)
    except OSError as exc:
        raise RuntimeError(
            f"Kayıt klasörüne yazılamıyor: {os.path.abspath(directory)} ({exc}). "
            "config.py içindeki OUTPUT_DIR ayarını kontrol edin."
        ) from exc


def find_previous_session_file(directory, suffix=""):
    """MON-16 (madde 66): `directory` içindeki en son DEĞİŞTİRİLMİŞ
    telem_*.csv dosyasını bulur -- bu fonksiyon mevcut oturumun dosyası
    AÇILMADAN ÖNCE çağrılmalıdır, aksi halde yeni dosya kendi kendini
    "önceki oturum" sanabilir. `suffix` (örn. "_SIM") verilirse yalnız o
    eki taşıyan dosyalar aranır; verilmezse (varsayılan "") yalnız _SIM
    TAŞIMAYAN dosyalar aranır -- SIMULATE ve gerçek kayıtlar birbirini asla
    "önceki oturum" saymaz. Hiç aday yoksa (veya klasör yoksa) None döner."""
    if not os.path.isdir(directory):
        return None
    candidates = []
    for name in os.listdir(directory):
        if not (name.startswith("telem_") and name.endswith(".csv")):
            continue
        if bool(suffix) != ("_SIM" in name):
            continue
        candidates.append(os.path.join(directory, name))
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def read_last_timestamp_ms(filepath):
    """Bir telem_*.csv dosyasının SON (geçerli) satırındaki zaman_ms
    değerini okur. Dosya boş/yalnız başlıksa, okunamıyorsa veya son satır
    bozuksa None döner -- hiçbir zaman exception fırlatmaz, çağıran taraf
    (serial_worker) bunu "önceki oturum bilgisi yok" olarak ele alır."""
    try:
        with open(filepath, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if not line.strip() or line == HEADER:
            continue
        try:
            return int(line.split(";")[0])
        except (ValueError, IndexError):
            return None
    return None


def open_log_file():
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    filename = make_log_filename(suffix=_simulate_suffix())
    # "x" (exclusive create): make_log_filename zaten diskteki isim
    # çakışmalarını sayaç ekiyle önler; bu sadece savunma katmanıdır --
    # isim üretimi bir şekilde bozulup mevcut bir dosyayla çakışırsa "w"
    # gibi sessizce sıfırlamak (truncate) yerine FileExistsError fırlatır.
    f = open(filename, "x", encoding="utf-8")
    f.write(HEADER + "\n")
    f.flush()
    return f, filename


def open_events_log():
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    filename = make_events_log_filename(suffix=_simulate_suffix())
    return open(filename, "a", encoding="utf-8")


def open_backup_log_file(primary_filename):
    """MON-02 (madde 20): config.BACKUP_OUTPUT_DIR ayarlıysa birincil ile
    AYNI dosya adında ikincil bir kopya açar (örn. bir USB bellek yolu).

    Döner: (file, path) başarıda; (None, None) BACKUP_OUTPUT_DIR
    ayarlanmamışsa (yedek özelliği kapalı); (None, hata_mesaji) yedek
    açma HATA verirse. Bu fonksiyon hiçbir zaman exception fırlatmaz --
    çağıran taraf (serial_worker) birincil kaydı bu sonuçtan bağımsız
    sürdürür."""
    if not config.BACKUP_OUTPUT_DIR:
        return None, None
    try:
        os.makedirs(config.BACKUP_OUTPUT_DIR, exist_ok=True)
        backup_path = os.path.join(config.BACKUP_OUTPUT_DIR, os.path.basename(primary_filename))
        f = open(backup_path, "x", encoding="utf-8")
        f.write(HEADER + "\n")
        f.flush()
        return f, backup_path
    except Exception as exc:
        return None, str(exc)


class MockSerial:
    """SIMULATE modu için sahte AKS seri veri kaynağı.

    R2 (9.2.e/9.2.h): periyodik olarak GERÇEKÇİ bir kesinti + replay
    senaryosu üretir — LINK,DOWN -> OUTAGE_TICKS sn boyunca hiçbir satır
    gelmez (b"" — gerçek RF kesintisinde olduğu gibi) -> LINK,UP -> geriye
    dönük (backdated) ts'li bir replay burst'ü -> canlıya dönüş. Böylece
    Monitor'un sıralı-ekleme + boşluk-kırma (gap-break) grafik davranışı
    `python monitor.py` (SIMULATE modu, gerçek COM portu gerektirmez) ile
    gözle görülür şekilde denenebilir (bkz. Documents/LoRa_Link_Analysis.md
    "Bench Prosedürü").
    """

    OUTAGE_EVERY_N_TICKS = 40
    OUTAGE_TICKS = 8  # ~8 sn'lik kesinti (LINK_TIMEOUT_MS'e yakın, kısa demo)

    def __init__(self):
        self.seq = 0
        self.start_time = time.time()
        self.speed = 50.0
        self.soc = 90.0
        self.temp = 35
        self._tick = 0
        self._mode = "live"  # "live" | "offline" | "draining"
        self._offline_backlog = []  # (elapsed_ms, speed, temp, soc, seq)

    def _advance_sensors(self):
        import random
        self.speed += random.uniform(-3, 3)
        self.speed = max(0, min(config.MAX_SPEED_KMH - 20, self.speed))

        self.temp += random.choice([-1, 0, 1])
        self.temp = max(20, min(65, self.temp))

        self.soc -= 0.05
        self.soc = max(0, self.soc)

    def _build_csv_line(self, elapsed_ms, speed, temp, soc, seq):
        voltage = 45.0 + (soc / 100.0) * 8.0
        speed_x10 = int(speed * 10)
        voltage_deciv = int(voltage * 10)
        soc_hundredths = int(soc * 100)
        return f"CSV,{elapsed_ms},{speed_x10},{temp},{voltage_deciv},{soc_hundredths},{seq}\r\n".encode("utf-8")

    def readline(self):
        time.sleep(1.0)  # 1 saniye aralıklarla veri simüle et
        self._tick += 1

        if self._mode == "draining" and self._offline_backlog:
            r_ms, r_speed, r_temp, r_soc, r_seq = self._offline_backlog.pop(0)
            if not self._offline_backlog:
                self._mode = "live"
            return self._build_csv_line(r_ms, r_speed, r_temp, r_soc, r_seq)

        elapsed_ms = int((time.time() - self.start_time) * 1000)
        self._advance_sensors()

        if self._mode == "live":
            if self._tick % self.OUTAGE_EVERY_N_TICKS == 0:
                self._mode = "offline"
                self._offline_backlog = []
                return b"LINK,DOWN\r\n"
            self.seq += 1
            return self._build_csv_line(elapsed_ms, self.speed, self.temp, self.soc, self.seq)

        # self._mode == "offline": RF fiilen kesik — canlı TX YOK (readline
        # boş döner, gerçek bir kablo/menzil kopmasında olduğu gibi), ama
        # AKS yerel olarak (offline buffer) örneklemeye devam eder.
        self.seq += 1
        self._offline_backlog.append((elapsed_ms, self.speed, self.temp, self.soc, self.seq))
        if len(self._offline_backlog) >= self.OUTAGE_TICKS:
            self._mode = "draining"
            return b"LINK,UP\r\n"
        return b""

    def close(self):
        pass


def open_serial_connection():
    """config.SERIAL_PORT'u açar. MON-09 (madde 86): açılamazsa VE
    config.AUTO_DISCOVER_PORT True ise, sistemde görünen diğer portları
    sırayla dener ve ilk açılabileni döndürür (config.SERIAL_PORT
    DEĞİŞTİRİLMEZ -- yalnız bu bağlantı için kullanılır; hangi porta
    bağlanıldığı çağıran tarafta `ser.port` üzerinden okunabilir). Hiçbiri
    açılamazsa orijinal SerialException'ı fırlatır."""
    if config.SERIAL_PORT == "SIMULATE":
        return MockSerial()
    try:
        return serial.Serial(config.SERIAL_PORT, config.SERIAL_BAUD, timeout=2)
    except serial.SerialException as primary_exc:
        if not config.AUTO_DISCOVER_PORT:
            raise
        for candidate in list_available_ports():
            if candidate == config.SERIAL_PORT:
                continue  # zaten denendi, tekrar deneme
            try:
                return serial.Serial(candidate, config.SERIAL_BAUD, timeout=2)
            except serial.SerialException:
                continue
        raise primary_exc


def serial_worker(data_queue, stop_event, connect=open_serial_connection,
                   reconnect_interval=RECONNECT_INTERVAL_SEC, heartbeat=None,
                   restart_attempt=0):
    """Seri portu okur, CSV'ye yazar ve ayrıştırılmış verileri kuyruğa koyar.

    Bu fonksiyon ayrı bir thread'de çalışır; CSV dosyaya yazma işlemi
    burada kalır, GUI sadece data_queue üzerinden veri okur.

    9.2.g: port koparsa (USB çekilmesi vb.) uygulama ÇIKMAZ — açık log dosyası
    öyle kalır, `reconnect_interval` saniyede bir yeniden bağlanma denenir.
    Yeniden bağlanma AYNI dosyada devam eder; yeni dosyaya geçiş yalnızca
    `detect_new_boot` seq üzerinden gerçek bir yeni boot tespit ettiğinde olur
    (bkz. csv_logger.detect_new_boot) — bağlantı kopması tek başına yeni dosya
    açtırmaz.

    MON-01 (madde 19): fonksiyonun TAMAMI (dosya açma dahil) geniş bir
    try/except/finally ile sarılıdır — hiçbir hata thread'i SESSİZCE
    öldüremez; her beklenmeyen istisna TAM traceback ile events log'a
    yazılır (mümkünse) ve data_queue'ya "worker_crashed" mesajı konur. GUI
    tarafı bu mesajı ve `heartbeat` tazeliğini izleyerek "KAYIT DURDU"
    durumuna geçer, gerekirse worker'ı yeniden başlatır (bkz.
    compute_worker_health_state, MonitorApp._start_worker_thread).
    """
    if heartbeat is None:
        heartbeat = WorkerHeartbeat()

    log_file = None
    filename = None
    events_file = None
    backup_file = None
    ser = None
    prev_seq = None
    prev_ts_ms = None
    reject_limiters = {"parse_hatasi": RateLimiter(), "aralik_hatasi": RateLimiter()}
    dedup_tracker = RecentKeyDedup(max_size=200)
    dedup_count = 0

    def safe_log_event(message):
        if events_file is None:
            return
        try:
            events_file.write(format_event_line(message) + "\n")
            events_file.flush()
        except Exception:
            pass

    def log_rejected_line(reason, raw_line):
        # MON-03: sayaç GUI'ye her zaman gider (flood korumasından bağımsız);
        # yalnızca DİSKE yazılan ham satır sayısı saniyede
        # REJECT_LOG_MAX_PER_SEC ile sınırlanır.
        data_queue.put({"type": "reject", "reason": reason})
        limiter = reject_limiters[reason]
        allowed, suppressed_prev = limiter.record(time.monotonic())
        if suppressed_prev:
            safe_log_event(
                f"REDDEDILEN SATIR ({reason}): onceki saniyede {suppressed_prev} "
                f"satir daha bastirildi (saniyede en fazla {REJECT_LOG_MAX_PER_SEC} loglanir)"
            )
        if allowed:
            safe_log_event(f"REDDEDILEN SATIR ({reason}): {raw_line}")

    def open_backup(primary_filename):
        # MON-02: yalnız config.BACKUP_OUTPUT_DIR ayarlıyken data_queue'ya
        # "backup_status" gönderilir -- ayarlanmamışken (varsayılan) hiçbir
        # ek mesaj üretilmez, mevcut davranış/testler etkilenmez.
        nonlocal backup_file
        if not config.BACKUP_OUTPUT_DIR:
            backup_file = None
            return
        f, result = open_backup_log_file(primary_filename)
        if f is not None:
            backup_file = f
            data_queue.put({"type": "backup_status", "active": True, "path": result})
        else:
            backup_file = None
            safe_log_event(f"YEDEK KAYIT ACILAMADI: {result}")
            data_queue.put({"type": "backup_status", "active": False, "detail": result})

    def close_backup():
        nonlocal backup_file
        if backup_file is not None:
            try:
                backup_file.flush()
                backup_file.close()
            except Exception:
                pass
            backup_file = None

    try:
        # MON-16 (madde 66): yeni dosya AÇILMADAN ÖNCE önceki oturumun
        # dosyasını (varsa) bul -- aksi halde yeni dosya kendini "önceki
        # oturum" sanabilir. restart_attempt > 0 iken de aynı mantık geçerli
        # (bir önceki BAŞARISIZ worker'ın dosyası da "önceki oturum" sayılır).
        previous_session_file = find_previous_session_file(config.OUTPUT_DIR, suffix=_simulate_suffix())
        previous_session_last_ts_ms = (
            read_last_timestamp_ms(previous_session_file) if previous_session_file else None
        )

        log_file, filename = open_log_file()
        data_queue.put({"type": "filename", "name": filename})
        events_file = open_events_log()
        if restart_attempt > 0:
            safe_log_event(
                f"WORKER YENIDEN BASLATILDI (deneme {restart_attempt}/{MAX_WORKER_RESTARTS})"
            )
            print(f"Worker yeniden başlatıldı (deneme {restart_attempt}/{MAX_WORKER_RESTARTS})")
        open_backup(filename)
        # MON-12 (madde 84): kullanılan TAM kayıt klasörü yolu açılışta
        # events log'a da düşülür (konsola main() içinde zaten yazılıyor).
        safe_log_event(f"Kayıt klasörü: {os.path.abspath(config.OUTPUT_DIR)}")

        # MON-14 (madde 85): kayıt klasörü bir bulut senkron klasöründeyse
        # (OneDrive/Dropbox/Google Drive/iCloud) UYARIR ama ENGELLEMEZ.
        cloud_marker = detect_cloud_sync_folder(config.OUTPUT_DIR)
        if cloud_marker:
            safe_log_event(
                f"UYARI: Kayit klasoru bir bulut senkron klasorunde ({cloud_marker}). "
                "Yaris oncesi senkronizasyonu duraklatin veya OUTPUT_DIR'i degistirin."
            )
            data_queue.put({"type": "cloud_sync_warning", "service": cloud_marker})

        if previous_session_file is not None:
            if previous_session_last_ts_ms is not None:
                safe_log_event(
                    f"Onceki oturum {os.path.basename(previous_session_file)} ile sona ermisti, "
                    f"son zaman_ms: {previous_session_last_ts_ms}"
                )
                data_queue.put(
                    {"type": "previous_session", "last_ts_ms": previous_session_last_ts_ms}
                )
            else:
                safe_log_event(
                    f"Onceki oturum dosyasi bulundu ama okunamadi/bos: "
                    f"{os.path.basename(previous_session_file)}"
                )

        print("TUFAN İzleme Merkezi başlatıldı")
        if not config.CONFIG_CONFIRMED:
            print(
                "UYARI: BATARYA KAPASITESI TEYITSIZ — kalan_enerji_Wh kolonu "
                "gecersiz (config.py: CONFIG_CONFIRMED=False)"
            )
        if config.SERIAL_PORT == "SIMULATE":
            print(
                "UYARI: SIMULATE modu aktif — uretilen tum veri SAHTEDIR, "
                "gercek kayit icin config.py SERIAL_PORT'u gercek COM portuna cevirin "
                "veya --port COMx ile calistirin"
            )

        # MON-09 (madde 86): açılışta mevcut seri portları listele ve events
        # log'a yaz -- teknik kontrolde "hangi portlar görünüyor?" sorusuna
        # canlı donanıma dokunmadan (9.4.a.vii: müdahale yasak) cevap verilsin.
        safe_log_event("Baslangicta " + format_port_list_message(list_available_ports()).lower())

        while not stop_event.is_set():
            heartbeat.beat()

            if ser is None:
                try:
                    ser = connect()
                except serial.SerialException:
                    ser = None

                if ser is None:
                    available = list_available_ports()
                    data_queue.put(
                        {"type": "port_down", "ts": time.monotonic(), "available_ports": available}
                    )
                    safe_log_event(
                        f"SERI PORT BULUNAMADI: {config.SERIAL_PORT} acilamadi. "
                        f"{format_port_list_message(available)} "
                        f"{reconnect_interval:.0f} sn sonra tekrar denenecek"
                    )
                    if stop_event.wait(reconnect_interval):
                        break
                    continue

                # MON-09: gerçekte bağlanılan port config.SERIAL_PORT'tan
                # FARKLI olabilir (open_serial_connection otomatik keşifle
                # başka bir porta düşmüş olabilir) -- ser.port varsa (gerçek
                # serial.Serial) onu, yoksa (test sahteleri/MockSerial)
                # config.SERIAL_PORT'u kullan.
                actual_port = getattr(ser, "port", config.SERIAL_PORT)
                data_queue.put({"type": "port_up", "ts": time.monotonic(), "port": actual_port})
                if actual_port != config.SERIAL_PORT:
                    safe_log_event(
                        f"SERI PORT OTOMATIK BULUNDU: {config.SERIAL_PORT} acilamadi, "
                        f"onun yerine {actual_port} kullanildi | Dosya: {filename}"
                    )
                    print(f"UYARI: {config.SERIAL_PORT} acilamadi, otomatik olarak {actual_port} kullaniliyor")
                else:
                    safe_log_event(f"SERI PORT BAGLANDI: {actual_port} | Dosya: {filename}")
                    print(f"Port: {actual_port} | Dosya: {filename}")

            try:
                raw = ser.readline()
            except (serial.SerialException, OSError) as exc:
                safe_log_event(f"SERI PORT KOPTU: {exc}")
                data_queue.put({"type": "port_down", "ts": time.monotonic()})
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
                continue

            if not raw:
                continue

            # errors="ignore" hicbir zaman UnicodeDecodeError firlatmaz,
            # bu yuzden try/except gerekmez.
            line = raw.decode("utf-8", errors="ignore").strip()

            if not line:
                continue

            if line.startswith("CSV,"):
                parsed, reject_reason = parse_csv_line_verbose(line)
                if parsed is None:
                    # MON-03 (madde 50/69): bozuk VEYA aralık-dışı satır
                    # sessizce atılmaz -- sayılır ve (rate-limitli) ham
                    # haliyle events log'a düşülür; jüri dosyasına YAZILMAZ.
                    if reject_reason is not None:
                        log_rejected_line(reject_reason, line)
                    continue

                curr_seq = parsed["seq"]
                curr_ts_ms = parsed["timestamp_ms"]

                # MON-16 (madde 66): bu worker'ın işlediği İLK satır (prev_seq
                # hâlâ None) ve önceki oturumdan bir son zaman_ms biliniyorsa
                # -- yer istasyonu (bu uygulama) yeniden başlamış olabilir,
                # AKS ise (muhtemelen) kesintisiz devam etmiştir. Fark > 0 ise
                # (ts_ms gerçekten ilerlemiş, yeni bir AKS boot'u değilse) bu
                # AKS TARAFINDA TAMPONLANMAMIŞ bir boşluktur -- gizlenmez.
                if prev_seq is None and previous_session_last_ts_ms is not None:
                    station_gap_sec = (curr_ts_ms - previous_session_last_ts_ms) / 1000.0
                    if station_gap_sec > 0:
                        safe_log_event(
                            f"YER ISTASYONU KESINTISI: {station_gap_sec:.1f} sn veri kaybi "
                            "(bu bosluk AKS tarafinda TAMPONLANMADI)"
                        )
                        data_queue.put({"type": "station_gap", "gap_sec": station_gap_sec})

                # MON-13 (madde 109/3): aynı (seq, zaman_ms) ikilisi son 200
                # satır içinde daha önce görüldüyse bu bir tekrar (dedup) --
                # dosyaya İKİNCİ kez yazılmaz, prev_seq/prev_ts_ms de
                # ETKİLENMEZ (sanki bu satır hiç gelmemiş gibi davranılır).
                if dedup_tracker.is_duplicate((curr_seq, curr_ts_ms)):
                    dedup_count += 1
                    data_queue.put({"type": "dedup", "count": dedup_count})
                    continue

                if detect_new_boot(prev_seq, curr_seq):
                    # MON-13 (madde 109/2): ÖNCE yeni dosyayı aç, YALNIZ
                    # başarılı olursa eskisini kapat (sıra bilerek ters
                    # çevrildi). Eski sırada (önce kapat, sonra aç) açma
                    # başarısız olursa `log_file` zaten KAPALI bir dosyaya
                    # işaret ediyordu -- finally bloğu bunu flush etmeye
                    # çalışıp İKİNCİ bir exception fırlatıyordu. Bu sırada
                    # açma başarısız olursa `log_file` hâlâ ESKİ (geçerli,
                    # açık) dosyayı gösterir -- veri kaybı olmadan ESKİ
                    # dosyada kayda devam edilir.
                    try:
                        new_log_file, new_filename = open_log_file()
                    except OSError as exc:
                        safe_log_event(
                            f"YENI BOOT: yeni dosya acilamadi, ESKI dosyada devam ediliyor: {exc}"
                        )
                    else:
                        old_log_file = log_file
                        log_file, filename = new_log_file, new_filename
                        try:
                            old_log_file.flush()
                            old_log_file.close()
                        except Exception:
                            pass
                        close_backup()
                        data_queue.put({"type": "filename", "name": filename})
                        open_backup(filename)
                        safe_log_event(f"YENI BOOT tespit edildi -> {filename}")
                        print(f"YENİ BOOT tespit edildi → {filename}")
                    # R2: yeni boot -> ts_ms sıfırdan başlar; GUI grafiği
                    # (ts_ms tabanlı x ekseni) eski boot'un yüksek ts'leriyle
                    # karışmasın diye temizlenmeli -- dosya rotasyonu
                    # başarısız olsa bile (AKS gerçekten yeniden boot etti).
                    data_queue.put({"type": "new_boot"})
                elif is_replay_ts(prev_ts_ms, curr_ts_ms):
                    # 9.2.e: AKS offline-buffer drenajı sırasında replay edilen
                    # paketler eski ts taşır ama seq artmaya devam eder (bkz.
                    # csv_logger.detect_new_boot) — CSV satırı normal şekilde
                    # yazılır, bu yalnızca teşhis/jüri için events log notudur.
                    safe_log_event(
                        f"REPLAY? ts_ms geriye gitti: {prev_ts_ms} -> {curr_ts_ms} "
                        f"(seq {prev_seq} -> {curr_seq})"
                    )

                prev_seq = curr_seq
                prev_ts_ms = curr_ts_ms

                # R2 KARAR: CSV satırları GELİŞ SIRASIYLA yazılmaya devam
                # eder (replay ts'si eski olsa bile dosyada sonra görünür) —
                # şartname (9.2.e/9.2.h) satırların dosyada ts_ms'e göre
                # SIRALI olmasını ŞART KOŞMUYOR, yalnızca kesinti aralığının
                # ts damgalarıyla (herhangi bir sırada) MEVCUT olmasını
                # istiyor. Sıra bilgisi zaten zaman damgasının (ts_ms, ilk
                # kolon) kendisinde var — jüri/analiz sort ederek okuyabilir.
                # Grafik (GUI) ise ts_sec'e göre sıralı ekler (bkz.
                # insert_sorted_point) çünkü ORADA çizgi süreklidiği için
                # geliş sırası YETERSİZ kalır; CSV dosyası için bu gerekçe
                # geçerli değil.
                record = format_record(parsed, config.BATTERY_CAPACITY_WH)
                log_file.write(record + "\n")
                # 9.2.g: kayıt kanıt niteliğindedir — çökme anında veri
                # kaybı kabul edilemez, bu yüzden her satırda hemen
                # flush + fsync (OS sayfa önbelleğini de aşıp diske yazar).
                log_file.flush()
                os.fsync(log_file.fileno())

                if backup_file is not None:
                    # MON-02: ikincil yazma birincilden TAMAMEN ayrı bir
                    # try/except içindedir -- burada oluşan HİÇBİR hata
                    # birincil kaydı etkilemez. flush yapılır ama fsync
                    # YAPILMAZ (USB'de yavaş olabilir, madde 20).
                    try:
                        backup_file.write(record + "\n")
                        backup_file.flush()
                    except Exception as exc:
                        safe_log_event(f"YEDEK KAYIT YAZMA HATASI: {exc}")
                        data_queue.put(
                            {"type": "backup_status", "active": False, "detail": str(exc)}
                        )
                        close_backup()

                data_queue.put(
                    {
                        "type": "csv",
                        "ts": time.monotonic(),
                        # R2: paketin KENDİ zaman damgası (AKS ts_ms, sn) —
                        # grafik x ekseni artık VARIŞ sırası/zamanı yerine bunu
                        # kullanır (geç gelen replay noktaları doğru yere
                        # yerleşsin diye). "ts" (yukarıda, wall-clock varış)
                        # yalnız bağlantı sağlık göstergeleri için kalır.
                        "ts_sec": curr_ts_ms / 1000.0,
                        # MON-04/06: paketin HAM zaman_ms değeri -- ZAMAN
                        # kartında ve maks. ardışık zaman farkı hesabında
                        # kullanılır (ts_sec zaten bunun /1000'i, ama tam
                        # tamsayı hassasiyeti için ayrıca taşınır).
                        "timestamp_ms": curr_ts_ms,
                        "speed_kmh": parsed["speed_kmh_x10"] / 10,
                        "temp_c": parsed["temp_c"],
                        "voltage_v": parsed["pack_voltage_deciv"] / 10,
                        "soc_percent": parsed["soc_hundredths"] / 100,
                        "energy_wh": round(
                            parsed["soc_hundredths"] / 10000 * config.BATTERY_CAPACITY_WH
                        ),
                    }
                )

            elif line.startswith("LINK,DOWN"):
                # 9.2.f şeması 5 kolonludur — LINK satırları CSV'ye YAZILMAZ,
                # yalnızca GUI'ye ve events log'a bildirilir.
                data_queue.put({"type": "link_down", "ts": time.monotonic()})
                safe_log_event("LINK,DOWN alindi")

            elif line.startswith("LINK,UP"):
                data_queue.put({"type": "link_up", "ts": time.monotonic()})
                safe_log_event("LINK,UP alindi")

            else:
                continue

    except Exception:
        tb = traceback.format_exc()
        safe_log_event("WORKER CRASH (beklenmeyen hata):\n" + tb)
        print(f"KAYIT DURDU - worker beklenmeyen hata ile sonlandi:\n{tb}")
        data_queue.put({"type": "worker_crashed", "traceback": tb})
    finally:
        heartbeat.beat()
        if log_file is not None:
            try:
                log_file.flush()
                log_file.close()
            except Exception:
                pass
        close_backup()
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
        safe_log_event("Izleme durduruldu")
        if events_file is not None:
            try:
                events_file.flush()
                events_file.close()
            except Exception:
                pass
        if filename is not None:
            print(f"İzleme durduruldu. Dosya kaydedildi: {filename}")


def list_available_ports():
    """Sistemde gorunen seri portlarin device adlarini dondurur (pyserial
    list_ports sarmalayicisi); gercek donanim gerektirdiginden ayri bir
    fonksiyonda tutulur ki cagiran taraf (main) testte kolayca mock'layabilsin."""
    return [p.device for p in list_ports.comports()]


def format_port_list_message(ports):
    """list_available_ports() ciktisini konsola basilacak insan-okunur bir
    metne cevirir; saf fonksiyon oldugu icin gercek donanim/pyserial
    olmadan test edilebilir."""
    if not ports:
        return "Sistemde seri port bulunamadi."
    return "Bulunan seri portlar: " + ", ".join(ports)


def format_headless_status_line(packet_count, last_ts_ms, max_gap_sec, port_connected, link_connected):
    """MON-10 (madde 87): headless moddaki periyodik konsol durum satırını
    üretir -- saf fonksiyon, tkinter/gerçek worker olmadan test edilebilir."""
    if not port_connected:
        durum = "SERİ PORT KOPUK"
    elif not link_connected:
        durum = "KOPUK"
    else:
        durum = "BAĞLI"
    last_ts_text = "--" if last_ts_ms is None else str(last_ts_ms)
    return (
        f"[headless] satır: {packet_count} | son zaman_ms: {last_ts_text} "
        f"| maks ardışık fark: {max_gap_sec:.1f} sn | durum: {durum}"
    )


def run_headless(status_interval_sec=5.0):
    """MON-10 (madde 87): tkinter/matplotlib HİÇ import edilmeden çalışan
    konsol kayıt modu -- 9.2.g'nin "kaydı gösterememe" değil "kaydı hiç
    TUTAMAMA" riskine karşı son çare (grafik sürücüsü sorunlu bir laptopta
    bile kayıt kesilmeden devam etsin). GUI'nin worker canlılık izleme +
    otomatik yeniden başlatma mantığının (MON-01) sade bir konsol
    karşılığıdır; MON-02 yedek kayıt yolu da (serial_worker içinde
    olduğundan) burada AYNEN çalışır.

    Ctrl+C ile durdurulur (KeyboardInterrupt) -- açık dosya flush edilip
    kapatılır (bkz. serial_worker'ın finally bloğu).
    """
    data_queue = queue.Queue()
    stop_event = threading.Event()
    heartbeat = WorkerHeartbeat()
    restart_count = 0

    def start_worker(restart_attempt=0):
        heartbeat.beat()
        t = threading.Thread(
            target=serial_worker,
            args=(data_queue, stop_event),
            kwargs={"heartbeat": heartbeat, "restart_attempt": restart_attempt},
            daemon=True,
        )
        t.start()
        return t

    worker_thread = start_worker()

    packet_count = 0
    last_ts_ms = None
    all_timestamps_ms = []
    port_connected = True
    link_connected = False
    permanently_failed = False
    last_status_print = time.monotonic()

    print("Headless (GUI'siz) mod aktif -- tkinter/matplotlib YÜKLENMEDİ.")

    try:
        while not stop_event.is_set():
            try:
                msg = data_queue.get(timeout=0.5)
            except queue.Empty:
                msg = None

            if msg is not None:
                msg_type = msg["type"]
                if msg_type == "csv":
                    packet_count += 1
                    last_ts_ms = msg["timestamp_ms"]
                    bisect.insort(all_timestamps_ms, last_ts_ms)
                    link_connected = True
                elif msg_type == "link_down":
                    link_connected = False
                elif msg_type == "link_up":
                    link_connected = True
                elif msg_type == "port_down":
                    port_connected = False
                    link_connected = False
                elif msg_type == "port_up":
                    port_connected = True
                elif msg_type == "new_boot":
                    all_timestamps_ms.clear()
                elif msg_type == "previous_session":
                    bisect.insort(all_timestamps_ms, msg["last_ts_ms"])

            if not permanently_failed:
                state = compute_worker_health_state(
                    worker_thread.is_alive(), heartbeat.seconds_since_beat(), restart_count
                )
                if state["should_restart"]:
                    restart_count += 1
                    print(
                        f"UYARI: worker öldü, yeniden başlatılıyor "
                        f"(deneme {restart_count}/{MAX_WORKER_RESTARTS})"
                    )
                    worker_thread = start_worker(restart_attempt=restart_count)
                elif state["permanently_failed"]:
                    permanently_failed = True
                    print(
                        f"KAYIT DURDU: worker {MAX_WORKER_RESTARTS} denemeden sonra da "
                        "başlatılamadı."
                    )

            now = time.monotonic()
            if now - last_status_print >= status_interval_sec:
                last_status_print = now
                gap_sec = max_consecutive_gap_sec(all_timestamps_ms)
                print(
                    format_headless_status_line(
                        packet_count, last_ts_ms, gap_sec, port_connected, link_connected
                    )
                )
    except KeyboardInterrupt:
        print("Ctrl+C alındı, kapatılıyor...")
    finally:
        stop_event.set()
        worker_thread.join(timeout=5.0)
