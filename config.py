"""TUFAN İzleme Merkezi - ayarlar."""

SERIAL_PORT = "COM3"          # kullanıcı değiştirir
SERIAL_BAUD = 115200          # UKS USART1 baud hızı
BATTERY_CAPACITY_WH = 1000.0  # TODO: gerçek batarya kapasitesi (Wh)
OUTPUT_DIR = "logs"           # CSV dosyaları buraya yazılır

GRAPH_WINDOW_SEC = 60          # grafik kayan pencere süresi
MAX_SPEED_KMH = 150            # Y ekseni üst sınırı
