from csv_logger import HEADER, detect_new_boot, format_record, parse_csv_line


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


def test_header_format():
    assert HEADER == "zaman_ms;hiz_kmh;T_bat_C;V_bat_C;kalan_enerji_Wh"


def test_detect_new_boot_first_packet():
    assert detect_new_boot(None, 0) is False


def test_detect_new_boot_normal():
    assert detect_new_boot(41, 42) is False


def test_detect_new_boot_reset():
    assert detect_new_boot(500, 2) is True
