"""R2 — grafik geç-veri (replay) desteği: sıralı ekleme, pencere kırpma ve
boşluk-kırma (gap-break) çizgi üretimi için saf fonksiyon testleri.

Bu testler tkinter/matplotlib'e dokunmaz; yalnızca monitor_core.py'deki saf
yardımcı fonksiyonları (insert_sorted_point, trim_history_window,
build_line_with_gaps) dogrudan test eder.
"""

import math

import monitor_core


def test_insert_sorted_point_keeps_list_sorted_by_ts():
    history = []
    monitor_core.insert_sorted_point(history, (10.0, 50.0))
    monitor_core.insert_sorted_point(history, (30.0, 55.0))
    monitor_core.insert_sorted_point(history, (20.0, 52.0))  # geç gelen replay noktası

    assert history == [(10.0, 50.0), (20.0, 52.0), (30.0, 55.0)]


def test_insert_sorted_point_late_replay_lands_in_historical_position():
    """Canlı akış ilerlerken (yüksek ts) geç gelen bir replay noktası
    (düşük ts) listenin SONUNA değil, doğru kronolojik konumuna girmeli."""
    history = [(100.0, 40.0), (101.0, 41.0), (105.0, 45.0)]
    monitor_core.insert_sorted_point(history, (102.0, 42.0))  # replay, aradaki bosluğu dolduruyor

    assert history == [
        (100.0, 40.0), (101.0, 41.0), (102.0, 42.0), (105.0, 45.0),
    ]


def test_trim_history_window_drops_points_older_than_window_from_latest_ts():
    history = [(0.0, 1.0), (5.0, 2.0), (9.0, 3.0), (10.0, 4.0)]
    trimmed = monitor_core.trim_history_window(history, window_sec=5.0)
    # en yeni ts=10.0 -> cutoff=5.0 -> 5.0 dahil, 0.0 disarida
    assert trimmed == [(5.0, 2.0), (9.0, 3.0), (10.0, 4.0)]


def test_trim_history_window_empty_list_is_noop():
    assert monitor_core.trim_history_window([], window_sec=10.0) == []


def test_build_line_with_gaps_no_gap_returns_data_unchanged():
    history = [(0.0, 10.0), (1.0, 11.0), (2.0, 12.0)]
    xs, ys = monitor_core.build_line_with_gaps(history, gap_threshold_sec=5.0)
    assert xs == [0.0, 1.0, 2.0]
    assert ys == [10.0, 11.0, 12.0]


def test_build_line_with_gaps_inserts_nan_break_for_large_gap():
    """>5 sn'lik dolmamış boşlukta çizgi KESİLMELİ (NaN breakpoint) —
    9.2.e/9.2.h: düz-çizgi interpolasyonu ile 'veri var' yanılsaması verilmez."""
    history = [(0.0, 10.0), (1.0, 11.0), (20.0, 30.0)]  # 1.0 -> 20.0: 19 sn bosluk
    xs, ys = monitor_core.build_line_with_gaps(history, gap_threshold_sec=5.0)

    assert xs[:2] == [0.0, 1.0]
    assert math.isnan(xs[2]) and math.isnan(ys[2])
    assert xs[3:] == [20.0]
    assert ys == [10.0, 11.0, ys[2], 30.0]


def test_build_line_with_gaps_gap_filled_by_replay_removes_break():
    """Replay noktası araya girince (insert_sorted_point ile) boşluk küçülür
    ve eşiğin altına inerse kırılma noktası KAYBOLMALI."""
    history = [(0.0, 10.0), (1.0, 11.0), (20.0, 30.0)]
    monitor_core.insert_sorted_point(history, (3.0, 12.0))  # kısmi replay doldurma

    xs, ys = monitor_core.build_line_with_gaps(history, gap_threshold_sec=5.0)
    # 0,1,3 arasi bosluk yok (<=5s); 3 -> 20 hala >5s -> kirilma kalir.
    assert xs[:3] == [0.0, 1.0, 3.0]
    assert math.isnan(xs[3]) and math.isnan(ys[3])
    assert xs[4:] == [20.0]


def test_build_line_with_gaps_empty_history_returns_empty_lists():
    xs, ys = monitor_core.build_line_with_gaps([])
    assert xs == []
    assert ys == []


def test_build_line_with_gaps_single_point_no_break():
    xs, ys = monitor_core.build_line_with_gaps([(5.0, 42.0)])
    assert xs == [5.0]
    assert ys == [42.0]


# --- MON-04 (madde 48): zaman_ms -> "dk:sn.ms" ------------------------------


def test_format_timestamp_ms_matches_spec_example():
    assert monitor_core.format_timestamp_ms(754567) == "12:34.567"


def test_format_timestamp_ms_zero():
    assert monitor_core.format_timestamp_ms(0) == "00:00.000"


def test_format_timestamp_ms_under_a_minute():
    assert monitor_core.format_timestamp_ms(4321) == "00:04.321"


def test_format_timestamp_ms_truncates_fractional_ms():
    assert monitor_core.format_timestamp_ms(1500.9) == "00:01.500"


# --- MON-06 (madde 67/68, 9.2.h): gerçek ardışık zaman farkı ----------------


def test_max_consecutive_gap_sec_empty_or_single_is_zero():
    assert monitor_core.max_consecutive_gap_sec([]) == 0.0
    assert monitor_core.max_consecutive_gap_sec([1000]) == 0.0


def test_max_consecutive_gap_sec_finds_largest_gap_in_sorted_list():
    # ardışık farklar: 1, 8, 0.5 sn -> maks 8 sn
    sorted_ts = [0, 1000, 9000, 9500]
    assert monitor_core.max_consecutive_gap_sec(sorted_ts) == 8.0


def test_max_consecutive_gap_sec_all_within_five_seconds():
    sorted_ts = [0, 2000, 4000, 6000]
    assert monitor_core.max_consecutive_gap_sec(sorted_ts) == 2.0


# --- MON-08 (madde 108): uzun yol kısaltma -----------------------------------


def test_truncate_path_for_display_short_path_unchanged():
    path = "C:\\logs\\telem_x.csv"
    assert monitor_core.truncate_path_for_display(path, max_len=60) == path


def test_truncate_path_for_display_long_path_keeps_head_and_tail():
    path = "C:\\Users\\ravzanur\\Desktop\\TUFAN-Monitor\\logs\\telem_20260724_120000.csv"
    truncated = monitor_core.truncate_path_for_display(path, max_len=40)
    assert len(truncated) <= 40
    assert "..." in truncated
    assert truncated.startswith(path[:5])
    assert truncated.endswith(path[-5:])


# --- MON-05 (madde 49): bayat veri gösterim kararı --------------------------


def test_compute_stale_display_no_data_yet_is_stale_with_no_message():
    is_stale, message = monitor_core.compute_stale_display(None, now=100.0, stale_threshold_sec=3.0)
    assert is_stale is True
    assert message == ""


def test_compute_stale_display_fresh_data_is_not_stale():
    is_stale, message = monitor_core.compute_stale_display(98.0, now=100.0, stale_threshold_sec=3.0)
    assert is_stale is False
    assert message == ""


def test_compute_stale_display_old_data_is_stale_with_elapsed_message():
    is_stale, message = monitor_core.compute_stale_display(87.6, now=100.0, stale_threshold_sec=3.0)
    assert is_stale is True
    assert message == "son veri: 12.4 sn önce"


def test_compute_stale_display_exactly_at_threshold_is_not_stale():
    # sınırda (== threshold) henüz bayat sayılmaz, > threshold gerekir.
    is_stale, _ = monitor_core.compute_stale_display(97.0, now=100.0, stale_threshold_sec=3.0)
    assert is_stale is False
