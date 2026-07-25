"""TUFAN İzleme Merkezi - ayarlar."""

# Varsayılanın "SIMULATE" mi yoksa gerçek bir port mu olacağı EKİBE ait bir
# karardır — bu değişiklikte varsayılan bilerek DEĞİŞTİRİLMEDİ. SIMULATE
# modunda üretilen dosyalar "_SIM" ekiyle (telem_..._SIM.csv,
# events_..._SIM.log) ve pencere başlığındaki kalıcı uyarıyla gerçek
# kayıttan ayrıştığından, varsayılanı SIMULATE bırakmak artık güvenli.
SERIAL_PORT = "COM5"          # kullanıcı değiştirir ("SIMULATE" veya "COM3" / "/dev/cu.usbserial-xxx")
SERIAL_BAUD = 115200          # UKS USART1 baud hızı

# MON-09 (madde 86): SERIAL_PORT açılamazsa (örn. USB başka bir porta
# düştüyse) mevcut diğer portları deneyip ilk başarılı olanı kullan. Hangi
# porta bağlanıldığı events log'a ve durum çubuğuna yazılır -- müdahale
# gerekmez (9.4.a.vii). False yapılırsa yalnız SERIAL_PORT denenir (eski
# davranış).
AUTO_DISCOVER_PORT = True

# 9.2.f: izleme merkezi kaydında kalan_enerji_Wh kolonu zorunludur.
# BATTERY_CAPACITY_WH, format_record() içindeki enerji hesabının çarpanı.
# TEYİTLİ — batarya ekibi, 2026-07 (100 Ah paket, nominal kapasite).
# Y23 (24.07.2026) ekip teyidi: 100 Ah / 8700 Wh. Bu, üreticinin verdiği
# NOMİNAL ENERJİ değeridir — "100 Ah x 76.8 V nominal = 7680 Wh" aritmetiğiyle
# birebir tutmaz ve TUTMASI DA BEKLENMEZ. Değer ekip tarafından ayrıca
# doğrulanmıştır; hesapla karşılaştırıp "çelişkili" diye değiştirmeyin.
BATTERY_CAPACITY_WH = 8700.0

# BATTERY_CAPACITY_WH gerçek değerle güncellenip teyit edildiğinde True
# yapılır. False iken konsola başlangıç uyarısı basılır ve GUI pencere
# başlığına kalıcı bir uyarı eklenir — kayıt akışı YİNE DE durmaz (9.2.g:
# kayıt gösterememek diskalifiye nedenidir, eksik parametre kaydı
# durduramaz).
CONFIG_CONFIRMED = True
OUTPUT_DIR = "logs"           # CSV dosyaları buraya yazılır

# MON-02 (madde 20): ayarlanmışsa her CSV satırı birincil dosyaya yazıldıktan
# sonra AYNEN bu klasöre de (aynı dosya adıyla) yazılır -- örn. bir USB
# bellek yolu. İkincil yazma hatası birincil kaydı ASLA etkilemez (bkz.
# monitor.open_backup_log_file / serial_worker).
BACKUP_OUTPUT_DIR = None

GRAPH_WINDOW_SEC = 60          # grafik kayan pencere süresi
MAX_SPEED_KMH = 150            # Y ekseni üst sınırı ve hız aralık kapısı üst sınırı

# MON-03 (madde 50/69): parse_csv_line() alan bazlı aralık kapıları -- bu
# sınırların DIŞINDA bir alan taşıyan satır TÜMÜYLE reddedilir (dosyaya hiç
# yazılmaz). Sınırlar kasıtlı olarak burada, config.py'de tutulur.
#
# TASARIM KURALI — BU KAPILAR GENİŞ TUTULUR (Y23 gözden geçirmesi, 25.07.2026):
# Bunlar bir "beklenen çalışma aralığı" değil, BOZUK SATIR filtresidir. Kapıyı
# gerçek pakete daraltmak (örn. gerilimi 60-90 V yapmak) hiçbir şey KAZANDIRMAZ
# ama aralık dışına çıkan geçerli bir ölçümün kaydını SESSİZCE DÜŞÜRÜR — ve
# şartname 9.2.g'ye göre kaydı gösterememek diskalifiye nedenidir. Gerçek paket
# aralığı (Y23: min 60 V, nominal 76.8 V, maks 87.6 V) GÖSTERGE ölçeklerinde
# kullanılır (aşağıdaki *_GAUGE_* değerleri), kabul kapılarında DEĞİL.
MIN_SPEED_KMH = 0              # üst sınır için MAX_SPEED_KMH kullanılır
MIN_TEMP_C = -40               # cBMS24 datasheet alt ucu
# cBMS24 ölçüm üst ucu 85 °C civarıdır; kapı bilinçli olarak 150'de bırakıldı
# (bkz. yukarıdaki tasarım kuralı). Sıcaklık zaten 55/70 °C eşikleriyle AKS
# tarafında değerlendiriliyor; burada amaç yalnızca bozuk sayıyı elemektir.
MAX_TEMP_C = 150
MIN_VOLTAGE_V = 0
# Şartname: batarya paketi 150 V'u aşamaz. Gerçek paket maksimumu 87.6 V (Y23),
# yani kapı zaten bol paylıdır -- daraltılmadı (bkz. tasarım kuralı).
MAX_VOLTAGE_V = 150
MIN_SOC_PERCENT = 0
MAX_SOC_PERCENT = 100
MIN_TIMESTAMP_MS = 0

# MON-05 (madde 49): son geçerli satırdan bu yana bu süre geçtiyse GUI
# kartları "--" gösterir ve soluklaşır -- bağlantı koptuğunda bayat veri
# CANLI veri gibi gösterilmez. Kayıt davranışını ETKİLEMEZ, yalnız gösterim.
STALE_DATA_SEC = 3.0

# MON-07 (madde 89): metrik kart (bar) gösterge ölçekleri -- gerçek paket
# aralığına göre; kodda sabit bırakılmaz. HIZ için MAX_SPEED_KMH, KALAN
# ENERJİ için BATTERY_CAPACITY_WH (yukarıda tanımlı) üst sınır olarak
# kullanılır -- bunlar zaten config'de olduğundan tekrar edilmez.
SPEED_GAUGE_MIN = 0
SOC_GAUGE_MIN = 0
SOC_GAUGE_MAX = 100
# Gerçek paket aralığı 60-87.6 V (Y23 ekip teyidi, 24.07.2026: min 60,
# nominal 76.8, maks 87.6 V) -- eski 40-60 aralığı hatalıydı (madde 89).
# 90 üst sınırı, 87.6'lık gerçek maksimumun ibreyi tam uca dayamaması için.
VOLTAGE_GAUGE_MIN = 60.0
VOLTAGE_GAUGE_MAX = 90.0
TEMP_GAUGE_MIN = 20
TEMP_GAUGE_MAX = 80
ENERGY_GAUGE_MIN = 0
