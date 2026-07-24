"""MON-06: tools/rapor.py -- 9.2.h uyum raporu testleri.

BİRİNCİL dosyanın (geliş sırasıyla yazılmış) yazma sırasını DEĞİŞTİRMEDEN,
zaman_ms'e göre sıralanmış bir türev dosya ve bir konsol raporu
üretildiğini doğrular.
"""

from tools import rapor

HEADER = "zaman_ms;hiz_kmh;T_bat_C;V_bat_C;kalan_enerji_Wh"


def _line(ts_ms, speed=30.0, temp=32, voltage=78.0, energy=5000):
    return f"{ts_ms};{speed:.1f};{temp};{voltage:.1f};{energy}"


def test_read_records_skips_header_and_parses_timestamp(tmp_path):
    csv_path = tmp_path / "telem_x.csv"
    csv_path.write_text(HEADER + "\n" + _line(1000) + "\n" + _line(500) + "\n", encoding="utf-8")
    records = rapor.read_records(str(csv_path))
    assert [r[0] for r in records] == [1000, 500]


def test_read_records_skips_malformed_lines(tmp_path):
    csv_path = tmp_path / "telem_x.csv"
    csv_path.write_text(
        HEADER + "\n" + _line(1000) + "\nbozuk;satir\n" + _line(2000) + "\n", encoding="utf-8"
    )
    records = rapor.read_records(str(csv_path))
    assert [r[0] for r in records] == [1000, 2000]


def test_sort_by_timestamp_orders_ascending_and_is_stable():
    records = [(3000, "c"), (1000, "a"), (2000, "b"), (1000, "a2")]
    sorted_records = rapor.sort_by_timestamp(records)
    assert [r[0] for r in sorted_records] == [1000, 1000, 2000, 3000]
    # kararlı sıralama: aynı ts_ms'li satırların göreli sırası korunur
    assert [r[1] for r in sorted_records[:2]] == ["a", "a2"]


def test_max_consecutive_gap_sec_computes_largest_sorted_gap():
    sorted_records = [(0, "x"), (1000, "x"), (9000, "x")]  # 8 sn boşluk
    assert rapor.max_consecutive_gap_sec(sorted_records) == 8.0


def test_max_consecutive_gap_sec_single_or_empty_is_zero():
    assert rapor.max_consecutive_gap_sec([]) == 0.0
    assert rapor.max_consecutive_gap_sec([(0, "x")]) == 0.0


def test_find_gaps_reports_only_gaps_above_threshold():
    sorted_records = [(0, "x"), (1000, "x"), (9000, "x"), (9500, "x")]
    gaps = rapor.find_gaps(sorted_records, gap_threshold_sec=5.0)
    assert len(gaps) == 1
    index, prev_ts, curr_ts, gap_sec = gaps[0]
    assert (index, prev_ts, curr_ts) == (2, 1000, 9000)
    assert gap_sec == 8.0


def test_derived_filename_appends_sirali_before_extension():
    assert rapor.derived_filename("logs/telem_20260724_120000.csv") == (
        "logs/telem_20260724_120000_sirali.csv"
    )


def test_write_sorted_csv_writes_header_and_lines_in_given_order(tmp_path):
    out_path = tmp_path / "out.csv"
    sorted_records = [(0, _line(0)), (1000, _line(1000))]
    rapor.write_sorted_csv(sorted_records, str(out_path))
    content = out_path.read_text(encoding="utf-8").splitlines()
    assert content[0] == HEADER
    assert content[1:] == [_line(0), _line(1000)]


def test_main_does_not_modify_primary_file_and_writes_derived_sorted_file(tmp_path, capsys):
    primary = tmp_path / "telem_test.csv"
    original_content = HEADER + "\n" + _line(2000) + "\n" + _line(1000) + "\n" + _line(9000) + "\n"
    primary.write_text(original_content, encoding="utf-8")

    rapor.main([str(primary)])

    # BİRİNCİL dosya (geliş sırası) hiç DEĞİŞMEMİŞ olmalı.
    assert primary.read_text(encoding="utf-8") == original_content

    derived = tmp_path / "telem_test_sirali.csv"
    assert derived.exists()
    derived_lines = derived.read_text(encoding="utf-8").splitlines()
    assert derived_lines[0] == HEADER
    assert [line.split(";")[0] for line in derived_lines[1:]] == ["1000", "2000", "9000"]

    out = capsys.readouterr().out
    assert "Toplam satır: 3" in out
    assert "Maks. ardışık zaman farkı: 7.0 sn" in out  # 2000 -> 9000 = 7 sn
    assert "aşan boşluk sayısı: 1" in out
