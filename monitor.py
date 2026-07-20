"""TUFAN İzleme Merkezi - ana uygulama (seri okuma + CSV yazma + GUI)."""

import argparse
import os
import queue
import threading
import time
import tkinter as tk

import serial
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
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
)

LINK_TIMEOUT_SEC = 3.0
GUI_POLL_MS = 200
RECONNECT_INTERVAL_SEC = 2.0  # 9.2.h: port kopunca 2 sn'de bir yeniden bağlanma denenir


def _simulate_suffix():
    """SIMULATE modunda sahte veri dosyalarını gerçek kayıttan ayırmak için
    dosya adı eki. Tek noktadan çözülür: open_log_file/open_events_log
    çağıran her yer (ilk açılış ve yeni-boot sonrası yeniden açılış dahil)
    bunu otomatik alır."""
    return "_SIM" if config.SERIAL_PORT == "SIMULATE" else ""


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


class MockSerial:
    def __init__(self):
        self.seq = 0
        self.start_time = time.time()
        self.speed = 50.0
        self.soc = 90.0
        self.temp = 35

    def readline(self):
        time.sleep(1.0)  # 1 saniye aralıklarla veri simüle et
        elapsed_ms = int((time.time() - self.start_time) * 1000)
        self.seq += 1
        
        import random
        self.speed += random.uniform(-3, 3)
        self.speed = max(0, min(config.MAX_SPEED_KMH - 20, self.speed))
        
        self.temp += random.choice([-1, 0, 1])
        self.temp = max(20, min(65, self.temp))
        
        self.soc -= 0.05
        self.soc = max(0, self.soc)
        
        voltage = 45.0 + (self.soc / 100.0) * 8.0
        
        speed_x10 = int(self.speed * 10)
        voltage_deciv = int(voltage * 10)
        soc_hundredths = int(self.soc * 100)
        
        # Rastgele LINK durumları
        if self.seq % 40 == 0:
            return b"LINK,DOWN\r\n"
        elif self.seq % 42 == 0:
            return b"LINK,UP\r\n"
            
        return f"CSV,{elapsed_ms},{speed_x10},{self.temp},{voltage_deciv},{soc_hundredths},{self.seq}\r\n".encode('utf-8')

    def close(self):
        pass


def open_serial_connection():
    if config.SERIAL_PORT == "SIMULATE":
        return MockSerial()
    return serial.Serial(config.SERIAL_PORT, config.SERIAL_BAUD, timeout=2)


def serial_worker(data_queue, stop_event, connect=open_serial_connection,
                   reconnect_interval=RECONNECT_INTERVAL_SEC):
    """Seri portu okur, CSV'ye yazar ve ayrıştırılmış verileri kuyruğa koyar.

    Bu fonksiyon ayrı bir thread'de çalışır; CSV dosyaya yazma işlemi
    burada kalır, GUI sadece data_queue üzerinden veri okur.

    9.2.g: port koparsa (USB çekilmesi vb.) uygulama ÇIKMAZ — açık log dosyası
    öyle kalır, `reconnect_interval` saniyede bir yeniden bağlanma denenir.
    Yeniden bağlanma AYNI dosyada devam eder; yeni dosyaya geçiş yalnızca
    `detect_new_boot` seq üzerinden gerçek bir yeni boot tespit ettiğinde olur
    (bkz. csv_logger.detect_new_boot) — bağlantı kopması tek başına yeni dosya
    açtırmaz.
    """
    log_file, filename = open_log_file()
    data_queue.put({"type": "filename", "name": filename})
    events_file = open_events_log()

    def log_event(message):
        events_file.write(format_event_line(message) + "\n")
        events_file.flush()

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

    prev_seq = None
    prev_ts_ms = None
    ser = None

    try:
        while not stop_event.is_set():
            if ser is None:
                try:
                    ser = connect()
                except serial.SerialException:
                    ser = None

                if ser is None:
                    data_queue.put({"type": "port_down", "ts": time.monotonic()})
                    log_event(
                        f"SERI PORT KOPUK: {config.SERIAL_PORT} acilamadi, "
                        f"{reconnect_interval:.0f} sn sonra tekrar denenecek"
                    )
                    if stop_event.wait(reconnect_interval):
                        break
                    continue

                data_queue.put({"type": "port_up", "ts": time.monotonic()})
                log_event(f"SERI PORT BAGLANDI: {config.SERIAL_PORT} | Dosya: {filename}")
                print(f"Port: {config.SERIAL_PORT} | Dosya: {filename}")

            try:
                raw = ser.readline()
            except (serial.SerialException, OSError) as exc:
                log_event(f"SERI PORT KOPTU: {exc}")
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
                parsed = parse_csv_line(line)
                if parsed is None:
                    continue

                curr_seq = parsed["seq"]
                curr_ts_ms = parsed["timestamp_ms"]
                if detect_new_boot(prev_seq, curr_seq):
                    log_file.flush()
                    log_file.close()
                    log_file, filename = open_log_file()
                    data_queue.put({"type": "filename", "name": filename})
                    log_event(f"YENI BOOT tespit edildi -> {filename}")
                    print(f"YENİ BOOT tespit edildi → {filename}")
                elif is_replay_ts(prev_ts_ms, curr_ts_ms):
                    # 9.2.e: AKS offline-buffer drenajı sırasında replay edilen
                    # paketler eski ts taşır ama seq artmaya devam eder (bkz.
                    # csv_logger.detect_new_boot) — CSV satırı normal şekilde
                    # yazılır, bu yalnızca teşhis/jüri için events log notudur.
                    log_event(
                        f"REPLAY? ts_ms geriye gitti: {prev_ts_ms} -> {curr_ts_ms} "
                        f"(seq {prev_seq} -> {curr_seq})"
                    )

                prev_seq = curr_seq
                prev_ts_ms = curr_ts_ms

                record = format_record(parsed, config.BATTERY_CAPACITY_WH)
                log_file.write(record + "\n")
                # 9.2.g: kayıt kanıt niteliğindedir — çökme anında veri
                # kaybı kabul edilemez, bu yüzden her satırda hemen
                # flush + fsync (OS sayfa önbelleğini de aşıp diske yazar).
                log_file.flush()
                os.fsync(log_file.fileno())

                data_queue.put(
                    {
                        "type": "csv",
                        "ts": time.monotonic(),
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
                log_event("LINK,DOWN alindi")

            elif line.startswith("LINK,UP"):
                data_queue.put({"type": "link_up", "ts": time.monotonic()})
                log_event("LINK,UP alindi")

            else:
                continue

    except Exception as exc:
        log_event(f"Beklenmeyen hata: {exc}")
        print(f"Seri okuma hatası: {exc}")
    finally:
        log_file.flush()
        log_file.close()
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
        log_event("Izleme durduruldu")
        events_file.flush()
        events_file.close()
        print(f"İzleme durduruldu. Dosya kaydedildi: {filename}")


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

    def _on_resize(self, event=None):
        self.update_bar()

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


class MonitorApp:
    """tkinter tabanlı TUFAN telemetri izleme penceresi."""

    def __init__(self, root):
        self.root = root
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
        self.root.title(title)
        self.root.geometry("1020x600")
        self.root.minsize(960, 520)
        self.root.configure(bg="#0B0F19")

        self.data_queue = queue.Queue()
        self.stop_event = threading.Event()

        self.packet_count = 0
        self.last_packet_time = None
        self.start_time = time.monotonic()
        self.speed_history = []  # [(t_sn, hiz_kmh), ...]

        self.port_connected = True  # ilk bağlanma denemesi sonucu ilk mesajla güncellenir
        self.link_connected = False

        self._build_widgets()

        self.worker_thread = threading.Thread(
            target=serial_worker, args=(self.data_queue, self.stop_event), daemon=True
        )
        self.worker_thread.start()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(GUI_POLL_MS, self.update_gui)

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

        # RIGHT PANEL (Metric Cards Column - 40% Width)
        right_panel = tk.Frame(main_container, bg="#0B0F19", width=300)
        right_panel.pack(side="right", fill="both", padx=(10, 0))
        right_panel.pack_propagate(False) # Stable layout

        # Define 1 column and 5 rows
        for r in range(5):
            right_panel.rowconfigure(r, weight=1)
        right_panel.columnconfigure(0, weight=1)

        # Create cards inside the column
        self.speed_card = MetricCard(right_panel, "HIZ", "km/h", 0, config.MAX_SPEED_KMH, "#00D2FF")
        self.speed_card.frame.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        self.soc_card = MetricCard(right_panel, "SoC", "%", 0, 100, "#10B981")
        self.soc_card.frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)

        self.voltage_card = MetricCard(right_panel, "GERİLİM", "V", 40.0, 60.0, "#A855F7")
        self.voltage_card.frame.grid(row=2, column=0, sticky="nsew", padx=4, pady=4)

        self.energy_card = MetricCard(right_panel, "KALAN ENERJİ", "Wh", 0, config.BATTERY_CAPACITY_WH, "#0EA5E9")
        self.energy_card.frame.grid(row=3, column=0, sticky="nsew", padx=4, pady=4)

        self.temp_card = MetricCard(right_panel, "SICAKLIK", "°C", 20, 80, "#F97316")
        self.temp_card.frame.grid(row=4, column=0, sticky="nsew", padx=4, pady=4)

        # Status frame (Footer)
        status_frame = tk.Frame(self.root, bg="#0B0F19")
        status_frame.pack(fill="x", padx=15, pady=(0, 10))

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

    def _refresh_status_badge(self):
        if not self.port_connected:
            self.status_badge.config(text="SERİ PORT KOPUK", bg="#64748B", fg="white")
        elif not self.link_connected:
            self.status_badge.config(text="KOPUK", bg="#EF4444", fg="white")
        else:
            self.status_badge.config(text="BAĞLI", bg="#10B981", fg="white")

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

                self.speed_history.append((msg["ts"] - self.start_time, msg["speed_kmh"]))
                self.link_connected = True

            elif msg_type == "link_down":
                self.link_connected = False

            elif msg_type == "link_up":
                self.last_packet_time = msg["ts"]
                self.link_connected = True

            elif msg_type == "port_down":
                self.port_connected = False
                self.link_connected = False

            elif msg_type == "port_up":
                self.port_connected = True

            elif msg_type == "filename":
                self.file_label.config(text=f"● KAYIT AKTİF: logs/{os.path.basename(msg['name'])}", fg="#10B981")

        if self.last_packet_time is not None and (now - self.last_packet_time) > LINK_TIMEOUT_SEC:
            self.link_connected = False

        self._refresh_status_badge()
        self._refresh_interval_indicator(now)

        cutoff = (now - self.start_time) - config.GRAPH_WINDOW_SEC
        self.speed_history = [(t, v) for t, v in self.speed_history if t >= cutoff]
        self._redraw_graph(now - self.start_time)

        self.root.after(GUI_POLL_MS, self.update_gui)

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

    def _redraw_graph(self, t_now):
        if self.speed_history:
            xs, ys = zip(*self.speed_history)
            self.speed_line.set_data(xs, ys)
        else:
            self.speed_line.set_data([], [])

        self.ax.set_xlim(max(0, t_now - config.GRAPH_WINDOW_SEC), max(t_now, config.GRAPH_WINDOW_SEC))
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
    return parser.parse_args(argv)


def resolve_serial_port(cli_port, config_port):
    """--port CLI argumani ile config.SERIAL_PORT arasinda oncelik cozer:
    CLI verilmisse (None degilse) o kazanir, aksi halde config degeri
    aynen kullanilir. Saf fonksiyon -- argparse/tkinter/config'e dokunmadan
    test edilebilir."""
    return cli_port if cli_port is not None else config_port


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


def main():
    args = parse_args()
    original_port = config.SERIAL_PORT
    config.SERIAL_PORT = resolve_serial_port(args.port, original_port)

    if args.port is None and original_port == "SIMULATE":
        # 2: --port verilmemis VE config SIMULATE ise, gercek kayda
        # gecebilmek icin mevcut portlari listele.
        print(format_port_list_message(list_available_ports()))
        print("Gercek kayit icin --port COMx verin (orn. python monitor.py --port COM5).")

    root = tk.Tk()
    MonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
