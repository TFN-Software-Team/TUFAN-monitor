"""TUFAN İzleme Merkezi - ayarlar."""

SERIAL_PORT = "COM3"          # kullanıcı değiştirir
SERIAL_BAUD = 115200          # UKS USART1 baud hızı

# 9.2.f: izleme merkezi kaydında kalan_enerji_Wh kolonu zorunludur.
# BATTERY_CAPACITY_WH, format_record() içindeki enerji hesabının çarpanı
# olduğundan gerçek değer girilene kadar bu kolon YANLIŞTIR. Kaynak: ekip
# teknik şartnamesi / batarya paketi datasheet'i (Wh, nominal kapasite).
# PLACEHOLDER — henüz teyit edilmedi.
BATTERY_CAPACITY_WH = 1000.0

# BATTERY_CAPACITY_WH gerçek değerle güncellenip teyit edildiğinde True
# yapılır. False iken konsola başlangıç uyarısı basılır ve GUI pencere
# başlığına kalıcı bir uyarı eklenir — kayıt akışı YİNE DE durmaz (9.2.g:
# kayıt gösterememek diskalifiye nedenidir, eksik parametre kaydı
# durduramaz).
CONFIG_CONFIRMED = False

OUTPUT_DIR = "logs"           # CSV dosyaları buraya yazılır

GRAPH_WINDOW_SEC = 60          # grafik kayan pencere süresi
MAX_SPEED_KMH = 150            # Y ekseni üst sınırı
