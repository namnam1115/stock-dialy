"""決算予定日の週末補正のテスト（EarningsCalendarAPIService）。

なぜこのテストがあるか:
  決算一覧に土日の決算予定日が表示される不具合があった。原因は、将来分が
  APIの予測日（前年実績＋約1年）で曜日がずれ、土日に落ちること。日本企業は
  土日に決算発表しないため、正規化時に週末を最寄りの平日へ寄せるよう修正した。
"""
from datetime import date, timedelta

from earnings_analysis.services.earnings_calendar_api import EarningsCalendarAPIService


def _first_weekday(target_wd):
    d = date(2026, 1, 1)
    while d.weekday() != target_wd:
        d += timedelta(days=1)
    return d


def test_saturday_moves_to_friday():
    sat = _first_weekday(5)
    assert EarningsCalendarAPIService._to_business_day(sat) == sat - timedelta(days=1)


def test_sunday_moves_to_monday():
    sun = _first_weekday(6)
    assert EarningsCalendarAPIService._to_business_day(sun) == sun + timedelta(days=1)


def test_weekday_is_unchanged():
    # 祝日を含まない通常営業週（2026-07-06 月〜07-10 金）で検証
    for day in range(6, 11):
        d = date(2026, 7, day)
        assert d.weekday() < 5
        assert EarningsCalendarAPIService._to_business_day(d) == d


def test_normalize_item_snaps_weekend_date_to_weekday():
    sat = _first_weekday(5)
    item = EarningsCalendarAPIService._normalize_item({
        'securities_code': '7203',
        'announcementDate': sat.isoformat(),
    })
    assert item is not None
    assert item['earnings_date'].weekday() < 5  # 平日に補正されている
    assert item['earnings_date'] == sat - timedelta(days=1)


def test_is_estimated_from_datestatus_confirmed():
    """dateStatus=confirmed は確定（is_estimated=False）。"""
    item = EarningsCalendarAPIService._normalize_item({
        'securities_code': '7203', 'announcementDate': '2026-07-08',
        'dateStatus': 'confirmed',
    })
    assert item['is_estimated'] is False


def test_is_estimated_from_datestatus_estimated():
    """dateStatus=estimated は予想（is_estimated=True）。"""
    item = EarningsCalendarAPIService._normalize_item({
        'securities_code': '7203', 'announcementDate': '2026-07-08',
        'dateStatus': 'estimated',
    })
    assert item['is_estimated'] is True


def test_is_estimated_defaults_when_only_estimated_date():
    """status 無し・予測日のみ → 予想扱い。確定日があれば確定扱い。"""
    est = EarningsCalendarAPIService._normalize_item({
        'securities_code': '7203', 'estimatedAnnouncementDate': '2026-07-08',
    })
    assert est['is_estimated'] is True
    conf = EarningsCalendarAPIService._normalize_item({
        'securities_code': '7203', 'announcementDate': '2026-07-08',
    })
    assert conf['is_estimated'] is False


def test_holiday_on_weekday_is_moved_to_business_day():
    """平日でも祝日なら営業日へ寄せる（元日=2026-01-01 木曜）。"""
    import jpholiday
    ganjitsu = date(2026, 1, 1)
    assert ganjitsu.weekday() < 5 and jpholiday.is_holiday(ganjitsu)  # 平日の祝日
    result = EarningsCalendarAPIService._to_business_day(ganjitsu)
    assert result.weekday() < 5 and not jpholiday.is_holiday(result)


def test_monday_holiday_moves_to_next_business_day():
    """月曜の祝日（海の日 2026-07-20）は翌営業日(火)へ。"""
    import jpholiday
    umi = date(2026, 7, 20)
    assert umi.weekday() == 0 and jpholiday.is_holiday(umi)
    assert EarningsCalendarAPIService._to_business_day(umi) == date(2026, 7, 21)


def test_result_is_always_a_business_day_around_new_year():
    """年末年始の連休帯でも、補正結果は必ず営業日になる。"""
    import jpholiday
    for day in range(1, 6):  # 2026-01-01〜05（元日+土日）
        d = date(2026, 1, day)
        r = EarningsCalendarAPIService._to_business_day(d)
        assert r.weekday() < 5 and not jpholiday.is_holiday(r)
