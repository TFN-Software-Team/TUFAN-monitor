"""CSV ayrıştırma ve dosya adı üretimi - saf fonksiyonlar, donanım bağımlılığı yok."""

import os
from datetime import datetime

import config

HEADER = "zaman_ms;hiz_kmh;T_bat_C;V_bat_C;kalan_enerji_Wh"


def _field_out_of_range(parsed: dict) -> str | None:
    """MON-03 (madde 50/69): parsed alanları config.py'deki aralık kapılarıyla
    kontrol eder; ilk aralık-dışı alanın adını döner, hepsi aralık içindeyse
    None döner. Ham (x10/x100) alanlar burada gerçek birimlere çevrilip
    karşılaştırılır (format_record ile aynı çevrim)."""
    if parsed["timestamp_ms"] < config.MIN_TIMESTAMP_MS:
        return "zaman_ms"

    speed_kmh = parsed["speed_kmh_x10"] / 10
    if not (config.MIN_SPEED_KMH <= speed_kmh <= config.MAX_SPEED_KMH):
        return "hiz_kmh"

    if not (config.MIN_TEMP_C <= parsed["temp_c"] <= config.MAX_TEMP_C):
        return "T_bat_C"

    voltage_v = parsed["pack_voltage_deciv"] / 10
    if not (config.MIN_VOLTAGE_V <= voltage_v <= config.MAX_VOLTAGE_V):
        return "V_bat_V"

    soc_percent = parsed["soc_hundredths"] / 100
    if not (config.MIN_SOC_PERCENT <= soc_percent <= config.MAX_SOC_PERCENT):
        return "SoC"

    return None


def parse_csv_line_verbose(line: str) -> tuple[dict | None, str | None]:
    """"CSV," ile başlayan bir telemetri satırını ayrıştırır ve reddedilirse
    SEBEBİNİ de döner: (parsed, None) başarıda; (None, "parse_hatasi") alan
    sayısı/sayısal biçim hatasında; (None, "aralik_hatasi") sayısal ama
    config.py'deki aralık kapılarından birini aşan bir alanda. Satır "CSV,"
    ile başlamıyorsa (None, None) döner -- bu bir red sayılmaz, çağıran taraf
    zaten bu fonksiyonu yalnız "CSV," öneki doğrulandıktan sonra çağırır."""
    line = line.strip()
    if not line.startswith("CSV,"):
        return None, None

    fields = line.split(",")
    if len(fields) != 7:
        return None, "parse_hatasi"

    try:
        parsed = {
            "timestamp_ms": int(fields[1]),
            "speed_kmh_x10": int(fields[2]),
            "temp_c": int(fields[3]),
            "pack_voltage_deciv": int(fields[4]),
            "soc_hundredths": int(fields[5]),
            "seq": int(fields[6]),
        }
    except ValueError:
        return None, "parse_hatasi"

    if _field_out_of_range(parsed) is not None:
        return None, "aralik_hatasi"

    return parsed, None


def parse_csv_line(line: str) -> dict | None:
    """"CSV," ile başlayan bir telemetri satırını ayrıştırır. Geriye dönük
    uyumluluk için korunur (bkz. parse_csv_line_verbose) -- sebep bilgisi
    olmadan yalnız parsed/None döner."""
    parsed, _reason = parse_csv_line_verbose(line)
    return parsed


def format_record(parsed: dict, battery_capacity_wh: float) -> str:
    """Ayrıştırılmış bir kaydı yönetmelik formatında CSV satırına dönüştürür."""
    timestamp_ms = parsed["timestamp_ms"]
    hiz_kmh = parsed["speed_kmh_x10"] / 10
    t_bat_c = parsed["temp_c"]
    v_bat_v = parsed["pack_voltage_deciv"] / 10
    kalan_enerji_wh = round(parsed["soc_hundredths"] / 10000 * battery_capacity_wh)

    return f"{timestamp_ms};{hiz_kmh:.1f};{t_bat_c};{v_bat_v:.1f};{kalan_enerji_wh}"


def _unique_path(directory: str, stem: str, extension: str) -> str:
    """`directory/stem.extension` diskte zaten varsa `_2`, `_3`, ... sayaç
    eki deneyerek diskte henüz bulunmayan bir yol döndürür. Aynı saniye
    içinde art arda üretilen dosya adlarının çakışıp mevcut bir dosyayı
    sessizce sıfırlamasını (truncate) önler."""
    candidate = os.path.join(directory, f"{stem}{extension}")
    counter = 2
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{stem}_{counter}{extension}")
        counter += 1
    return candidate


def make_log_filename(suffix: str = "") -> str:
    """PC tarih/saatine göre eşsiz bir log dosyası adı üretir; config.OUTPUT_DIR
    klasörünü oluşturur (MON-12, madde 84 -- eskiden "logs" sabit kodlanmıştı,
    OUTPUT_DIR ayarı YOK SAYILIYORDU). `suffix` (örn. "_SIM") uzantıdan ÖNCE
    eklenir; varsayılan "" olduğundan mevcut çağrılar/dosya adı deseni
    etkilenmez. Aynı saniye içinde ikinci bir çağrı yapılırsa (örn. new-boot
    yeniden-açılışı) `_2`, `_3`, ... sayaç eki ile isim çakışması önlenir."""
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return _unique_path(config.OUTPUT_DIR, f"telem_{timestamp}{suffix}", ".csv")


def detect_new_boot(prev_seq: int | None, curr_seq: int) -> bool:
    """Seq sayacında geriye sıçrama veya sıfırlanma olup olmadığını tespit eder.

    AKS yalnızca gerçek TX anında seq'i artırır (P6: test_replay_sanitize_and_seq
    ile sabitlendi); bağlantı koptuktan sonra tamponda biriken paketler eski
    zaman damgasıyla (ts) ama ARTAN seq ile tekrar gönderilir (replay). Bu
    yüzden yeni-boot tespiti yalnızca seq'e bakar, ts'e bakmaz — ts geriye
    gitmesi replay'in normal bir parçasıdır, yeni boot'un işareti değildir.

    seq alanı uint32'dir; bir yarış (~birkaç saat, 5 sn'lik örnekleme
    aralığında yüz binlerce paket) süresince 2**32 paketi bulamayacağından
    sayaç taşması (overflow) bu tespit mantığının kapsamı dışında bırakılmıştır.

    MON-13 (madde 109/1): önceki sürümde ikinci bir koşul daha vardı
    (`curr_seq < 10 and prev_seq > 100`) -- "iki katmanlı koruma" izlenimi
    veriyordu ama matematiksel olarak ULAŞILAMAZ ölü koddu: bu ikinci koşul
    yalnızca ilk koşul (`curr_seq < prev_seq`) YANLIŞ olduğunda, yani
    curr_seq >= prev_seq iken değerlendirilir; ama `prev_seq > 100` VE
    `curr_seq >= prev_seq` aynı anda doğruysa curr_seq de > 100 olur, bu da
    `curr_seq < 10` ile ÇELİŞİR. Yani ikinci koşul hiçbir girdi için ilkinden
    bağımsız olarak True olamaz. TEK katmanlı (curr_seq < prev_seq) tespit
    zaten yeterli ve doğru -- bilinçli olarak tek koşulda bırakıldı, sahte
    bir "ikinci katman" eklenmedi.
    """
    if prev_seq is None:
        return False
    return curr_seq < prev_seq


def is_replay_ts(prev_ts_ms: int | None, curr_ts_ms: int) -> bool:
    """ts_ms öncekinden küçükse True döner (AKS offline-buffer drenajı
    sırasında eski zaman damgasıyla tekrar gönderilen paketin işareti,
    9.2.e). Yalnızca teşhis/jüri kaydı içindir — detect_new_boot False
    döndüğünde (yeni boot DEĞİL) çağıran taraf bunu "REPLAY?" olarak
    events log'a not düşer; CSV dosyasını veya new-boot kararını etkilemez.
    """
    if prev_ts_ms is None:
        return False
    return curr_ts_ms < prev_ts_ms


def make_events_log_filename(suffix: str = "") -> str:
    """PC tarih/saatine göre eşsiz bir olay (link/port durumu) log dosyası adı
    üretir; config.OUTPUT_DIR klasörünü oluşturur (MON-12, madde 84). CSV
    telemetri dosyasından ayrıdır ve 5 kolonlu CSV şemasını etkilemez.
    `suffix` (örn. "_SIM") uzantıdan ÖNCE eklenir; varsayılan "" olduğundan
    mevcut çağrılar/dosya adı deseni etkilenmez. Aynı saniye içinde ikinci
    bir çağrı yapılırsa `_2`, `_3`, ... sayaç eki ile isim çakışması önlenir."""
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return _unique_path(config.OUTPUT_DIR, f"events_{timestamp}{suffix}", ".log")


def format_event_line(message: str, when: datetime | None = None) -> str:
    """Bir olayı ("SERI PORT KOPTU", "LINK,DOWN" vb.) zaman damgalı events log
    satırına dönüştürür. `when` testte sabit bir zaman enjekte etmek için var."""
    ts = (when or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    return f"[{ts}] {message}"
