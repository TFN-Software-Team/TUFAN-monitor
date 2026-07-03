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

## Batarya kapasitesi

`config.py` içinde `BATTERY_CAPACITY_WH` değeri, kalan enerji (Wh)
hesaplamasında kullanılır.

**TODO:** Şu an yer tutucu bir değer (1000.0 Wh) içeriyor — ekip teknik
şartnamesindeki gerçek batarya kapasitesi buraya girilmelidir.

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
5. **Dosyada beklenen ts deseni:** Kopma sırasında AKS tarafında biriken
   paketler yeniden bağlanınca "replay" edilir — AKS logunda buna karşılık
   gelen "`N paket, ts [a..b] replay ediliyor`" satırıyla eşleşecek şekilde,
   `zaman_ms` kolonu geçici olarak GERİYE döner (kopma anındaki `ts`'den
   daha eski değerler), sonra tekrar canlı akışa yakalanıp ileri gitmeye
   devam eder. Bu, `seq` sütununun (dosyada tutulmaz, ama sıralama mantığının
   dayandığı alan) sürekli ARTAN kalmasıyla ayırt edilir — dolayısıyla YENİ
   BOOT tespit edilmez ve tek dosyada kesintisiz kayıt sağlanır.

Bu senaryonun otomatik testi: `tests/test_monitor_serial.py::test_serial_disconnect_reconnect_same_file_no_row_loss`.
