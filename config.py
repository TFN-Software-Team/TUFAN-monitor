"""TUFAN İzleme Merkezi - ayarlar."""

# Varsayılanın "SIMULATE" mi yoksa gerçek bir port mu olacağı EKİBE ait bir
# karardır — bu değişiklikte varsayılan bilerek DEĞİŞTİRİLMEDİ. SIMULATE
# modunda üretilen dosyalar "_SIM" ekiyle (telem_..._SIM.csv,
# events_..._SIM.log) ve pencere başlığındaki kalıcı uyarıyla gerçek
# kayıttan ayrıştığından, varsayılanı SIMULATE bırakmak artık güvenli.
SERIAL_PORT = "COM5"          # kullanıcı değiştirir ("SIMULATE" veya "COM3" / "/dev/cu.usbserial-xxx")
SERIAL_BAUD = 115200          # UKS USART1 baud hızı

# 9.2.f: izleme merkezi kaydında kalan_enerji_Wh kolonu zorunludur.
# BATTERY_CAPACITY_WH, format_record() içindeki enerji hesabının çarpanı.
# TEYİTLİ — batarya ekibi, 2026-07 (100 Ah paket, nominal kapasite).
BATTERY_CAPACITY_WH = 8700.0   # 100 Ah paket, batarya ekibi teyidi (2026-07)

# BATTERY_CAPACITY_WH gerçek değerle güncellenip teyit edildiğinde True
# yapılır. False iken konsola başlangıç uyarısı basılır ve GUI pencere
# başlığına kalıcı bir uyarı eklenir — kayıt akışı YİNE DE durmaz (9.2.g:
# kayıt gösterememek diskalifiye nedenidir, eksik parametre kaydı
# durduramaz).
CONFIG_CONFIRMED = True
OUTPUT_DIR = "logs"           # CSV dosyaları buraya yazılır

GRAPH_WINDOW_SEC = 60          # grafik kayan pencere süresi
MAX_SPEED_KMH = 150            # Y ekseni üst sınırı
