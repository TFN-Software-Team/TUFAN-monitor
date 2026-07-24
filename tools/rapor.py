"""tools/rapor.py -- 9.2.h uyum raporu ve zaman-sıralı türev dosya üretici.

Kullanım:
    python tools/rapor.py logs/telem_20260724_120000.csv

BİRİNCİL kayıt dosyasına DOKUNMAZ (yalnız okur) -- zaman_ms'e göre
SIRALANMIŞ bir türev dosya (ör. telem_..._sirali.csv) üretir ve bir konsol
raporu basar. Bu, jüriye teknik kontrolde ARDIŞIK zaman_ms farklarının
(9.2.h: >5 sn boşluk olmamalı) gerçek -- varış sırasına değil, zaman
damgasına göre sıralı -- halini göstermek içindir.

monitor.py'nin CANLI kayıt akışı bilerek GELİŞ SIRASIYLA yazmaya devam eder
(bkz. monitor.serial_worker'daki "R2 KARAR" yorumu) -- kanıt bütünlüğü
için birincil dosyanın yazma sırası hiçbir zaman değiştirilmez. Sıralama
işlemi yalnızca bu script'in ürettiği türev dosyada yapılır.
"""

import argparse
import os
import sys

# tools/ altından çalıştırıldığında proje kökündeki csv_logger'ı bulabilmek için.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from csv_logger import HEADER  # noqa: E402  (sys.path ayarından sonra import)

GAP_THRESHOLD_SEC = 5.0


def read_records(csv_path):
    """CSV dosyasını okur, [(zaman_ms, ham_satır), ...] döner (GELİŞ
    SIRASIYLA, dosyadaki haliyle). Başlık satırını atlar; alan sayısı
    yanlış veya zaman_ms sayısal olmayan satırları sessizce atlar (bu bir
    rapor/teşhis aracıdır, canlı ayrıştırmanın kapılarını tekrar etmez)."""
    records = []
    with open(csv_path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split(";")
        if len(fields) != 5:
            continue
        try:
            ts_ms = int(fields[0])
        except ValueError:
            continue
        records.append((ts_ms, line))
    return records


def sort_by_timestamp(records):
    """[(ts_ms, satır), ...] listesini ts_ms'e göre KARARLI (stable) sıralar
    -- aynı ts_ms'li satırların BİRİNCİL dosyadaki göreli sırası korunur."""
    return sorted(records, key=lambda r: r[0])


def max_consecutive_gap_sec(sorted_records):
    """SIRALI [(ts_ms, satır), ...] listesindeki en büyük ARDIŞIK farkı
    saniye cinsinden döner. 2'den az kayıtta 0.0 döner."""
    if len(sorted_records) < 2:
        return 0.0
    max_gap_ms = 0
    for i in range(1, len(sorted_records)):
        gap = sorted_records[i][0] - sorted_records[i - 1][0]
        if gap > max_gap_ms:
            max_gap_ms = gap
    return max_gap_ms / 1000.0


def find_gaps(sorted_records, gap_threshold_sec=GAP_THRESHOLD_SEC):
    """SIRALI kayıtlar arasında gap_threshold_sec'i AŞAN ardışık boşlukları
    bulur. Döner: [(indeks, onceki_ts_ms, sonraki_ts_ms, fark_sn), ...]"""
    gaps = []
    threshold_ms = gap_threshold_sec * 1000.0
    for i in range(1, len(sorted_records)):
        prev_ts = sorted_records[i - 1][0]
        curr_ts = sorted_records[i][0]
        gap_ms = curr_ts - prev_ts
        if gap_ms > threshold_ms:
            gaps.append((i, prev_ts, curr_ts, gap_ms / 1000.0))
    return gaps


def derived_filename(csv_path):
    """BİRİNCİL dosyanın adını DEĞİŞTİRMEDEN yanına '_sirali' ekiyle bir
    türev dosya adı üretir. Örn: telem_X.csv -> telem_X_sirali.csv"""
    base, ext = os.path.splitext(csv_path)
    return f"{base}_sirali{ext}"


def write_sorted_csv(sorted_records, out_path):
    """Sıralı kayıtları (orijinal satır metniyle, AYNEN) yeni bir dosyaya
    yazar. BİRİNCİL dosyaya hiç dokunmaz -- yalnız bu yeni türev dosyayı
    oluşturur/üzerine yazar."""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(HEADER + "\n")
        for _ts, line in sorted_records:
            f.write(line + "\n")


def format_report(csv_path, records, gaps, max_gap_sec):
    lines = [
        f"Kaynak dosya: {csv_path}",
        f"Toplam satır: {len(records)}",
        f"Maks. ardışık zaman farkı: {max_gap_sec:.1f} sn",
        f"{GAP_THRESHOLD_SEC:.0f} sn'yi aşan boşluk sayısı: {len(gaps)}",
    ]
    if gaps:
        lines.append(
            "Boşluklar (sıralı dosyadaki satır indeksi, önceki->sonraki zaman_ms, fark):"
        )
        for index, prev_ts, curr_ts, gap_sec in gaps:
            lines.append(f"  #{index}: {prev_ts} -> {curr_ts}  ({gap_sec:.1f} sn)")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="9.2.h uyum raporu: CSV'yi zaman_ms'e göre sıralar, "
        "5 sn'yi aşan ardışık boşlukları listeler. BİRİNCİL dosyaya dokunmaz."
    )
    parser.add_argument("csv_path", help="Birincil telem_*.csv dosyasının yolu")
    args = parser.parse_args(argv)

    records = read_records(args.csv_path)
    sorted_records = sort_by_timestamp(records)
    gaps = find_gaps(sorted_records)
    max_gap = max_consecutive_gap_sec(sorted_records)

    out_path = derived_filename(args.csv_path)
    write_sorted_csv(sorted_records, out_path)

    print(format_report(args.csv_path, records, gaps, max_gap))
    print(f"\nSıralı türev dosya yazıldı: {out_path}")


if __name__ == "__main__":
    main()
