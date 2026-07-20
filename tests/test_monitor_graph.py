"""R2 — grafik geç-veri (replay) desteği: sıralı ekleme, pencere kırpma ve
boşluk-kırma (gap-break) çizgi üretimi için saf fonksiyon testleri.

Bu testler tkinter/matplotlib'e dokunmaz; yalnızca monitor.py'deki saf
yardımcı fonksiyonları (insert_sorted_point, trim_history_window,
build_line_with_gaps) dogrudan test eder.
"""

import math

import monitor


def test_insert_sorted_point_keeps_list_sorted_by_ts():
    history = []
    monitor.insert_sorted_point(history, (10.0, 50.0))
    monitor.insert_sorted_point(history, (30.0, 55.0))
    monitor.insert_sorted_point(history, (20.0, 52.0))  # geç gelen replay noktası

    assert history == [(10.0, 50.0), (20.0, 52.0), (30.0, 55.0)]


def test_insert_sorted_point_late_replay_lands_in_historical_position():
    """Canlı akış ilerlerken (yüksek ts) geç gelen bir replay noktası
    (düşük ts) listenin SONUNA değil, doğru kronolojik konumuna girmeli."""
    history = [(100.0, 40.0), (101.0, 41.0), (105.0, 45.0)]
    monitor.insert_sorted_point(history, (102.0, 42.0))  # replay, aradaki bosluğu dolduruyor

    assert history == [
        (100.0, 40.0), (101.0, 41.0), (102.0, 42.0), (105.0, 45.0),
    ]


def test_trim_history_window_drops_points_older_than_window_from_latest_ts():
    history = [(0.0, 1.0), (5.0, 2.0), (9.0, 3.0), (10.0, 4.0)]
    trimmed = monitor.trim_history_window(history, window_sec=5.0)
    # en yeni ts=10.0 -> cutoff=5.0 -> 5.0 dahil, 0.0 disarida
    assert trimmed == [(5.0, 2.0), (9.0, 3.0), (10.0, 4.0)]


def test_trim_history_window_empty_list_is_noop():
    assert monitor.trim_history_window([], window_sec=10.0) == []


def test_build_line_with_gaps_no_gap_returns_data_unchanged():
    history = [(0.0, 10.0), (1.0, 11.0), (2.0, 12.0)]
    xs, ys = monitor.build_line_with_gaps(history, gap_threshold_sec=5.0)
    assert xs == [0.0, 1.0, 2.0]
    assert ys == [10.0, 11.0, 12.0]


def test_build_line_with_gaps_inserts_nan_break_for_large_gap():
    """>5 sn'lik dolmamış boşlukta çizgi KESİLMELİ (NaN breakpoint) —
    9.2.e/9.2.h: düz-çizgi interpolasyonu ile 'veri var' yanılsaması verilmez."""
    history = [(0.0, 10.0), (1.0, 11.0), (20.0, 30.0)]  # 1.0 -> 20.0: 19 sn bosluk
    xs, ys = monitor.build_line_with_gaps(history, gap_threshold_sec=5.0)

    assert xs[:2] == [0.0, 1.0]
    assert math.isnan(xs[2]) and math.isnan(ys[2])
    assert xs[3:] == [20.0]
    assert ys == [10.0, 11.0, ys[2], 30.0]


def test_build_line_with_gaps_gap_filled_by_replay_removes_break():
    """Replay noktası araya girince (insert_sorted_point ile) boşluk küçülür
    ve eşiğin altına inerse kırılma noktası KAYBOLMALI."""
    history = [(0.0, 10.0), (1.0, 11.0), (20.0, 30.0)]
    monitor.insert_sorted_point(history, (3.0, 12.0))  # kısmi replay doldurma

    xs, ys = monitor.build_line_with_gaps(history, gap_threshold_sec=5.0)
    # 0,1,3 arasi bosluk yok (<=5s); 3 -> 20 hala >5s -> kirilma kalir.
    assert xs[:3] == [0.0, 1.0, 3.0]
    assert math.isnan(xs[3]) and math.isnan(ys[3])
    assert xs[4:] == [20.0]


def test_build_line_with_gaps_empty_history_returns_empty_lists():
    xs, ys = monitor.build_line_with_gaps([])
    assert xs == []
    assert ys == []


def test_build_line_with_gaps_single_point_no_break():
    xs, ys = monitor.build_line_with_gaps([(5.0, 42.0)])
    assert xs == [5.0]
    assert ys == [42.0]
