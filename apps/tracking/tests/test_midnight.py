"""Hourly beat task: recompute streaks for users whose local midnight just passed."""

from datetime import date, datetime, timedelta, timezone as dt_timezone

import pytest

from apps.tracking import selectors, services, tasks
from apps.tracking.models import StreakCache

from .test_services import D, log

pytestmark = pytest.mark.django_db

UTC = dt_timezone.utc
ZONES = ['UTC', 'Africa/Cairo', 'America/Los_Angeles', 'Asia/Kolkata', 'Asia/Kathmandu',
         'Pacific/Chatham', 'Pacific/Kiritimati']


def utc(*args):
    return datetime(*args, tzinfo=UTC)


@pytest.fixture
def users(make_user):
    return {tz: make_user(timezone=tz) for tz in ZONES}


class TestTimezonesWithNewDay:
    def test_cairo_midnight(self, users):
        # 22:00 UTC = 00:00 in Cairo (UTC+2 in January)
        assert selectors.timezones_with_new_day(utc(2026, 1, 12, 21), utc(2026, 1, 12, 22)) == ['Africa/Cairo']

    def test_half_hour_zone_is_caught_at_the_next_full_hour(self, users):
        # Kolkata midnight is 18:30 UTC; the 18:00 -> 19:00 window contains it.
        assert 'Asia/Kolkata' in selectors.timezones_with_new_day(utc(2026, 1, 12, 18), utc(2026, 1, 12, 19))

    def test_dst_day_without_a_local_midnight_hour(self, users):
        # 2026-04-24 Cairo jumps 23:00 -> 01:00: there is no 00:xx, the date still changes once.
        hit = [h for h in range(24)
               if 'Africa/Cairo' in selectors.timezones_with_new_day(utc(2026, 4, 23, h), utc(2026, 4, 23, h) +
                                                                     timedelta(hours=1))]
        assert hit == [21]  # window 21:00 -> 22:00 UTC = 23:00 -> 01:00 local

    @pytest.mark.parametrize('day', [date(2026, 1, 12), date(2026, 3, 8), date(2026, 4, 23), date(2026, 11, 1)])
    def test_every_zone_exactly_once_per_day(self, users, day):
        """24 hourly windows covering one UTC day: each zone's midnight is caught exactly once
        (days chosen around DST changes in the US and Egypt)."""
        counts = dict.fromkeys(ZONES, 0)
        start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        for h in range(24):
            for tz in selectors.timezones_with_new_day(start + timedelta(hours=h), start + timedelta(hours=h + 1)):
                counts[tz] += 1
        assert counts == dict.fromkeys(ZONES, 1)

    def test_only_zones_that_have_users(self, make_user):
        make_user(timezone='UTC')
        assert selectors.timezones_with_new_day(utc(2026, 1, 12, 21), utc(2026, 1, 12, 22)) == []


class TestTrackableIdsForNewDay:
    def test_selects_active_trackables_of_matching_users(self, make_user):
        cairo, la = make_user(timezone='Africa/Cairo'), make_user(timezone='America/Los_Angeles')
        mine = services.create_trackable(cairo, name='Read', start_date=D)
        archived = services.create_trackable(cairo, name='Old', start_date=D)
        services.archive_trackable(cairo, archived)
        services.create_trackable(la, name='Other zone', start_date=D)
        inactive = make_user(timezone='Africa/Cairo', is_active=False)
        services.create_trackable(inactive, name='Inactive user', start_date=D)

        assert selectors.trackable_ids_for_new_day(utc(2026, 1, 12, 21), utc(2026, 1, 12, 22)) == [mine.pk]


class TestHourlyTask:
    def test_resets_a_streak_broken_by_a_missed_day(self, make_user, freeze_utc):
        """No check-in, no pause change: only the midnight task can notice the missed day."""
        cairo = make_user(timezone='Africa/Cairo')
        t = services.create_trackable(cairo, name='Read', start_date=date(2026, 1, 1))
        for d in (date(2026, 1, 9), date(2026, 1, 10)):
            log(cairo, t, d)
        freeze_utc(2026, 1, 10, 12, 0)
        services.refresh_streak_cache(t.pk)
        assert StreakCache.objects.get(trackable=t).current == 2

        # Cairo skips Jan 11 entirely. Midnight Jan 12 local = 22:00 UTC on Jan 11.
        freeze_utc(2026, 1, 11, 22, 1)
        queued = tasks.recompute_streaks_after_local_midnight()

        cache = StreakCache.objects.get(trackable=t)
        assert queued == 1
        assert (cache.current, cache.longest, cache.computed_for_date) == (0, 2, date(2026, 1, 12))

    def test_late_run_uses_the_full_hour_window(self, make_user):
        cairo = make_user(timezone='Africa/Cairo')
        services.create_trackable(cairo, name='Read', start_date=D)
        # Beat fires 40 minutes late: the window is still 21:00 -> 22:00 UTC.
        assert tasks.recompute_streaks_after_local_midnight(now='2026-01-12T22:40:00+00:00') == 1
        assert tasks.recompute_streaks_after_local_midnight(now='2026-01-12T23:01:00+00:00') == 0

    def test_fans_out_in_batches(self, make_user, monkeypatch):
        cairo = make_user(timezone='Africa/Cairo')
        for i in range(5):
            services.create_trackable(cairo, name=f'T{i}', start_date=D)
        monkeypatch.setattr(tasks, 'BATCH_SIZE', 2)
        batches = []
        monkeypatch.setattr(tasks.recompute_streaks, 'delay', lambda ids: batches.append(ids))

        assert tasks.recompute_streaks_after_local_midnight(now='2026-01-12T22:01:00+00:00') == 5
        assert [len(b) for b in batches] == [2, 2, 1]

    def test_batch_skips_deleted_trackables(self, user):
        t = services.create_trackable(user, name='Gone', start_date=D)
        pk = str(t.pk)
        t.delete()
        tasks.recompute_streaks([pk])  # no error
        assert not StreakCache.objects.exists()

    def test_registered_in_beat_schedule(self, settings):
        entry = settings.CELERY_BEAT_SCHEDULE['recompute-streaks-after-local-midnight']
        assert entry['task'] == 'apps.tracking.tasks.recompute_streaks_after_local_midnight'
        assert entry['schedule'].minute == {1}
