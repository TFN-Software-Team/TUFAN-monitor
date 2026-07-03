"""CSV ayrıştırma ve dosya adı üretimi - saf fonksiyonlar, donanım bağımlılığı yok."""

import os
from datetime import datetime

HEADER = "zaman_ms;hiz_kmh;T_bat_C;V_bat_C;kalan_enerji_Wh"


def parse_csv_line(line: str) -> dict | None:
    """"CSV," ile başlayan bir telemetri satırını ayrıştırır."""
    line = line.strip()
    if not line.startswith("CSV,"):
        return None

    fields = line.split(",")
    if len(fields) != 7:
        return None

    try:
        return {
            "timestamp_ms": int(fields[1]),
            "speed_kmh_x10": int(fields[2]),
            "temp_c": int(fields[3]),
            "pack_voltage_deciv": int(fields[4]),
            "soc_hundredths": int(fields[5]),
            "seq": int(fields[6]),
        }
    except ValueError:
        return None


def format_record(parsed: dict, battery_capacity_wh: float) -> str:
    """Ayrıştırılmış bir kaydı yönetmelik formatında CSV satırına dönüştürür."""
    timestamp_ms = parsed["timestamp_ms"]
    hiz_kmh = parsed["speed_kmh_x10"] / 10
    t_bat_c = parsed["temp_c"]
    v_bat_v = parsed["pack_voltage_deciv"] / 10
    kalan_enerji_wh = round(parsed["soc_hundredths"] / 10000 * battery_capacity_wh)

    return f"{timestamp_ms};{hiz_kmh:.1f};{t_bat_c};{v_bat_v:.1f};{kalan_enerji_wh}"


def make_log_filename() -> str:
    """PC tarih/saatine göre eşsiz bir log dosyası adı üretir; logs/ klasörünü oluşturur."""
    os.makedirs("logs", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join("logs", f"telem_{timestamp}.csv")


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
    """
    if prev_seq is None:
        return False
    if curr_seq < prev_seq:
        return True
    if curr_seq < 10 and prev_seq > 100:
        return True
    return False


def make_events_log_filename() -> str:
    """PC tarih/saatine göre eşsiz bir olay (link/port durumu) log dosyası adı
    üretir; logs/ klasörünü oluşturur. CSV telemetri dosyasından ayrıdır ve
    5 kolonlu CSV şemasını etkilemez."""
    os.makedirs("logs", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join("logs", f"events_{timestamp}.log")


def format_event_line(message: str, when: datetime | None = None) -> str:
    """Bir olayı ("SERI PORT KOPTU", "LINK,DOWN" vb.) zaman damgalı events log
    satırına dönüştürür. `when` testte sabit bir zaman enjekte etmek için var."""
    ts = (when or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    return f"[{ts}] {message}"
