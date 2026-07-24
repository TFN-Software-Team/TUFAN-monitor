import locale
import os
from datetime import datetime

import pytest

import config
import csv_logger
from csv_logger import (
    HEADER,
    detect_new_boot,
    format_event_line,
    format_record,
    is_replay_ts,
    make_events_log_filename,
    make_log_filename,
    parse_csv_line,
    parse_csv_line_verbose,
)


def test_parse_valid_line():
    parsed = parse_csv_line("CSV,12345,300,32,780,6283,42")
    assert parsed == {
        "timestamp_ms": 12345,
        "speed_kmh_x10": 300,
        "temp_c": 32,
        "pack_voltage_deciv": 780,
        "soc_hundredths": 6283,
        "seq": 42,
    }


def test_parse_invalid_prefix():
    assert parse_csv_line("TEL,12345,300,32,780,6283,42") is None
    assert parse_csv_line("DASH,xxx") is None


def test_parse_wrong_field_count():
    assert parse_csv_line("CSV,12345,300,32,780") is None


def test_format_record_values():
    parsed = parse_csv_line("CSV,12345,300,32,780,6283,42")
    record = format_record(parsed, battery_capacity_wh=1000.0)

    parts = record.split(";")
    assert len(parts) == 5
    assert parts[0] == "12345"
    assert parts[1] == "30.0"
    assert parts[2] == "32"
    assert parts[3] == "78.0"
    assert parts[4] == str(round(6283 / 10000 * 1000.0))


def test_format_record_energy_full_capacity_at_soc_10000():
    # soc=10000 (100%) -> kalan_enerji_Wh == kapasitenin tamamı
    parsed = parse_csv_line("CSV,0,0,0,0,10000,0")
    record = format_record(parsed, battery_capacity_wh=1000.0)
    assert record.split(";")[4] == "1000"


def test_format_record_energy_half_capacity_at_soc_5000():
    # soc=5000 (%50) -> kalan_enerji_Wh == kapasitenin yarısı
    parsed = parse_csv_line("CSV,0,0,0,0,5000,0")
    record = format_record(parsed, battery_capacity_wh=1000.0)
    assert record.split(";")[4] == "500"


def test_format_record_energy_zero_at_soc_zero():
    # soc=0 -> kalan_enerji_Wh == 0
    parsed = parse_csv_line("CSV,0,0,0,0,0,0")
    record = format_record(parsed, battery_capacity_wh=1000.0)
    assert record.split(";")[4] == "0"


def test_format_record_energy_rounding_is_round_half_to_even():
    # format_record round() (Python yerleşik) kullanır: banker's rounding,
    # yani tam ,5 durumunda en yakın ÇİFT sayıya yuvarlar (round-half-even),
    # her zaman yukarı yuvarlamaz. Bu davranış kasıtlıdır (Python round()
    # ile aynı), aşağıdaki iki örnek bunu belgeler:
    #   6285/10000*1000 = 628.5 -> 628 (628 çift, aşağı yuvarlanır)
    #   6295/10000*1000 = 629.5 -> 630 (630 çift, yukarı yuvarlanır)
    parsed_down = parse_csv_line("CSV,0,0,0,0,6285,0")
    assert format_record(parsed_down, battery_capacity_wh=1000.0).split(";")[4] == "628"

    parsed_up = parse_csv_line("CSV,0,0,0,0,6295,0")
    assert format_record(parsed_up, battery_capacity_wh=1000.0).split(";")[4] == "630"


def test_header_format():
    assert HEADER == "zaman_ms;hiz_kmh;T_bat_C;V_bat_C;kalan_enerji_Wh"


def test_header_matches_spec_byte_for_byte():
    # 9.2.f: sartname ornegiyle bayt bayt ayni olmali (gizli karakter/encoding
    # farki olmadigindan emin olmak icin utf-8 bayt karsilastirmasi).
    assert HEADER.encode("utf-8") == b"zaman_ms;hiz_kmh;T_bat_C;V_bat_C;kalan_enerji_Wh"


def test_format_record_has_exactly_five_semicolon_fields_and_no_newline():
    parsed = parse_csv_line("CSV,12345,300,32,780,6283,42")
    record = format_record(parsed, battery_capacity_wh=1000.0)

    assert "\n" not in record
    assert record.count(";") == 4
    assert len(record.split(";")) == 5


def test_format_record_decimal_separator_is_dot_locale_independent():
    # Ondalik ayrac NOKTA olmali, sistem locale'inden bagimsiz (virgullu
    # ondalik kullanan bir locale altinda da davranis degismemeli).
    original = locale.setlocale(locale.LC_NUMERIC)
    try:
        comma_locale_set = False
        for candidate in ("de_DE.UTF-8", "German", "tr_TR.UTF-8", "Turkish"):
            try:
                locale.setlocale(locale.LC_NUMERIC, candidate)
                comma_locale_set = True
                break
            except locale.Error:
                continue
        if not comma_locale_set:
            pytest.skip("bu sistemde virgullu-ondalik bir locale bulunamadi")

        parsed = parse_csv_line("CSV,12345,305,32,781,6283,42")
        record = format_record(parsed, battery_capacity_wh=1000.0)

        assert "," not in record
        parts = record.split(";")
        assert parts[1] == "30.5"
        assert parts[3] == "78.1"
    finally:
        locale.setlocale(locale.LC_NUMERIC, original)


def test_make_events_log_filename_pattern(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    filename = make_events_log_filename()
    assert os.path.dirname(filename) == "logs"
    assert os.path.basename(filename).startswith("events_")
    assert filename.endswith(".log")
    assert not os.path.basename(filename).endswith("_SIM.log")
    assert (tmp_path / "logs").is_dir()


def test_make_log_filename_default_pattern_unchanged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    filename = make_log_filename()
    assert os.path.dirname(filename) == "logs"
    assert os.path.basename(filename).startswith("telem_")
    assert filename.endswith(".csv")
    assert not os.path.basename(filename).endswith("_SIM.csv")


# --- MON-12 (madde 84): config.OUTPUT_DIR gerçekten kullanılmalı -----------


def test_make_log_filename_uses_configured_output_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "OUTPUT_DIR", "custom_kayit_klasoru")
    filename = make_log_filename()
    assert os.path.dirname(filename) == "custom_kayit_klasoru"
    assert (tmp_path / "custom_kayit_klasoru").is_dir()


def test_make_events_log_filename_uses_configured_output_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "OUTPUT_DIR", "custom_kayit_klasoru")
    filename = make_events_log_filename()
    assert os.path.dirname(filename) == "custom_kayit_klasoru"


def test_make_log_filename_with_sim_suffix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    filename = make_log_filename(suffix="_SIM")
    assert os.path.basename(filename).startswith("telem_")
    assert filename.endswith("_SIM.csv")


def test_make_events_log_filename_with_sim_suffix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    filename = make_events_log_filename(suffix="_SIM")
    assert os.path.basename(filename).startswith("events_")
    assert filename.endswith("_SIM.log")


class _FixedDatetime(datetime):
    """datetime.now() sabit bir deger dondursun diye monkeypatch icin."""

    _fixed = datetime(2026, 7, 13, 14, 15, 30)

    @classmethod
    def now(cls, tz=None):
        return cls._fixed


def test_make_log_filename_same_second_collision_gets_counter_suffix(tmp_path, monkeypatch):
    # 9.2.g: ayni saniyede ikinci bir new-boot yeniden-acilisi ayni ismi
    # uretmemeli -- ilk dosyanin icerigi (mevcut kayit) sessizce
    # sifirlanmamali (truncate). make_log_filename saniye-cozunurluklu
    # saate ragmen benzersiz bir isim garanti etmeli.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(csv_logger, "datetime", _FixedDatetime)

    filename1 = make_log_filename()
    with open(filename1, "x", encoding="utf-8") as f:
        f.write(HEADER + "\ntelem-1-icerigi\n")

    filename2 = make_log_filename()

    assert filename1 != filename2, "ayni saniyede uretilen ikinci isim ilkiyle CAKISMAMALI"
    assert os.path.basename(filename1) == "telem_20260713_141530.csv"
    assert os.path.basename(filename2) == "telem_20260713_141530_2.csv"

    # ikinci cagri, ilk dosyaya hic dokunmamis olmali (icerik korunmus).
    with open(filename1, encoding="utf-8") as f:
        assert f.read() == HEADER + "\ntelem-1-icerigi\n"
    assert not os.path.exists(filename2), "make_log_filename dosyayi kendisi olusturmamali, yalniz ismi hesaplamali"

    # filename2 henuz diskte olusturulmadigi icin ayni ismi tekrar dondurur
    # (make_log_filename saf bir isim hesaplayicidir, dosyayi kendisi
    # yaratmaz) -- once onu da olustur, sonra ucuncu cagrinin _3'e
    # atladigini dogrula.
    with open(filename2, "x", encoding="utf-8"):
        pass
    filename3 = make_log_filename()
    assert filename3 not in (filename1, filename2)
    assert os.path.basename(filename3) == "telem_20260713_141530_3.csv"


def test_make_log_filename_sim_suffix_collision_counter_order(tmp_path, monkeypatch):
    # suffix (_SIM) sayac ekinden ONCE gelmeli: telem_..._SIM_2.csv,
    # telem_..._2_SIM.csv DEGIL.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(csv_logger, "datetime", _FixedDatetime)

    filename1 = make_log_filename(suffix="_SIM")
    with open(filename1, "x", encoding="utf-8"):
        pass

    filename2 = make_log_filename(suffix="_SIM")

    assert os.path.basename(filename1) == "telem_20260713_141530_SIM.csv"
    assert os.path.basename(filename2) == "telem_20260713_141530_SIM_2.csv"


def test_make_events_log_filename_same_second_collision_gets_counter_suffix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(csv_logger, "datetime", _FixedDatetime)

    filename1 = make_events_log_filename()
    with open(filename1, "x", encoding="utf-8") as f:
        f.write("olay-1-icerigi\n")

    filename2 = make_events_log_filename()

    assert filename1 != filename2
    assert os.path.basename(filename1) == "events_20260713_141530.log"
    assert os.path.basename(filename2) == "events_20260713_141530_2.log"

    with open(filename1, encoding="utf-8") as f:
        assert f.read() == "olay-1-icerigi\n"


def test_format_event_line_has_timestamp_prefix_and_message():
    from datetime import datetime

    line = format_event_line("SERI PORT KOPTU: test", when=datetime(2026, 1, 2, 3, 4, 5))
    assert line == "[2026-01-02 03:04:05] SERI PORT KOPTU: test"


def test_detect_new_boot_first_packet():
    assert detect_new_boot(None, 0) is False


def test_detect_new_boot_normal():
    assert detect_new_boot(41, 42) is False


def test_detect_new_boot_reset():
    assert detect_new_boot(500, 2) is True


def test_detect_new_boot_equal_seq_is_not_a_new_boot():
    # MON-13 (madde 109/1): eski "ikinci katman" (curr_seq < 10 and
    # prev_seq > 100) matematiksel olarak ULAŞILAMAZ ölü kod olduğundan
    # silindi -- tek koşul (curr_seq < prev_seq) davranışı DEĞİŞTİRMEDİ.
    # curr_seq == prev_seq (tekrar/eşit) bir boot DEĞİLDİR.
    assert detect_new_boot(100, 100) is False


def _simulate_boot_count(seq_ts_pairs):
    """serial_worker'daki prev_seq izleme mantigini tekrarlar: her (seq, ts)
    ciftini sirayla detect_new_boot'a verir, kac kez yeni-boot tetiklendigini
    sayar. ts burada yalniz okunabilirlik icin tasiniyor, detect_new_boot'a
    verilmiyor (fonksiyon zaten ts'i gormez)."""
    prev_seq = None
    boots = 0
    for seq, _ts in seq_ts_pairs:
        if detect_new_boot(prev_seq, seq):
            boots += 1
        prev_seq = seq
    return boots


def test_detect_new_boot_ignores_offline_buffer_zip_drain_replay():
    # AKS kontratı (ESP_AKS lib/Telemetry/Telemetry.cpp:sendStatus +
    # test_replay_then_live_seq_is_sequential_and_monotonic): seq yalnizca
    # gercek TX aninda artar, hem canli hem replay paketler icin — bu yuzden
    # 60 sn'lik kesinti sonrasi fermuar drenaji sirasinda seq HIC durmadan
    # artar, yalniz ts_ms replay paketlerinde (kesinti anindaki eski deger)
    # geriye siçrar. Bu senaryoda TEK BIR yeni dosya bile acilmamali.
    sequence = [
        (98, 98000), (99, 99000), (100, 100000),  # kesintiden hemen once
        # fermuar drenaji: 1 replay + 1 canli / tik, seq kesintisiz artar
        (101, 41000), (102, 100100),
        (103, 42000), (104, 100200),
        (105, 43000), (106, 100300),
        (107, 44000), (108, 100400),
        (109, 45000), (110, 100500),  # drenaj biter, artik hep canli
        (111, 100600), (112, 100700),
    ]
    assert _simulate_boot_count(sequence) == 0


def test_detect_new_boot_real_boot_after_running():
    sequence = [(100, 100000), (0, 1200)]
    assert _simulate_boot_count(sequence) == 1


def test_detect_new_boot_two_consecutive_real_boots():
    sequence = [
        (100, 100000),
        (0, 1200),      # 1. gercek boot
        (1, 2200),
        (50, 51000),
        (0, 900),       # 2. gercek boot
        (1, 1900),
    ]
    assert _simulate_boot_count(sequence) == 2


def test_is_replay_ts_first_sample_is_never_replay():
    assert is_replay_ts(None, 12345) is False


def test_is_replay_ts_true_when_ts_goes_backward():
    assert is_replay_ts(100000, 41000) is True


def test_is_replay_ts_false_when_ts_advances_or_holds():
    assert is_replay_ts(100000, 100500) is False
    assert is_replay_ts(100000, 100000) is False


# --- MON-03 (madde 50/69): alan bazlı aralık kapıları ------------------------


def test_parse_csv_line_rejects_speed_above_max():
    too_fast = f"CSV,0,{int((config.MAX_SPEED_KMH + 10) * 10)},32,780,6283,1"
    assert parse_csv_line(too_fast) is None


def test_parse_csv_line_rejects_negative_speed():
    assert parse_csv_line("CSV,0,-10,32,780,6283,1") is None


def test_parse_csv_line_accepts_speed_at_bounds():
    low = f"CSV,0,{int(config.MIN_SPEED_KMH * 10)},32,780,6283,1"
    high = f"CSV,0,{int(config.MAX_SPEED_KMH * 10)},32,780,6283,1"
    assert parse_csv_line(low) is not None
    assert parse_csv_line(high) is not None


def test_parse_csv_line_rejects_temp_out_of_range():
    too_cold = f"CSV,0,300,{config.MIN_TEMP_C - 1},780,6283,1"
    too_hot = f"CSV,0,300,{config.MAX_TEMP_C + 1},780,6283,1"
    assert parse_csv_line(too_cold) is None
    assert parse_csv_line(too_hot) is None


def test_parse_csv_line_accepts_temp_at_bounds():
    low = f"CSV,0,300,{config.MIN_TEMP_C},780,6283,1"
    high = f"CSV,0,300,{config.MAX_TEMP_C},780,6283,1"
    assert parse_csv_line(low) is not None
    assert parse_csv_line(high) is not None


def test_parse_csv_line_rejects_voltage_above_max():
    # şartname: batarya paketi 150 V'u aşamaz
    too_high = f"CSV,0,300,32,{int((config.MAX_VOLTAGE_V + 1) * 10)},6283,1"
    assert parse_csv_line(too_high) is None


def test_parse_csv_line_accepts_voltage_at_max_bound():
    at_max = f"CSV,0,300,32,{int(config.MAX_VOLTAGE_V * 10)},6283,1"
    assert parse_csv_line(at_max) is not None


def test_parse_csv_line_rejects_soc_out_of_range():
    over_100 = f"CSV,0,300,32,780,{int((config.MAX_SOC_PERCENT + 1) * 100)},1"
    negative = "CSV,0,300,32,780,-100,1"
    assert parse_csv_line(over_100) is None
    assert parse_csv_line(negative) is None


def test_parse_csv_line_rejects_negative_timestamp():
    assert parse_csv_line("CSV,-1,300,32,780,6283,1") is None


def test_parse_csv_line_verbose_success_has_no_reason():
    parsed, reason = parse_csv_line_verbose("CSV,12345,300,32,780,6283,42")
    assert parsed is not None
    assert reason is None


def test_parse_csv_line_verbose_wrong_field_count_is_parse_hatasi():
    parsed, reason = parse_csv_line_verbose("CSV,12345,300,32,780")
    assert parsed is None
    assert reason == "parse_hatasi"


def test_parse_csv_line_verbose_non_numeric_field_is_parse_hatasi():
    parsed, reason = parse_csv_line_verbose("CSV,abc,300,32,780,6283,42")
    assert parsed is None
    assert reason == "parse_hatasi"


def test_parse_csv_line_verbose_out_of_range_field_is_aralik_hatasi():
    too_fast = f"CSV,0,{int((config.MAX_SPEED_KMH + 10) * 10)},32,780,6283,1"
    parsed, reason = parse_csv_line_verbose(too_fast)
    assert parsed is None
    assert reason == "aralik_hatasi"


def test_parse_csv_line_verbose_non_csv_prefix_is_not_a_rejection():
    # "CSV," ile başlamayan satır bir RED değildir -- çağıran taraf zaten
    # bu fonksiyonu yalnız "CSV," öneki doğrulandıktan sonra çağırır.
    parsed, reason = parse_csv_line_verbose("LINK,DOWN")
    assert parsed is None
    assert reason is None
