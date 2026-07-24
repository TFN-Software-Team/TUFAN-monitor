# Yarış Günü Kontrol Listesi (Prova)

Bu listeyi **yarış sabahı, sistemi ayağa kaldırmadan önce** yazılımcı
olmayan bir ekip üyesi tek başına doldurabilmeli. Her madde işaretlenene
kadar `BASLAT.bat`'ı çalıştırmayın.

## Bilgisayar / işletim sistemi

- [ ] Windows uyku/hazırda bekletme **KAPALI**
      (Ayarlar → Sistem → Güç ve Pil → Uyku → "Hiçbir zaman")
- [ ] Bulut senkronizasyonu (OneDrive/Dropbox/Google Drive/iCloud)
      **duraklatıldı** (bkz. `README.md` → "Kayıt klasörü bir bulut senkron
      klasöründe olmamalı")
- [ ] Disk boş alanı yeterli (**> 1 GB**)
- [ ] Sistem saati doğru (zaman damgalarının anlamlı olması için)
- [ ] Laptop pili dolu / şarjda (yarış süresince fişte kalmalı)

## Bağlantı / donanım

- [ ] Seri port numarası doğru (`config.py` → `SERIAL_PORT`, veya
      `--port COMx` ile geçici olarak verilecek)
- [ ] Yedek kayıt yolu (USB bellek) takılı ve `config.py` → `BACKUP_OUTPUT_DIR`
      içinde doğru şekilde ayarlı
- [ ] İkinci (yedek) dinleyici laptop hazır ve kurulu

## Yazılım ayarları

- [ ] **SIMULATE modu KAPALI** — pencere başlığında
      `[SİMÜLASYON — GERÇEK VERİ DEĞİL]` uyarısı GÖRÜNMÜYOR
      (görünüyorsa `config.py` → `SERIAL_PORT` gerçek bir COM portu değil)
- [ ] `BATTERY_CAPACITY_WH` değeri batarya ekibi tarafından teyitli
      (pencere başlığında `[BATARYA KAPASITESI TEYITSIZ]` uyarısı YOK)
- [ ] Sürüm/tag doğru (kullanılan kod, teknik kontrole hazırlanan sürümle
      aynı — `git status` / `git log -1` ile doğrulayın)

## Başlatma

- [ ] `BASLAT.bat` çift tıklanarak çalıştırıldı, pencere açıldı
- [ ] Durum rozeti **BAĞLI** (yeşil) oldu, veri akmaya başladı
- [ ] Durum çubuğunda (alt kısım) doğru port/baud ve kayıt dosyası yolu
      görünüyor

---

**Doğrulama:** Yazılımcı olmayan bir ekip üyesi bu listeyle sistemi TEK
BAŞINA ayağa kaldırabilmeli. Yarıştan önce süreli bir prova yapın (örn.
5 dakika içinde tüm maddeler tamamlanabilmeli).
