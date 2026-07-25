# TUFAN İzleme Merkezi (TUFAN-Monitor)

UKS aracından gelen seri telemetriyi okuyup yönetmelik madde 9.2.f/g/h
kapsamındaki "izleme merkezi" CSV kayıt işlevini karşılayan bağımsız
masaüstü uygulaması. Bu proje UKS/AKS repolarından bağımsızdır.

## Kurulum

```
pip install -r requirements.txt
```

## Çalıştırma

```
python monitor.py
```

Uygulama `logs/` klasörüne `telem_YYYYMMDD_HHMMSS.csv` adında bir dosya
yazar ve her CSV satırını ekrana özetler.

Durdurmak için `Ctrl+C` kullanın; açık dosya flush edilip kapatılır.

## Port ayarı

`config.py` içinde `SERIAL_PORT` değerini bilgisayarınızdaki gerçek COM
portuyla değiştirin (örn. `"COM5"`).

Alternatif olarak `config.py`'yi hiç değiştirmeden, çalıştırırken
`--port` ile geçici olarak COM portu verebilirsiniz — verilirse
`config.SERIAL_PORT` değerini o oturum için ezer:

```
python monitor.py --port COM5
```

`--port` verilmez ve `config.py` içinde `SERIAL_PORT = "SIMULATE"` ise,
uygulama başlangıçta sistemde görünen seri portları (pyserial
`list_ports`) konsola listeler ve gerçek kayıt için `--port COMx`
verilmesi gerektiğini hatırlatır.

## Aynı anda iki izleme (UKS terminali + Monitor) — Y26

**Bir seri portu aynı anda yalnızca TEK program açabilir.** Bu bir kod
eksiği değil, işletim sistemi kısıtıdır: port bir programa verildiğinde
diğeri onu açamaz ("access denied" / "port meşgul"). Bu yüzden UKS
terminal-izleme ile TUFAN-Monitor GUI'sini aynı porta aynı anda
bağlayamazsınız.

### Seçenek 1 (ÖNERİLEN): yalnız TUFAN-Monitor kullanın

Monitor, UKS terminalinin gösterdiği her şeyi zaten gösteriyor — üstelik
grafik ve kayıtla birlikte. Ayrıca ham satırları görmek için ayrı bir
terminale de ihtiyacınız yok:

> **Ham Veri paneli:** Alt bardaki **"Ham Veri (F2)"** düğmesi (veya **F2**
> tuşu) hattan gelen ham satırları (`CSV,...`, `LINK,...` vb.) kaydıran bir
> panelde gösterir. Son ~50 satır tutulur, panel **salt-okunurdur** ve
> **kayıt davranışını etkilemez**. Performans için varsayılan olarak
> **kapalıdır**; kapalıyken satırlar yalnızca tampona yazılır, ekran
> güncellenmez.

Yarış sırasında Monitor tek başına yeterlidir.

### Seçenek 2: iki ayrı USB-seri dönüştürücü + iki UKS kartı

Biri terminale, diğeri Monitor'e bağlanır. Her instance farklı bir portla
çalıştırılır:

```
python monitor.py --port COM5
python monitor.py --port COM7
```

Pratik değil (iki kart + iki dönüştürücü gerekir) ama mümkündür. Kayıt
dosyası adları saniye çözünürlüklü olduğundan aynı anda başlatılan iki
instance çakışmaz — ikinci dosya `_2` ekiyle açılır.

## Headless (GUI'siz) mod — madde 87

tkinter veya matplotlib bir laptopta açılamazsa (sürücü sorunu, eksik Tk
kurulumu vb.) kayıt YİNE DE tutulmalı — grafik arayüz kaybı, 9.2.g'nin
kaydı GÖSTERMEME değil kaydı hiç TUTAMAMA riskine dönüşmemeli.

```
python monitor.py --no-gui
```

Bu modda tkinter/matplotlib **hiç import edilmez** (modül seviyesinde
değil, yalnızca GUI seçildiğinde yüklenir — bkz. `monitor._load_gui_dependencies`).
Konsola 5 saniyede bir özet basılır:

```
[headless] satır: 128 | son zaman_ms: 64231 | maks ardışık fark: 1.0 sn | durum: BAĞLI
```

`Ctrl+C` ile durdurulur; açık dosya flush edilip kapatılır. MON-02 yedek
kayıt yolu (`BACKUP_OUTPUT_DIR`) bu modda da aynen çalışır.

**Otomatik düşüş:** `--no-gui` verilmeden çalıştırılıp da tkinter/matplotlib
yüklenemezse (import hatası), uygulama ÇIKMAZ — bunu net bir konsol
uyarısıyla bildirip otomatik olarak headless moda düşer.

## SIMULATE modu

`config.py` içinde `SERIAL_PORT = "SIMULATE"` iken uygulama gerçek bir
seri porta bağlanmaz; `monitor.MockSerial` ile sahte telemetri paketleri
üretir. Bu mod geliştirme/deneme amaçlıdır — üretilen veri GERÇEK DEĞİLDİR
ve bu sessizce geçilmez:

- Pencere başlığına kalıcı `[SİMÜLASYON — GERÇEK VERİ DEĞİL]` ibaresi
  eklenir (CONFIG_CONFIRMED uyarısıyla aynı yerde; ikisi aynı anda
  geçerliyse ikisi de görünür).
- Konsola başlangıçta belirgin bir uyarı basılır.
- O oturumda yazılan dosyalar `_SIM` ekiyle işaretlenir:
  `logs/telem_YYYYMMDD_HHMMSS_SIM.csv` ve
  `logs/events_YYYYMMDD_HHMMSS_SIM.log`. Yeni-boot tespiti sırasında
  açılan ikinci (veya sonraki) dosyalar da bu eki taşır.

**Gerçek kayıt veya jüri provası öncesi** `config.py` içindeki
`SERIAL_PORT` değeri mutlaka gerçek COM portuna çevrilmelidir; aksi halde
kaydedilen veri sahte olur.

## Batarya kapasitesi

`config.py` içinde `BATTERY_CAPACITY_WH` değeri, kalan enerji (Wh)
hesaplamasında kullanılır. Değer **8700.0 Wh** (100 Ah paket, nominal
kapasite) olarak girilmiş ve batarya ekibi tarafından teyit edilmiştir
(2026-07, bkz. `config.py: CONFIG_CONFIRMED = True`).

## Çıktı formatı

`logs/telem_YYYYMMDD_HHMMSS.csv` dosyası şu başlıkla başlar:

```
zaman_ms;hiz_kmh;T_bat_C;V_bat_C;kalan_enerji_Wh
```

Her satır bir telemetri örneğidir.

Araç sıfırlanıp (yeni boot) seri akışta seq sayacı geriye sıçradığında,
uygulama otomatik olarak yeni bir log dosyasına geçer ve konsola bunu
bildirir.

## Yönetmelik uyumu

Bu uygulama, yönetmelik madde 9.2.f/g/h'de tanımlanan "izleme merkezi"nin
CSV kayıt işlevini karşılar (seri telemetrinin sürekli CSV'ye kaydedilmesi).

## Seri port kopması / yeniden bağlanma

Seri port fiziksel olarak koparsa (USB kablosunun çekilmesi, anten sökülmesi
vb.) uygulama KAPANMAZ:

- Açık log dosyası olduğu gibi kalır, hiçbir veri kaybolmaz.
- Port her 2 saniyede bir yeniden açılmaya çalışılır (`RECONNECT_INTERVAL_SEC`,
  `monitor.py`).
- GUI'deki durum rozeti "SERİ PORT KOPUK" (gri) olarak değişir; bu, telemetri
  linkinin koptuğunu belirten "KOPUK" (kırmızı) durumundan ayrı ve
  önceliklidir.
- Port geri geldiğinde kayıt AYNI dosyada devam eder. Yeni bir dosyaya geçiş
  yalnızca `csv_logger.detect_new_boot` gerçek bir araç sıfırlanmasını
  (seq geriye sıçraması/sıfırlanması) tespit ettiğinde olur — bağlantı
  kopması tek başına yeni dosya açtırmaz.
- Tüm port ve link olayları (`logs/events_YYYYMMDD_HHMMSS.log`) zaman
  damgalı olarak ayrı bir olay günlüğüne yazılır; bu dosya CSV şemasını
  etkilemez ve `logs/` altında olduğundan repoya girmez (`.gitignore`).
- **Jüri notu (9.2.e):** Yönetmelik madde 9.2.e, bağlantı yeniden
  sağlandığında aracın kesinti sırasında biriktirdiği kayıtların izleme
  merkezine aktarılmasını (replay) ve kaydın AYNI dosyada DEVAM etmesini
  öngörür. AKS bu replay'i "fermuar" yöntemiyle yapar: her verici turunda
  1 canlı + en fazla 1 tamponlanmış paket gönderilir; tamponlanmış
  paketler kesinti anındaki eski `zaman_ms` değerini taşır. Bu yüzden CSV
  dosyasında drenaj süresince `zaman_ms` kolonunun GERİYE sıçraması
  **beklenen ve şartnameye uygun** bir davranıştır — bir hata değildir.
  Monitor bunu gerçek araç sıfırlanmasından (`seq` sayacının geriye
  sıçraması/sıfırlanması) ayırt eder: replay sırasında `seq` kesintisiz
  ARTAR (bkz. `csv_logger.detect_new_boot`), bu yüzden yeni dosya
  AÇILMAZ; her replay tespiti ayrıca `logs/events_*.log` dosyasına
  zaman damgalı `REPLAY?` notu olarak düşülür (teşhis amaçlı, CSV
  şemasını etkilemez).

## Yer istasyonu (bu bilgisayar) kesintileri — madde 66

Yukarıdaki bölüm AKS/UKS tarafındaki (araç) kesintileri anlatır. Bunun
DIŞINDA, bu izleme merkezinin çalıştığı bilgisayarın kendisiyle ilgili bir
kesinti sınıfı daha vardır ve bu, AKS tarafından GÖRÜLEMEZ:

- Uygulamanın (`python monitor.py`) yeniden başlatılması (çökme, kapatıp
  açma, güncelleme).
- Bilgisayarın uykuya/hazırda bekletmeye geçmesi.
- USB portunun güç tasarrufuyla geçici olarak kesilmesi/sıfırlanması.

Bu durumlarda AKS'in kendisi kesintiyi FARK ETMEZ — link'i "UP" sanmaya
devam eder ve tamponlama (replay) TETİKLENMEZ, çünkü tamponlama AKS'in
kendi RF linkindeki bir kesintiye karşı tasarlanmıştır, yer istasyonunun
(bu bilgisayarın) kendisindeki bir kesintiye karşı değil. Sonuç: CSV
dosyasında telafisi olmayan, hiçbir replay ile doldurulmayacak bir boşluk
oluşur.

**Bu, monitor.py tarafından ÇÖZÜLEMEZ** (kök neden AKS/UKS tarafındaki bir
tasarım sınırıdır) — ama monitor.py bunu GÖRÜNÜR kılar:

- Açılışta, `config.OUTPUT_DIR` içinde önceki bir oturumdan kalma bir
  `telem_*.csv` dosyası varsa, uygulama bunu tespit eder ve son
  `zaman_ms` değerini `logs/events_*.log` dosyasına yazar.
- Yeni oturumun İLK satırı geldiğinde, önceki oturumun son `zaman_ms`'i ile
  aradaki fark hesaplanır. Fark pozitifse (yani gerçekten bir AKS
  yeniden-boot'u değil de yalnızca yer istasyonu kesintisiyse), ekranda
  KALICI bir not ("⚠ YER İSTASYONU KESİNTİSİ: X sn veri kaybı") ve
  `logs/events_*.log`'da bir kayıt olarak görünür.
- Bu fark, durum çubuğundaki "Maks. ardışık zaman farkı" göstergesine
  (madde 67/68) dahil edilir.

**Operasyonel önlemler (yarış günü):**

- Laptop'ta uyku/hazırda bekletme KAPALI olmalı (bkz.
  `PROVA_KONTROL_LISTESI.md`).
- USB bağlantı noktasının güç tasarrufu (Aygıt Yöneticisi → USB Kök
  Hub → Güç Yönetimi → "Bilgisayarın bu aygıtı kapatmasına izin ver"
  KAPALI) devre dışı bırakılmalı.
- Uygulama yarışın ORTASINDA (zorunlu olmadıkça) yeniden BAŞLATILMAMALI —
  her yeniden başlatma, yukarıdaki telafisiz boşluk riskini taşır.

## Kayıt klasörü bir bulut senkron klasöründe olmamalı — madde 85

`logs/` klasörü (veya `config.OUTPUT_DIR` neyi gösteriyorsa) OneDrive,
Dropbox, Google Drive veya iCloud gibi bulut senkron klasörlerinden
BİRİNİN İÇİNDEYSE, kayıt sırasında şu risklerle karşılaşılabilir:

- Senkron istemcisi dosyayı okumak için geçici olarak KİLİTLEYEBİLİR.
- Senkron gecikmesi diskteki yazma performansını etkileyebilir.
- Aynı anda hem monitor.py hem de senkron istemcisi dosyaya erişmeye
  çalışabilir.

Uygulama açılışta yolu kontrol eder; eşleşme varsa ENGELLEMEZ, yalnızca net
bir uyarı basar (konsol, GUI başlığı altında kalıcı bir satır, ve
`logs/events_*.log`).

**Yarış günü — OneDrive duraklatma adımları (Windows):**

1. Görev çubuğundaki OneDrive bulut simgesine sağ tıklayın.
2. "Yardım ve Ayarlar" (Help & Settings) → "Senkronizasyonu Duraklat"
   (Pause syncing) → "2 saat" (veya yarış süresi kadar) seçin.
3. Alternatif/kalıcı çözüm: `config.py` içindeki `OUTPUT_DIR` değerini
   OneDrive'ın dışında bir klasöre (örn. `C:\TUFAN_KAYIT\logs`) çevirin.
4. Yarış bitince senkronizasyonu tekrar başlatmayı unutmayın.

## Teknik kontrol provası (60 sn anten sök-tak senaryosu)

Yönetmelik teknik kontrolünde jüriye gösterilecek prova adımları:

1. Uygulamayı çalışır ve telemetri akışı normal durumdayken başlatın.
   Durum rozeti **BAĞLI** (yeşil) olmalı, "Son kayıttan bu yana" göstergesi
   4 sn altında (varsayılan renk) kalmalı.
2. **t=0 sn:** Aracın anten/seri bağlantısını (USB veya UKS-AKS radyo linki)
   fiziksel olarak sökün.
   - Seri port kopmasıysa: rozet birkaç saniye içinde **SERİ PORT KOPUK**
     (gri) olur.
   - Yalnızca radyo linki kopmasıysa (`LINK,DOWN` alınırsa): rozet **KOPUK**
     (kırmızı) olur; port kendisi açık kaldığından "SERİ PORT KOPUK"
     görünmez.
   - Her iki durumda da "Son kayıttan bu yana" göstergesi 4 sn'de sarıya,
     5 sn'de kırmızıya döner (>4 sn / >5 sn eşikleri) — ancak CSV dosyasına
     bu süre boyunca yeni satır YAZILMAZ.
3. **t=0–60 sn arası:** Bağlantı kesikken dosya açık kalır, hiçbir pencere
   kapanmaz/çökmez. Konsolda ve `logs/events_*.log` dosyasında sırayla
   "SERI PORT KOPUK" / "LINK,DOWN alindi" ve 2 sn'de bir yeniden deneme
   kayıtları görülür.
4. **t=60 sn:** Anten/kabloyu geri takın.
   - Rozet **BAĞLI** durumuna döner (seri port önce **SERİ PORT KOPUK**'tan
     çıkar, ardından ilk `CSV,` paketiyle **KOPUK**'tan **BAĞLI**'ya geçer).
   - `logs/events_*.log` dosyasında "SERI PORT BAGLANDI" / "LINK,UP alindi"
     satırı görülür.
   - **Dosya adı DEĞİŞMEMİŞ olmalı** — kayıt aynı `telem_YYYYMMDD_HHMMSS.csv`
     dosyasında devam eder.
5. **Dosyada beklenen ts deseni (9.2.e):** Kopma sırasında AKS tarafında
   biriken paketler yeniden bağlanınca "fermuar" yöntemiyle (1 canlı + en
   fazla 1 tamponlanmış paket / verici turu) replay edilir. Bu yüzden
   `zaman_ms` kolonu drenaj süresince geçici olarak GERİYE döner (kopma
   anındaki `ts`'den daha eski değerler), sonra tekrar canlı akışa
   yakalanıp ileri gitmeye devam eder — bu, jüriye gösterilecek **beklenen
   ve şartnameye uygun** bir desendir, hata değildir. Bu, `seq` sütununun
   (dosyada tutulmaz, ama sıralama mantığının dayandığı alan) sürekli
   ARTAN kalmasıyla ayırt edilir — dolayısıyla YENİ BOOT tespit edilmez ve
   tek dosyada kesintisiz kayıt sağlanır. Her replay tespiti ayrıca
   `logs/events_*.log` dosyasına zaman damgalı `REPLAY?` notuyla düşülür.

Bu senaryonun otomatik testleri:
`tests/test_monitor_serial.py::test_serial_disconnect_reconnect_same_file_no_row_loss`
ve `tests/test_monitor_serial.py::test_replay_ts_regression_logs_tag_without_touching_csv_schema`.
