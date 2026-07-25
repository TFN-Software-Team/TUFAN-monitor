"""TUFAN İzleme Merkezi - GUI (tkinter/matplotlib) uygulaması.

MON-11 (madde 95): saf mantık + donanım/dosya G-Ç (seri okuma worker'i dahil)
monitor_core.py'ye taşındı -- bu modül yalnızca GUI'ye (MetricCard,
MonitorApp) ve CLI giriş noktasına (main) odaklanır. tkinter/matplotlib
eksikse yalnızca BU modülün import'u başarısız olur; monitor_core.py'yi
kullanan (worker/saf fonksiyon) testler bundan ETKİLENMEZ.

MON-10 (madde 87): tkinter/matplotlib MODÜL SEVİYESİNDE import EDİLMEZ --
yalnızca `run_gui()` çağrıldığında (bkz. `_load_gui_dependencies`) yüklenir.
Böylece `python monitor.py --no-gui` bu iki bağımlılığı hiç yüklemeden
`monitor_core.run_headless()`'a düşebilir; ayrıca `import monitor` (örn.
`--port`/`--no-gui` argüman ayrıştırma testleri için) tkinter kurulu
olmayan bir ortamda bile güvenle çalışır.
"""

import argparse
import bisect
import collections
import os
import queue
import sys
import threading
import time

import config
from monitor_core import (
    MAX_WORKER_RESTARTS,
    WorkerHeartbeat,
    build_line_with_gaps,
    check_output_dir_writable,
    compute_stale_display,
    compute_worker_health_state,
    detect_cloud_sync_folder,
    format_port_list_message,
    format_timestamp_ms,
    insert_sorted_point,
    list_available_ports,
    list_ports,  # noqa: F401 -- testler monitor.list_ports.comports'u monkeypatch'ler (paylaşılan modül nesnesi)
    max_consecutive_gap_sec,
    run_headless,
    serial_worker,
    trim_history_window,
    truncate_path_for_display,
)

LINK_TIMEOUT_SEC = 3.0
GUI_POLL_MS = 200

# Y26: ham satır panelinde tutulan azami satır sayısı (kaydıran tampon).
# ~50 satır, 1 Hz telemetride son ~50 saniyeyi kapsar — "şu an ne geliyor?"
# sorusuna cevap vermeye yeter, belleği ve widget güncellemesini sınırlar.
RAW_PANEL_MAX_LINES = 50

# MON-10: modül seviyesinde import EDİLMEZ (bkz. _load_gui_dependencies) --
# yalnızca isim çözümlemesi için yer tutucu; MetricCard/_Tooltip/MonitorApp
# metodları bunlara yalnız ÇAĞRILDIKLARINDA erişir (tanım anında değil), bu
# yüzden gerçek değerleri run_gui() çağrılana kadar None kalması güvenlidir.
tk = None
FigureCanvasTkAgg = None
Figure = None


def _load_gui_dependencies():
    """MON-10 (madde 87): tkinter/matplotlib'i LAZY olarak yükler -- yalnız
    GUI modu gerçekten seçildiğinde (bkz. run_gui) çağrılır. Biri eksikse
    ImportError fırlatır; çağıran taraf (main) bunu yakalayıp headless moda
    otomatik düşebilir."""
    global tk, FigureCanvasTkAgg, Figure
    import tkinter as _tk
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg as _FigureCanvasTkAgg
    from matplotlib.figure import Figure as _Figure
    tk = _tk
    FigureCanvasTkAgg = _FigureCanvasTkAgg
    Figure = _Figure


class MetricCard:
    def __init__(self, parent, title, unit, min_val, max_val, color):
        self.title = title
        self.unit = unit
        self.min_val = min_val
        self.max_val = max_val
        self.color = color
        self.current_value = 0.0

        # Frame
        self.frame = tk.Frame(parent, bg="#161D30", bd=0, highlightthickness=1, highlightbackground="#242F4D")

        # Left accent line
        self.accent_line = tk.Frame(self.frame, bg=color, width=4)
        self.accent_line.pack(side="left", fill="y")

        # Main inner container
        self.inner = tk.Frame(self.frame, bg="#161D30", padx=12, pady=10)
        self.inner.pack(side="left", fill="both", expand=True)

        # Center container inside inner to center vertically
        self.center_container = tk.Frame(self.inner, bg="#161D30")
        self.center_container.pack(expand=True, fill="x")

        # Top row: Title & Unit
        header_frame = tk.Frame(self.center_container, bg="#161D30")
        header_frame.pack(fill="x", side="top")

        self.title_label = tk.Label(header_frame, text=title.upper(), font=("Helvetica Neue", 11, "bold"), fg="#94A3B8", bg="#161D30")
        self.title_label.pack(side="left")

        # Value & Unit row
        value_frame = tk.Frame(self.center_container, bg="#161D30")
        value_frame.pack(fill="x", side="top", pady=(2, 4))

        self.value_label = tk.Label(value_frame, text="--", font=("Helvetica Neue", 20, "bold"), fg=color, bg="#161D30")
        self.value_label.pack(side="left")

        self.unit_label = tk.Label(value_frame, text=f" {unit}", font=("Helvetica Neue", 10, "bold"), fg="#64748B", bg="#161D30")
        self.unit_label.pack(side="left", anchor="s", pady=(0, 2))

        # Sleek custom canvas progress bar at the bottom
        self.canvas = tk.Canvas(self.center_container, height=4, bg="#1E293B", highlightthickness=0, bd=0)
        self.canvas.pack(fill="x", side="top", pady=(2, 0))

        self.bar = self.canvas.create_rectangle(0, 0, 0, 4, fill=color, width=0)
        self.canvas.bind("<Configure>", self._on_resize)

        # MON-04/05: opsiyonel küçük alt yazı -- ZAMAN kartında ham zaman_ms
        # değeri, MON-05'te "son veri: X sn önce" için kullanılır.
        self.subtitle_label = tk.Label(
            self.center_container, text="", font=("Helvetica Neue", 8),
            fg="#475569", bg="#161D30",
        )
        self.subtitle_label.pack(fill="x", side="top", anchor="w", pady=(2, 0))
        self._stale = False

    def _on_resize(self, event=None):
        self.update_bar()

    def set_subtitle(self, text):
        self.subtitle_label.config(text=text)

    def set_display(self, value_text, subtitle=""):
        """MON-04: serbest biçimli (float'a zorlanmayan) bir değer göster --
        örneğin ZAMAN kartının "dk:sn.ms" biçimi. Bar güncellenmez (bu kart
        için bir ilerleme çubuğu anlamlı değil)."""
        self.value_label.config(text=value_text, fg=self.color)
        self.subtitle_label.config(text=subtitle, fg="#475569")
        self._stale = False

    def set_stale(self, is_stale, message=""):
        """MON-05 (madde 49): bayat veri -- kartı "--" gösterip gri/soluk
        yapar. Veri geri geldiğinde (is_stale=False) normal renge döner;
        set_value/set_display bir sonraki çağrıda zaten kendi rengini
        uygular, burada yalnız staleness geçişini yönetiyoruz."""
        self._stale = is_stale
        if is_stale:
            self.value_label.config(text="--", fg="#475569")
            self.canvas.itemconfig(self.bar, fill="#334155")
            self.subtitle_label.config(text=message, fg="#F59E0B")
        else:
            # NOT: subtitle_label bilerek DOKUNULMAZ -- fresh veri geldiğinde
            # (bu geçiş zaten yalnız bu durumda tetiklenir) set_value/
            # set_display çağrısı AYNI GUI turunda, bu satırdan ÖNCE zaten
            # doğru subtitle'ı yazmış olur (bkz. MonitorApp.update_gui sırası:
            # önce mesaj kuyruğu işlenir, sonra _refresh_stale_state çağrılır).
            # Burada temizlemek, o taze subtitle'ı ANINDA silerdi.
            self.value_label.config(fg=self.color)
            self.canvas.itemconfig(self.bar, fill=self.color)

    def set_value(self, value):
        try:
            self.current_value = float(value)
        except (ValueError, TypeError):
            self.current_value = 0.0

        if isinstance(value, float):
            val_str = f"{value:.1f}"
        else:
            val_str = f"{value}"

        self.value_label.config(text=val_str)
        # Taze bir sayısal değer geldi -- olası bir önceki "son veri: X sn
        # önce" (staleness) alt yazısını temizle.
        self.subtitle_label.config(text="", fg="#475569")

        # Dynamic temperature coloring
        if self.title == "SICAKLIK":
            if self.current_value >= 60.0:
                dynamic_color = "#EF4444"  # Red
            elif self.current_value >= 50.0:
                dynamic_color = "#F59E0B"  # Yellow/Orange
            else:
                dynamic_color = self.color  # Default Orange (#F97316)
            self.value_label.config(fg=dynamic_color)
            self.canvas.itemconfig(self.bar, fill=dynamic_color)

        self.update_bar()

    def update_bar(self):
        span = self.max_val - self.min_val
        if span <= 0:
            pct = 0.0
        else:
            pct = (self.current_value - self.min_val) / span
        pct = max(0.0, min(1.0, pct))

        width = self.canvas.winfo_width()
        if width > 1:
            self.canvas.coords(self.bar, 0, 0, int(width * pct), 4)


class _Tooltip:
    """MON-08 (madde 108): basit hover tooltip -- yalnızca kısaltılmış bir
    yolun tam halini göstermek için, ekstra bağımlılık gerektirmeden saf
    tkinter ile."""

    def __init__(self, widget, text_provider):
        self.widget = widget
        self.text_provider = text_provider
        self.tip_window = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, event=None):
        text = self.text_provider()
        if not text or self.tip_window is not None:
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip_window = tk.Toplevel(self.widget)
        self.tip_window.wm_overrideredirect(True)
        self.tip_window.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            self.tip_window, text=text, bg="#1E293B", fg="#F8FAFC",
            font=("Helvetica Neue", 8), padx=6, pady=3,
            highlightthickness=1, highlightbackground="#334155",
        )
        label.pack()

    def _hide(self, event=None):
        if self.tip_window is not None:
            self.tip_window.destroy()
            self.tip_window = None


class MonitorApp:
    """tkinter tabanlı TUFAN telemetri izleme penceresi."""

    def __init__(self, root):
        self.root = root
        self._current_title = None
        self.recording_stopped = False
        self._set_title()
        self.root.geometry("1080x620")
        self.root.minsize(1000, 540)
        self.root.configure(bg="#0B0F19")

        self.data_queue = queue.Queue()
        self.stop_event = threading.Event()

        self.packet_count = 0
        self.parse_error_count = 0
        self.range_error_count = 0
        self.dedup_count = 0  # MON-13 (madde 109/3)
        self.last_packet_time = None
        self.start_time = time.monotonic()
        # R2: [(ts_sec, hiz_kmh), ...] — ts_sec paketin KENDİ zaman damgası
        # (AKS ts_ms/1000), varış sırası/zamanı DEĞİL; insert_sorted_point
        # ile her zaman ts_sec'e göre SIRALI tutulur (bkz. update_gui).
        self.speed_history = []

        self.port_connected = True  # ilk bağlanma denemesi sonucu ilk mesajla güncellenir
        self.link_connected = False

        # MON-09 (madde 86): gerçekte bağlanılan port (otomatik keşifle
        # config.SERIAL_PORT'tan farklı olabilir) ve son bilinen port listesi.
        self.active_port = config.SERIAL_PORT
        self.last_known_ports = []

        # MON-01 (madde 19): worker canlılığı heartbeat + restart durumu.
        self.heartbeat = WorkerHeartbeat()
        self.worker_restart_count = 0
        self.worker_permanently_failed = False
        self._kayit_durdu_visible = False
        self._blink_on = False
        self._blink_tick = 0

        # MON-05 (madde 49): veri hiç gelmemişken de "bayat" sayılır --
        # kartlar başlangıçta zaten "--" gösterdiğinden bu tutarlıdır.
        self._data_is_stale = True

        # MON-06 (madde 67/68, 9.2.h): tüm zaman_ms değerleri SIRALI tutulur
        # (yeni boot'ta temizlenir) -- gerçek ardışık boşluk hesaplanır.
        self.all_timestamps_ms = []

        # MON-08 (madde 108): durum çubuğu için tam dosya yolu ve satır sayısı.
        self.log_file_path = None

        # Y26 (madde C): ham satır paneli. Bir seri portu aynı anda TEK program
        # açabilir (işletim sistemi kısıtı), bu yüzden UKS terminal-izleme ile
        # Monitor GUI'si BİRLİKTE çalıştırılamıyor. Panel, ham "CSV,/LINK," vb.
        # satırları Monitor'ün İÇİNDE gösterir — ayrı terminal gerekmez.
        # Varsayılan KAPALI (performans): her satır için widget güncellemesi
        # yapmamak üzere panel kapalıyken satırlar yalnızca tampona yazılır.
        self.raw_panel_visible = False
        self.raw_lines = collections.deque(maxlen=RAW_PANEL_MAX_LINES)
        self._raw_dirty = False

        self._build_widgets()

        self._start_worker_thread()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(GUI_POLL_MS, self.update_gui)

    def _set_title(self):
        title = "TUFAN Telemetri İzleme Merkezi"
        if not config.CONFIG_CONFIRMED:
            # 9.2.g: eksik parametre kaydı durduramaz — kayıt akışı devam
            # eder, ama teyitsiz kapasite pencere başlığında KALICI olarak
            # görünür kalır (tek satırlık konsol uyarısı gözden kaçabilir).
            title += " [BATARYA KAPASITESI TEYITSIZ — kalan_enerji_Wh gecersiz]"
        if config.SERIAL_PORT == "SIMULATE":
            # SIMULATE modunda uretilen veri sahte — bu da CONFIG_CONFIRMED
            # uyarisiyla ayni kalicilikta baslikta gorunur kalmali (ikisi
            # ayni anda gecerliyse ikisi de eklenir).
            title += " [SİMÜLASYON — GERÇEK VERİ DEĞİL]"
        if self.recording_stopped:
            # MON-01 (madde 19): pencere başlığına KALICI/görünür bir uyarı --
            # ekran görüntüsü tek başına bile "kayıt durdu" kanıtı taşısın.
            title += " [KAYIT DURDU]"
        if title != self._current_title:
            self._current_title = title
            self.root.title(title)

    def _start_worker_thread(self, restart_attempt=0):
        self.heartbeat.beat()
        self.worker_thread = threading.Thread(
            target=serial_worker,
            args=(self.data_queue, self.stop_event),
            kwargs={"heartbeat": self.heartbeat, "restart_attempt": restart_attempt},
            daemon=True,
        )
        self.worker_thread.start()

    def _build_widgets(self):
        # Header panel
        header_frame = tk.Frame(self.root, bg="#0B0F19")
        header_frame.pack(fill="x", padx=15, pady=(15, 10))
        
        title_container = tk.Frame(header_frame, bg="#0B0F19")
        title_container.pack(side="left")
        
        title_label = tk.Label(
            title_container, 
            text="TUFAN ELEKTROMOBİL TELEMETRİ SİSTEMİ", 
            font=("Helvetica Neue", 16, "bold"), 
            fg="#F8FAFC", 
            bg="#0B0F19"
        )
        title_label.pack(anchor="w")
        
        self.file_label = tk.Label(
            title_container,
            text="● BEKLEMEDE: Kayıt dosyası oluşturuluyor...",
            font=("Helvetica Neue", 9, "bold"),
            fg="#475569",
            bg="#0B0F19"
        )
        self.file_label.pack(anchor="w", pady=(2, 0))

        # MON-02 (madde 20): birincil kaydın hemen altında ikincil (yedek)
        # kaydın durumunu gösteren küçük ikinci gösterge.
        if config.BACKUP_OUTPUT_DIR:
            backup_initial_text = "○ YEDEK KAYIT: bekleniyor..."
        else:
            backup_initial_text = "○ Yedek kayıt yolu ayarlanmadı"
        self.backup_label = tk.Label(
            title_container,
            text=backup_initial_text,
            font=("Helvetica Neue", 9, "bold"),
            fg="#475569",
            bg="#0B0F19"
        )
        self.backup_label.pack(anchor="w", pady=(2, 0))

        # MON-01 (madde 19): "KOPUK" rozetinden GÖRSEL OLARAK FARKLI, büyük
        # ve yanıp sönen bir uyarı -- yalnız worker gerçekten ölmüşken veya
        # heartbeat 5 sn'den uzun süredir gelmemişken PACK edilir (bkz.
        # _refresh_worker_health). Başlangıçta paketlenmez (görünmez).
        self.kayit_durdu_label = tk.Label(
            title_container,
            text="⛔ KAYIT DURDU",
            font=("Helvetica Neue", 14, "bold"),
            fg="white",
            bg="#EF4444",
            padx=10,
            pady=4,
        )

        # MON-16 (madde 66): yer istasyonu kesintisi tespit edilirse (ve
        # yalnızca o zaman) dolan, oturum boyunca KALICI kalan bir not.
        self.station_gap_label = tk.Label(
            title_container,
            text="",
            font=("Helvetica Neue", 9, "bold"),
            fg="#F59E0B",
            bg="#0B0F19",
        )
        self.station_gap_label.pack(anchor="w", pady=(2, 0))

        # MON-14 (madde 85): kayıt klasörü bir bulut senkron klasöründeyse
        # (ve yalnızca o zaman) dolan, oturum boyunca KALICI kalan bir uyarı.
        self.cloud_sync_label = tk.Label(
            title_container,
            text="",
            font=("Helvetica Neue", 9, "bold"),
            fg="#F59E0B",
            bg="#0B0F19",
        )
        self.cloud_sync_label.pack(anchor="w", pady=(2, 0))

        team_label = tk.Label(
            header_frame, 
            text="TFN SOFTWARE TEAM", 
            font=("Helvetica Neue", 9, "bold"), 
            fg="#00D2FF", 
            bg="#0B0F19",
            padx=10,
            pady=4,
            relief="flat",
            highlightthickness=1,
            highlightbackground="#00D2FF"
        )
        team_label.pack(side="right", anchor="n", pady=2)

        # Main workspace container (Left/Right Split)
        main_container = tk.Frame(self.root, bg="#0B0F19")
        main_container.pack(fill="both", expand=True, padx=15, pady=(5, 5))

        # LEFT PANEL (Wide Telemetry Plot - 60% Width)
        left_panel = tk.Frame(main_container, bg="#0B0F19")
        left_panel.pack(side="left", fill="both", expand=True, padx=(0, 10))

        # Graph Card wrapper to match right cards styling and align edges
        graph_card = tk.Frame(left_panel, bg="#161D30", bd=0, highlightthickness=1, highlightbackground="#242F4D", padx=10, pady=10)
        graph_card.pack(fill="both", expand=True)

        self.figure = Figure(figsize=(6.5, 4.2), dpi=90, facecolor='#161D30')
        self.ax = self.figure.add_subplot(111, facecolor='#161D30')
        self.ax.set_ylim(0, config.MAX_SPEED_KMH)
        self.ax.set_xlim(0, config.GRAPH_WINDOW_SEC)
        
        # Style grid & spines
        self.ax.spines['bottom'].set_color('#334155')
        self.ax.spines['left'].set_color('#334155')
        self.ax.spines['top'].set_visible(False)
        self.ax.spines['right'].set_visible(False)
        self.ax.tick_params(colors='#94A3B8', which='both', labelsize=9)
        self.ax.xaxis.label.set_color('#94A3B8')
        self.ax.yaxis.label.set_color('#94A3B8')
        
        self.ax.set_xlabel("Zaman (sn)", fontsize=9, labelpad=5)
        self.ax.set_ylabel("Hız (km/h)", fontsize=9, labelpad=5)
        self.ax.set_title("HIZ PROFİLİ (GERÇEK ZAMANLI SWEEP)", fontdict={'color': '#F8FAFC', 'weight': 'bold', 'size': 10}, pad=10)
        self.ax.grid(True, color='#242F4D', linestyle='--', linewidth=0.5)
        
        (self.speed_line,) = self.ax.plot([], [], color="#00D2FF", linewidth=2.5)
        self.figure.tight_layout()

        self.canvas = FigureCanvasTkAgg(self.figure, master=graph_card)
        self.canvas.get_tk_widget().config(bg="#161D30", highlightthickness=0, takefocus=0)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        # RIGHT PANEL (Metric Cards Grid - 2 sütun x 3 satır)
        right_panel = tk.Frame(main_container, bg="#0B0F19", width=340)
        right_panel.pack(side="right", fill="both", padx=(10, 0))
        right_panel.pack_propagate(False) # Stable layout

        for r in range(3):
            right_panel.rowconfigure(r, weight=1)
        right_panel.columnconfigure(0, weight=1)
        right_panel.columnconfigure(1, weight=1)

        # MON-07 (madde 89): gösterge (bar) ölçekleri config.py'den -- kodda
        # sabit bırakılmaz; gerçek paket aralığına göre.
        self.speed_card = MetricCard(right_panel, "HIZ", "km/h", config.SPEED_GAUGE_MIN, config.MAX_SPEED_KMH, "#00D2FF")
        self.speed_card.frame.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        # MON-04 (madde 48): ZAMAN kartı, hız kartının YANINDA.
        self.time_card = MetricCard(right_panel, "ZAMAN", "", 0, 1, "#FACC15")
        self.time_card.frame.grid(row=0, column=1, sticky="nsew", padx=4, pady=4)

        self.soc_card = MetricCard(right_panel, "SoC", "%", config.SOC_GAUGE_MIN, config.SOC_GAUGE_MAX, "#10B981")
        self.soc_card.frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)

        self.voltage_card = MetricCard(
            right_panel, "GERİLİM", "V", config.VOLTAGE_GAUGE_MIN, config.VOLTAGE_GAUGE_MAX, "#A855F7"
        )
        self.voltage_card.frame.grid(row=1, column=1, sticky="nsew", padx=4, pady=4)

        self.energy_card = MetricCard(
            right_panel, "KALAN ENERJİ", "Wh", config.ENERGY_GAUGE_MIN, config.BATTERY_CAPACITY_WH, "#0EA5E9"
        )
        self.energy_card.frame.grid(row=2, column=0, sticky="nsew", padx=4, pady=4)

        self.temp_card = MetricCard(
            right_panel, "SICAKLIK", "°C", config.TEMP_GAUGE_MIN, config.TEMP_GAUGE_MAX, "#F97316"
        )
        self.temp_card.frame.grid(row=2, column=1, sticky="nsew", padx=4, pady=4)

        # MON-05: bayat-veri kontrolünün döneceği kartların listesi.
        self._metric_cards = [
            self.speed_card, self.time_card, self.soc_card,
            self.voltage_card, self.energy_card, self.temp_card,
        ]

        # Status frame (Footer)
        status_frame = tk.Frame(self.root, bg="#0B0F19")
        status_frame.pack(fill="x", padx=15, pady=(0, 5))

        self.status_badge = tk.Label(
            status_frame,
            text="KOPUK",
            font=("Helvetica Neue", 10, "bold"),
            bg="#EF4444",
            fg="white",
            width=12,
            padx=8,
            pady=4,
            bd=0,
            highlightthickness=0
        )
        self.status_badge.pack(side="left")

        # MON-09 (madde 86): SERİ PORT KOPUK durumunda bulunan portları
        # gösteren büyük/net uyarı -- yalnız port yokken dolu, aksi halde boş.
        self.port_hint_label = tk.Label(
            status_frame, text="", font=("Helvetica Neue", 10, "bold"),
            fg="#EF4444", bg="#0B0F19",
        )
        self.port_hint_label.pack(side="left", padx=(10, 0))

        self.packet_label = tk.Label(status_frame, text="Alınan paket: 0", font=("Helvetica Neue", 9), fg="#64748B", bg="#0B0F19")
        self.packet_label.pack(side="left", padx=15)

        self._default_interval_bg = "#161D30"
        self.interval_label = tk.Label(
            status_frame, 
            text="Son kayıt: --", 
            font=("Helvetica Neue", 9), 
            fg="#94A3B8", 
            bg="#161D30", 
            padx=8, 
            pady=3, 
            highlightthickness=1, 
            highlightbackground="#242F4D"
        )
        self.interval_label.pack(side="left", padx=15)

        # MON-03 (madde 50/69): üç sayaç -- satır_kabul (packet_count'la
        # paylaşılır), satır_parse_hatası, satır_aralık_hatası.
        self.counters_label = tk.Label(
            status_frame,
            text="Kabul: 0 | Format hatası: 0 | Aralık hatası: 0",
            font=("Helvetica Neue", 9),
            fg="#64748B",
            bg="#0B0F19",
        )
        self.counters_label.pack(side="left", padx=15)

        # MON-06 (madde 67/68, 9.2.h): gerçek (varış değil, zaman damgası
        # bazlı) ardışık boşluk göstergesi -- < 5 sn yeşil, >= 5 sn KIRMIZI.
        self.max_gap_label = tk.Label(
            status_frame,
            text="Maks. ardışık zaman farkı: -- sn",
            font=("Helvetica Neue", 9, "bold"),
            fg="#94A3B8",
            bg="#161D30",
            padx=8,
            pady=3,
            highlightthickness=1,
            highlightbackground="#242F4D",
        )
        self.max_gap_label.pack(side="left", padx=15)

        # MON-08 (madde 108): kalıcı alt durum çubuğu -- port/baud/tam yol/
        # satır sayısı, teknik kontrolde "kayıt nereye yazılıyor?" sorusuna
        # ekrandan cevap verilebilsin.
        bottom_bar = tk.Frame(self.root, bg="#0B0F19", highlightthickness=1, highlightbackground="#242F4D")
        bottom_bar.pack(fill="x", padx=15, pady=(0, 10))

        self.connection_summary_label = tk.Label(
            bottom_bar,
            text=f"{config.SERIAL_PORT} @ {config.SERIAL_BAUD}",
            font=("Helvetica Neue", 9),
            fg="#94A3B8",
            bg="#0B0F19",
        )
        self.connection_summary_label.pack(side="left", padx=(6, 15), pady=4)

        self.path_label = tk.Label(
            bottom_bar, text="Dosya: --", font=("Helvetica Neue", 9), fg="#94A3B8", bg="#0B0F19",
        )
        self.path_label.pack(side="left", padx=(0, 10), pady=4)
        self._path_tooltip = _Tooltip(self.path_label, lambda: self.log_file_path or "")

        self.open_folder_button = tk.Button(
            bottom_bar,
            text="Klasörü Aç",
            font=("Helvetica Neue", 8),
            command=self._open_log_folder,
            bg="#1E293B",
            fg="#F8FAFC",
            activebackground="#334155",
            activeforeground="#F8FAFC",
            relief="flat",
            padx=8,
            pady=2,
        )
        self.open_folder_button.pack(side="left", padx=(0, 15), pady=4)

        self.row_count_label = tk.Label(
            bottom_bar, text="0 satır", font=("Helvetica Neue", 9), fg="#94A3B8", bg="#0B0F19",
        )
        self.row_count_label.pack(side="left", padx=(0, 6), pady=4)

        # Y26: ham satır panelini aç/kapat. Klavye kısayolu da bağlanır (F2).
        self.raw_panel_button = tk.Button(
            bottom_bar,
            text="Ham Veri (F2)",
            font=("Helvetica Neue", 8),
            command=self.toggle_raw_panel,
            bg="#1E293B",
            fg="#F8FAFC",
            activebackground="#334155",
            activeforeground="#F8FAFC",
            relief="flat",
            padx=8,
            pady=2,
        )
        self.raw_panel_button.pack(side="right", padx=(0, 6), pady=4)

        # --- Y26: ham satır paneli (salt-okunur, varsayılan GİZLİ) ---
        # Bir COM portunu aynı anda tek program açabildiği için UKS terminal
        # izlemeyle Monitor birlikte çalıştırılamıyordu; bu panel ham satırları
        # Monitor'ün içinde göstererek o ihtiyacı ortadan kaldırır.
        self.raw_panel_frame = tk.Frame(
            self.root, bg="#0B0F19", highlightthickness=1, highlightbackground="#242F4D"
        )
        self.raw_text = tk.Text(
            self.raw_panel_frame,
            height=10,
            font=("Consolas", 9),
            bg="#0B0F19",
            fg="#94A3B8",
            insertbackground="#94A3B8",
            relief="flat",
            wrap="none",
        )
        raw_scroll = tk.Scrollbar(self.raw_panel_frame, command=self.raw_text.yview)
        self.raw_text.configure(yscrollcommand=raw_scroll.set)
        raw_scroll.pack(side="right", fill="y")
        self.raw_text.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        # SALT-OKUNUR: kullanıcı panele yazamaz/silemez (kanıt bütünlüğü
        # açısından da doğru — panel bir görüntüdür, bir düzenleyici değil).
        self.raw_text.config(state="disabled")

        self.root.bind("<F2>", lambda _event: self.toggle_raw_panel())

    def toggle_raw_panel(self):
        """Y26: ham satır panelini aç/kapat.

        Panel KAYIT DAVRANIŞINI ETKİLEMEZ — yalnızca zaten alınmış satırları
        gösterir. Varsayılan kapalıdır: kapalıyken gelen satırlar yalnızca
        kaydıran tampona yazılır, hiçbir widget güncellenmez (performans).
        """
        self.raw_panel_visible = not self.raw_panel_visible
        if self.raw_panel_visible:
            self.raw_panel_frame.pack(fill="both", padx=15, pady=(0, 10))
            self._raw_dirty = True          # açılışta tamponu bir kez bas
            self._refresh_raw_panel()
        else:
            self.raw_panel_frame.pack_forget()

    def _refresh_raw_panel(self):
        """Tamponu Text widget'ına basar. Yalnızca panel AÇIKKEN ve yeni satır
        geldiyse çalışır (update_gui'den çağrılır)."""
        if not self.raw_panel_visible or not self._raw_dirty:
            return
        self.raw_text.config(state="normal")
        self.raw_text.delete("1.0", "end")
        self.raw_text.insert("1.0", "\n".join(self.raw_lines))
        self.raw_text.see("end")           # her zaman en son satır görünsün
        self.raw_text.config(state="disabled")
        self._raw_dirty = False

    def _open_log_folder(self):
        """MON-08 (madde 108): kayıt dosyasının bulunduğu klasörü Windows
        Gezgini'nde açar. Dosya henüz bilinmiyorsa (worker daha başlamadı)
        veya platform desteklenmiyorsa sessizce hiçbir şey yapmaz."""
        if not self.log_file_path:
            return
        folder = os.path.dirname(os.path.abspath(self.log_file_path))
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            else:
                print(f"Klasörü Aç yalnızca Windows'ta desteklenir: {folder}")
        except Exception as exc:
            print(f"Klasör açılamadı: {exc}")

    def _refresh_status_badge(self):
        if not self.port_connected:
            self.status_badge.config(text="SERİ PORT KOPUK", bg="#64748B", fg="white")
            # MON-09 (madde 86): BÜYÜK ve net bir uyarı -- bulunan (varsa)
            # portları göster, teknik kontrolde ekip nedenini anlayabilsin.
            if self.last_known_ports:
                self.port_hint_label.config(
                    text="SERİ PORT BULUNAMADI — Bulunan portlar: " + ", ".join(self.last_known_ports)
                )
            else:
                self.port_hint_label.config(text="SERİ PORT BULUNAMADI — Sistemde hiç seri port yok")
        elif not self.link_connected:
            self.status_badge.config(text="KOPUK", bg="#EF4444", fg="white")
            self.port_hint_label.config(text="")
        else:
            self.status_badge.config(text="BAĞLI", bg="#10B981", fg="white")
            self.port_hint_label.config(text="")

    def update_gui(self):
        now = time.monotonic()

        while True:
            try:
                msg = self.data_queue.get_nowait()
            except queue.Empty:
                break

            msg_type = msg["type"]

            if msg_type == "csv":
                self.last_packet_time = msg["ts"]
                self.packet_count += 1

                self.speed_card.set_value(msg['speed_kmh'])
                self.temp_card.set_value(msg['temp_c'])
                self.voltage_card.set_value(msg['voltage_v'])
                self.soc_card.set_value(msg['soc_percent'])
                self.energy_card.set_value(msg['energy_wh'])
                self.packet_label.config(text=f"Alınan paket: {self.packet_count}")

                # MON-04 (madde 48): ham zaman_ms + insan-okunur "dk:sn.ms".
                raw_ms = msg["timestamp_ms"]
                self.time_card.set_display(format_timestamp_ms(raw_ms), subtitle=f"ham: {raw_ms} ms")

                # MON-06 (madde 67/68, 9.2.h): SIRALI tüm zaman_ms listesi --
                # gerçek ardışık boşluk bu tam listeden hesaplanır.
                bisect.insort(self.all_timestamps_ms, raw_ms)

                # R2: geliş sırası yerine paketin KENDİ ts_sec'ine göre sıralı
                # ekle — geç gelen replay noktaları (eski ts_sec) grafikte
                # doğru (geçmiş) konuma yerleşsin, listenin sonuna değil.
                insert_sorted_point(self.speed_history, (msg["ts_sec"], msg["speed_kmh"]))
                self.link_connected = True

            elif msg_type == "raw_line":
                # Y26: ham satır tamponu. Panel KAPALI olsa bile tampon dolar
                # (açıldığı anda son ~50 satır hazır olsun); widget güncellemesi
                # yalnızca panel AÇIKKEN yapılır — bkz. _refresh_raw_panel.
                self.raw_lines.append(msg["line"])
                self._raw_dirty = True

            elif msg_type == "link_down":
                self.link_connected = False

            elif msg_type == "link_up":
                self.last_packet_time = msg["ts"]
                self.link_connected = True

            elif msg_type == "port_down":
                self.port_connected = False
                self.link_connected = False
                # MON-09: hiçbir port açılamadıysa, bulunan (varsa) portları
                # sakla -- durum çubuğunda/rozette gösterilecek.
                self.last_known_ports = msg.get("available_ports", [])

            elif msg_type == "port_up":
                self.port_connected = True
                self.active_port = msg.get("port", config.SERIAL_PORT)
                self.connection_summary_label.config(text=f"{self.active_port} @ {config.SERIAL_BAUD}")

            elif msg_type == "filename":
                self.file_label.config(
                    text=f"● KAYIT AKTİF: {config.OUTPUT_DIR}/{os.path.basename(msg['name'])}",
                    fg="#10B981",
                )
                # MON-08 (madde 108): durum çubuğundaki tam yol/tooltip.
                self.log_file_path = os.path.abspath(msg["name"])
                self.path_label.config(text=f"Dosya: {truncate_path_for_display(self.log_file_path)}")

            elif msg_type == "new_boot":
                # R2: yeni boot -> ts_ms sıfırdan başlar; eski boot'un yüksek
                # ts'leriyle aynı grafikte karışmasın diye pencere temizlenir.
                self.speed_history.clear()
                # MON-06: eski boot'un yüksek zaman_ms'leri yeni boot'un
                # düşük değerleriyle karışırsa sahte bir dev "boşluk" görünür
                # -- bu bir gerçek iletişim kesintisi değil, temizlenmeli.
                self.all_timestamps_ms.clear()

            elif msg_type == "reject":
                # MON-03 (madde 50/69): bozuk/aralık-dışı satır sayaçları.
                if msg["reason"] == "parse_hatasi":
                    self.parse_error_count += 1
                elif msg["reason"] == "aralik_hatasi":
                    self.range_error_count += 1

            elif msg_type == "dedup":
                # MON-13 (madde 109/3): aynı (seq, zaman_ms) ikilisi tekrar
                # geldi -- dosyaya ikinci kez yazılmadı, yalnız sayaç artar.
                self.dedup_count = msg["count"]

            elif msg_type == "previous_session":
                # MON-16 (madde 66): önceki oturumun son zaman_ms'i, MON-06'nın
                # "maks ardışık zaman farkı" göstergesine dahil edilsin diye
                # sıralı listeye eklenir -- bu oturumun ilk gerçek noktası
                # geldiğinde aradaki fark doğal olarak hesaba katılır.
                bisect.insort(self.all_timestamps_ms, msg["last_ts_ms"])

            elif msg_type == "station_gap":
                # MON-16: yer istasyonu (bu uygulama) yeniden başlamıştı,
                # AKS tarafında TAMPONLANMAMIŞ bir boşluk tespit edildi --
                # ekranda KALICI olarak görünür kalır (teknik kontrolde
                # sorulursa gösterilebilsin).
                self.station_gap_label.config(
                    text=(
                        f"⚠ YER İSTASYONU KESİNTİSİ: {msg['gap_sec']:.1f} sn veri kaybı "
                        "(AKS tarafında tamponlanmadı)"
                    )
                )

            elif msg_type == "cloud_sync_warning":
                # MON-14 (madde 85): kayıt klasörü bir bulut senkron
                # klasöründe -- UYARIR ama uygulamayı ENGELLEMEZ.
                self.cloud_sync_label.config(
                    text=(
                        f"⚠ UYARI: Kayıt klasörü {msg['service']} senkron klasöründe — "
                        "yarış öncesi senkronizasyonu duraklatın veya OUTPUT_DIR'i değiştirin."
                    )
                )

            elif msg_type == "backup_status":
                # MON-02 (madde 20): ikincil kaydın durumu -- birincili
                # ETKİLEMEZ, yalnız gösterge güncellenir.
                if msg.get("active"):
                    self.backup_label.config(
                        text=f"● YEDEK AKTİF: {os.path.basename(msg.get('path', ''))}",
                        fg="#10B981",
                    )
                else:
                    detail = msg.get("detail")
                    text = "○ YEDEK KAYIT HATASI" if detail else "○ Yedek kayıt kapalı"
                    if detail:
                        text += f": {detail}"
                    self.backup_label.config(text=text, fg="#F59E0B" if detail else "#475569")

            elif msg_type == "worker_crashed":
                # MON-01: worker kendi traceback'ini events log'a zaten yazdı;
                # burada ekstra bir şey yapmaya gerek yok -- _refresh_worker_health
                # thread'in artık is_alive() olmadığını görüp KAYIT DURDU'ya
                # geçecek/yeniden başlatmayı deneyecek.
                pass

        if self.last_packet_time is not None and (now - self.last_packet_time) > LINK_TIMEOUT_SEC:
            self.link_connected = False

        self._refresh_status_badge()
        self._refresh_interval_indicator(now)
        self._refresh_worker_health()
        self._refresh_stale_state(now)
        self._refresh_max_gap_indicator()
        self.counters_label.config(
            text=f"Kabul: {self.packet_count} | Format hatası: {self.parse_error_count} "
            f"| Aralık hatası: {self.range_error_count} | Tekrar (dedup): {self.dedup_count}"
        )
        self.row_count_label.config(text=f"{self.packet_count} satır")

        self.speed_history = trim_history_window(self.speed_history, config.GRAPH_WINDOW_SEC)
        self._redraw_graph()
        self._refresh_raw_panel()  # Y26 — panel kapalıysa hemen döner

        self.root.after(GUI_POLL_MS, self.update_gui)

    def _refresh_stale_state(self, now):
        """MON-05 (madde 49): son geçerli satırdan bu yana config.STALE_DATA_SEC
        geçtiyse (veya hiç veri gelmediyse) TÜM kartlar "--" gösterip
        soluklaşır; veri geri geldiğinde normale döner. Yalnız GÖSTERİMİ
        etkiler, kayıt davranışına dokunmaz."""
        is_stale, message = compute_stale_display(self.last_packet_time, now, config.STALE_DATA_SEC)

        if is_stale:
            for card in self._metric_cards:
                card.set_stale(True, message)
            self._data_is_stale = True
        elif self._data_is_stale:
            # Yalnız geçiş anında (bayat -> taze) çalışır -- bu turda ilgili
            # kartlar zaten yukarıdaki mesaj işleme sırasında set_value/
            # set_display ile taze içerik almış olur (bkz. MetricCard.set_stale
            # yorumu); burada yalnız renk/bar sıfırlanır.
            for card in self._metric_cards:
                card.set_stale(False)
            self._data_is_stale = False

    def _refresh_max_gap_indicator(self):
        gap_sec = max_consecutive_gap_sec(self.all_timestamps_ms)
        self.max_gap_label.config(text=f"Maks. ardışık zaman farkı: {gap_sec:.1f} sn")
        if gap_sec >= 5.0:
            self.max_gap_label.config(bg="#EF4444", fg="white")
        else:
            self.max_gap_label.config(bg=self._default_interval_bg, fg="#94A3B8")

    def _refresh_worker_health(self):
        worker_alive = self.worker_thread.is_alive()
        state = compute_worker_health_state(
            worker_alive, self.heartbeat.seconds_since_beat(), self.worker_restart_count
        )

        if state["should_restart"]:
            self.worker_restart_count += 1
            print(
                f"Worker öldü, yeniden başlatılıyor (deneme "
                f"{self.worker_restart_count}/{MAX_WORKER_RESTARTS})"
            )
            self._start_worker_thread(restart_attempt=self.worker_restart_count)
            # Yeniden başlatma hemen ardından tekrar canlı sayılır --
            # bu turda "KAYIT DURDU" göstermeye gerek yok.
            state = compute_worker_health_state(
                self.worker_thread.is_alive(), self.heartbeat.seconds_since_beat(),
                self.worker_restart_count
            )

        if state["permanently_failed"]:
            self.worker_permanently_failed = True

        self.recording_stopped = state["recording_stopped"]
        self._set_title()

        if self.recording_stopped:
            if not self._kayit_durdu_visible:
                self.kayit_durdu_label.pack(anchor="w", pady=(4, 0))
                self._kayit_durdu_visible = True
            self._blink_tick += 1
            if self._blink_tick % 3 == 0:  # ~600ms'de bir yanıp söner (200ms tick)
                self._blink_on = not self._blink_on
                if self._blink_on:
                    self.kayit_durdu_label.config(bg="#7F1D1D", fg="#FCA5A5")
                else:
                    self.kayit_durdu_label.config(bg="#EF4444", fg="white")
        elif self._kayit_durdu_visible:
            self.kayit_durdu_label.pack_forget()
            self._kayit_durdu_visible = False

    def _refresh_interval_indicator(self, now):
        if self.last_packet_time is None:
            self.interval_label.config(text="Son kayıt: --", bg=self._default_interval_bg, fg="#94A3B8")
            return

        elapsed = now - self.last_packet_time
        self.interval_label.config(text=f"Son kayıttan bu yana: {elapsed:.1f} sn")
        if elapsed > 5.0:
            self.interval_label.config(bg="#EF4444", fg="white")
        elif elapsed > 4.0:
            self.interval_label.config(bg="#F97316", fg="white")
        else:
            self.interval_label.config(bg=self._default_interval_bg, fg="#94A3B8")

    def _redraw_graph(self):
        # R2: x ekseni artık paketlerin KENDİ ts_sec'i (AKS telemetri zaman
        # damgası) — duvar-saati/varış zamanı DEĞİL. self.speed_history
        # insert_sorted_point ile sürekli ts_sec'e göre sıralı tutulur; bu
        # yüzden en yeni BİLİNEN ts, listenin SON elemanıdır.
        if self.speed_history:
            xs, ys = build_line_with_gaps(self.speed_history)
            self.speed_line.set_data(xs, ys)
            t_latest = self.speed_history[-1][0]
        else:
            self.speed_line.set_data([], [])
            t_latest = 0.0

        self.ax.set_xlim(max(0, t_latest - config.GRAPH_WINDOW_SEC), max(t_latest, config.GRAPH_WINDOW_SEC))
        self.canvas.draw_idle()

    def on_close(self):
        self.stop_event.set()
        self.root.destroy()


def parse_args(argv=None):
    """CLI argumanlarini ayristirir. --port verilirse config.py'deki
    SERIAL_PORT degerini ezer (bkz. resolve_serial_port)."""
    parser = argparse.ArgumentParser(description="TUFAN İzleme Merkezi")
    parser.add_argument(
        "--port",
        default=None,
        help="Seri port (orn. COM5, /dev/cu.usbserial-xxx). Verilirse "
        "config.py'deki SERIAL_PORT degerini ezer.",
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="MON-10 (madde 87): tkinter/matplotlib hic yuklenmeden, "
        "konsoldan periyodik durum basan headless kayit modunda calistir.",
    )
    return parser.parse_args(argv)


def resolve_serial_port(cli_port, config_port):
    """--port CLI argumani ile config.SERIAL_PORT arasinda oncelik cozer:
    CLI verilmisse (None degilse) o kazanir, aksi halde config degeri
    aynen kullanilir. Saf fonksiyon -- argparse/tkinter/config'e dokunmadan
    test edilebilir."""
    return cli_port if cli_port is not None else config_port


def main():
    args = parse_args()
    original_port = config.SERIAL_PORT
    config.SERIAL_PORT = resolve_serial_port(args.port, original_port)

    if args.port is None and original_port == "SIMULATE":
        # 2: --port verilmemis VE config SIMULATE ise, gercek kayda
        # gecebilmek icin mevcut portlari listele.
        print(format_port_list_message(list_available_ports()))
        print("Gercek kayit icin --port COMx verin (orn. python monitor.py --port COM5).")

    # MON-12 (madde 84): OUTPUT_DIR gercekten YAZILABILIR mi diye acilista
    # kontrol edilir -- degilse uygulama BASLATILMAZ (sessizce baska yere
    # yazma YERINE net bir hatayla cikilir).
    try:
        check_output_dir_writable(config.OUTPUT_DIR)
    except RuntimeError as exc:
        print(f"BAŞLATILAMADI: {exc}")
        sys.exit(1)
    print(f"Kayıt klasörü: {os.path.abspath(config.OUTPUT_DIR)}")

    # MON-14 (madde 85): kayıt klasörü bir bulut senkron klasöründeyse
    # konsola da net bir uyarı basılır (GUI/events log uyarısı serial_worker
    # içinde ayrıca yapılır) -- uygulama yine de BAŞLAR, yalnız uyarır.
    cloud_marker = detect_cloud_sync_folder(config.OUTPUT_DIR)
    if cloud_marker:
        print(
            f"UYARI: Kayıt klasörü bir bulut senkron klasöründe ({cloud_marker}). "
            "Yarış öncesi senkronizasyonu duraklatın veya OUTPUT_DIR'i değiştirin."
        )

    if args.no_gui:
        print("Headless (--no-gui) modu seçildi -- tkinter/matplotlib yüklenmeyecek.")
        run_headless()
        return

    try:
        run_gui()
    except ImportError as exc:
        # MON-10 (madde 87): tkinter veya matplotlib yüklenemezse uygulama
        # ÇIKMAZ -- otomatik olarak headless moda düşer, bunu NET yazar.
        print(
            f"UYARI: GUI başlatılamadı ({exc}) -- otomatik olarak HEADLESS "
            "moda düşülüyor. Grafik arayüz olmadan kayıt DEVAM EDECEK."
        )
        run_headless()


def run_gui():
    """MON-10: tkinter/matplotlib'i (lazy) yükler ve GUI mainloop'unu
    başlatır. Bu fonksiyon çağrılmadan monitor.py import etmek tkinter/
    matplotlib gerektirmez (bkz. _load_gui_dependencies)."""
    _load_gui_dependencies()
    root = tk.Tk()
    MonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
