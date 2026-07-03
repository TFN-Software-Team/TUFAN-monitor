"""TUFAN İzleme Merkezi - ana uygulama (seri okuma + CSV yazma + GUI)."""

import os
import queue
import threading
import time
import tkinter as tk

import serial
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

import config
from csv_logger import (
    HEADER,
    detect_new_boot,
    format_event_line,
    format_record,
    make_events_log_filename,
    make_log_filename,
    parse_csv_line,
)

LINK_TIMEOUT_SEC = 3.0
GUI_POLL_MS = 200
RECONNECT_INTERVAL_SEC = 2.0  # 9.2.h: port kopunca 2 sn'de bir yeniden bağlanma denenir


def open_log_file():
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    filename = make_log_filename()
    f = open(filename, "w", encoding="utf-8")
    f.write(HEADER + "\n")
    f.flush()
    return f, filename


def open_events_log():
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    filename = make_events_log_filename()
    return open(filename, "a", encoding="utf-8")


def open_serial_connection():
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

    prev_seq = None
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

            try:
                line = raw.decode("utf-8", errors="ignore").strip()
            except UnicodeDecodeError:
                continue

            if not line:
                continue

            if line.startswith("CSV,"):
                parsed = parse_csv_line(line)
                if parsed is None:
                    continue

                curr_seq = parsed["seq"]
                if detect_new_boot(prev_seq, curr_seq):
                    log_file.flush()
                    log_file.close()
                    log_file, filename = open_log_file()
                    data_queue.put({"type": "filename", "name": filename})
                    log_event(f"YENI BOOT tespit edildi -> {filename}")
                    print(f"YENİ BOOT tespit edildi → {filename}")

                prev_seq = curr_seq

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
        self.root.title(title)
        self.root.geometry("800x500")
        self.root.minsize(640, 420)

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
        gauge_frame = tk.Frame(self.root)
        gauge_frame.pack(fill="x", padx=10, pady=10)
        gauge_frame.columnconfigure(0, weight=1)
        gauge_frame.columnconfigure(1, weight=1)

        self.speed_value, _ = self._make_gauge(gauge_frame, "HIZ", "km/h", 0, 0)
        self.temp_value, _ = self._make_gauge(gauge_frame, "SICAKLIK", "°C", 0, 1)
        self.voltage_value, _ = self._make_gauge(gauge_frame, "GERİLİM", "V", 1, 0)
        self.soc_value, _ = self._make_gauge(gauge_frame, "SoC", "%", 1, 1)

        energy_frame = tk.Frame(gauge_frame, relief="groove", borderwidth=2, padx=10, pady=8)
        energy_frame.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=4, pady=4)
        self.energy_label = tk.Label(
            energy_frame, text="KALAN ENERJİ: -- Wh", font=("Segoe UI", 14, "bold")
        )
        self.energy_label.pack()

        status_frame = tk.Frame(self.root)
        status_frame.pack(fill="x", padx=10, pady=(0, 5))

        self.status_badge = tk.Label(
            status_frame,
            text="KOPUK",
            font=("Segoe UI", 12, "bold"),
            bg="#c0392b",
            fg="white",
            width=10,
            padx=8,
            pady=4,
        )
        self.status_badge.pack(side="left")

        self.file_label = tk.Label(status_frame, text="Dosya: --")
        self.file_label.pack(side="left", padx=15)

        self.packet_label = tk.Label(status_frame, text="Alınan paket: 0")
        self.packet_label.pack(side="left", padx=15)

        # 9.2.h: ≤5 sn örnekleme aralığı göstergesi — CSV'ye ek satır yazmaz,
        # yalnızca son kayıttan bu yana geçen süreyi izler.
        self._default_interval_bg = status_frame.cget("bg")
        self.interval_label = tk.Label(status_frame, text="Son kayıt: --", padx=6)
        self.interval_label.pack(side="left", padx=15)

        self.figure = Figure(figsize=(7.5, 2.6), dpi=90)
        self.ax = self.figure.add_subplot(111)
        self.ax.set_ylim(0, config.MAX_SPEED_KMH)
        self.ax.set_xlim(0, config.GRAPH_WINDOW_SEC)
        self.ax.set_xlabel("Zaman (sn)")
        self.ax.set_ylabel("Hız (km/h)")
        (self.speed_line,) = self.ax.plot([], [], color="tab:blue")
        self.figure.tight_layout()

        self.canvas = FigureCanvasTkAgg(self.figure, master=self.root)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=5)

    def _make_gauge(self, parent, title, unit, row, col):
        frame = tk.Frame(parent, relief="groove", borderwidth=2, padx=10, pady=8)
        frame.grid(row=row, column=col, sticky="nsew", padx=4, pady=4)

        title_label = tk.Label(frame, text=title, font=("Segoe UI", 11, "bold"))
        title_label.pack(anchor="w")

        value_row = tk.Frame(frame)
        value_row.pack(anchor="w")

        value_label = tk.Label(value_row, text="--", font=("Segoe UI", 28, "bold"))
        value_label.pack(side="left")

        unit_label = tk.Label(value_row, text=f" {unit}", font=("Segoe UI", 11))
        unit_label.pack(side="left", anchor="s")

        return value_label, unit_label

    def _refresh_status_badge(self):
        if not self.port_connected:
            # Fiziksel port kaybı (USB çekilmesi vb.) — link durumundan ayrı
            # ve daha öncelikli gösterilir.
            self.status_badge.config(text="SERİ PORT KOPUK", bg="#7f8c8d")
        elif not self.link_connected:
            self.status_badge.config(text="KOPUK", bg="#c0392b")
        else:
            self.status_badge.config(text="BAĞLI", bg="#27ae60")

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

                self.speed_value.config(text=f"{msg['speed_kmh']:.1f}")
                self.temp_value.config(text=f"{msg['temp_c']}")
                self.voltage_value.config(text=f"{msg['voltage_v']:.1f}")
                self.soc_value.config(text=f"{msg['soc_percent']:.1f}")
                self.energy_label.config(text=f"KALAN ENERJİ: {msg['energy_wh']} Wh")
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
                self.file_label.config(text=f"Dosya: {os.path.basename(msg['name'])}")

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
            self.interval_label.config(text="Son kayıt: --", bg=self._default_interval_bg)
            return

        elapsed = now - self.last_packet_time
        self.interval_label.config(text=f"Son kayıttan bu yana: {elapsed:.1f} sn")
        if elapsed > 5.0:
            self.interval_label.config(bg="#c0392b", fg="white")
        elif elapsed > 4.0:
            self.interval_label.config(bg="#f1c40f", fg="black")
        else:
            self.interval_label.config(bg=self._default_interval_bg, fg="black")

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


def main():
    root = tk.Tk()
    MonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
