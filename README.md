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
