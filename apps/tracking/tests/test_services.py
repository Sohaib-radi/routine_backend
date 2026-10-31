import threading
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import connection

from apps.tracking import selectors, services
from apps.tracking.models import CheckIn, Pause, Schedule, StreakCache, Trackable

pytestmark = pytest.mark.django_db

D = date(2026, 1, 1)  # a Thursday


def day(n: int) -> date:
    return D + timedelta(days=n)


@pytest.fixture
def habit(user):
    """A daily boolean habit that started on D."""
    return services.create_trackable(user, name='Pray Fajr', start_date=D)


@pytest.fixture
def water(user):
    return services.create_trackable(
        user, name='Water', kind=Trackable.Kind.QUANTITY, target_value=8, unit='glasses', start_date=D
    )


# ----------------------------- user_today ---------------------------------- #
class TestUserToday:
    def test_cairo_after_midnight_is_the_next_day(self, make_user, freeze_utc):
        # Monday 2026-01-12 23:30 UTC = Tuesday 01:30 in Cairo (UTC+2 in winter).
        freeze_utc(2026, 1, 12, 23, 30)
        cairo = make_user(timezone='Africa/Cairo')
        utc = make_user(timezone='UTC')
        assert services.user_today(cairo) == date(2026, 1, 13)
        assert services.user_today(utc) == date(2026, 1, 12)

    def test_cairo_summer_time(self, make_user, freeze_utc):
        # Monday 2026-07-06 21:30 UTC = Tuesday 00:30 in Cairo (UTC+3, summer time).
        freeze_utc(2026, 7, 6, 21, 30)
        assert services.user_today(make_user(timezone='Africa/Cairo')) == date(2026, 7, 7)

    def test_west_of_utc_is_still_the_previous_day(self, make_user, freeze_utc):
        # Tuesday 2026-01-13 05:00 UTC = Monday 21:00 in Los Angeles.
        freeze_utc(2026, 1, 13, 5, 0)
        assert services.user_today(make_user(timezone='America/Los_Angeles')) == date(2026, 1, 12)

    def test_trackable_start_date_defaults_to_local_today(self, make_user, freeze_utc):
        freeze_utc(2026, 1, 12, 23, 30)
        cairo = make_user(timezone='Africa/Cairo')
        assert services.create_trackable(cairo, name='Read').start_date == date(2026, 1, 13)

    def test_checkin_at_0130_cairo_belongs_to_tuesday(self, make_user, freeze_utc):
        freeze_utc(2026, 1, 12, 23, 30)
        cairo = make_user(timezone='Africa/Cairo')
        t = services.create_trackable(cairo, name='Read', start_date=date(2026, 1, 1))
        checkin = services.log_checkin(cairo, t, services.user_today(cairo))
        assert checkin.date == date(2026, 1, 13)
        # Tuesday is "today" for this user, so it isn't a future date.
        with pytest.raises(ValidationError):
            services.log_checkin(cairo, t, date(2026, 1, 14))


# ----------------------------- create_trackable ---------------------------- #
class TestCreateTrackable:
    def test_creates_daily_schedule_by_default(self, user):
        t = services.create_trackable(user, name='Walk', start_date=D)
        assert Schedule.objects.get(trackable=t).frequency == Schedule.Frequency.DAILY

    def test_custom_schedule(self, user):
        t = services.create_trackable(
            user, name='Gym', start_date=D,
            schedule={'frequency': Schedule.Frequency.WEEKDAYS, 'weekdays_mask': 1 | 4 | 16},
        )
        assert t.schedule.weekdays_mask == 21

    @pytest.mark.parametrize('schedule', [
        {'frequency': 'every_n_days'},
        {'frequency': 'times_week'},
        {'frequency': 'rrule', 'rrule': 'not a rule'},
        {'frequency': 'weekdays', 'weekdays_mask': 0},
        {'frequency': 'weekdays', 'weekdays_mask': 128},
    ])
    def test_invalid_schedule_creates_nothing(self, user, schedule):
        with pytest.raises(ValidationError):
            services.create_trackable(user, name='Bad', start_date=D, schedule=schedule)
        assert not Trackable.objects.exists()

    def test_rejects_unknown_fields(self, user):
        with pytest.raises(ValidationError):
            services.create_trackable(user, name='Bad', user_id=1)


# ----------------------------- log_checkin --------------------------------- #
class TestLogCheckin:
    def test_quantity_accumulates_into_one_row(self, user, water):
        first = services.log_checkin(user, water, day(3), value=2, source=CheckIn.Source.VOICE)
        assert (first.value, first.status) == (Decimal('2.00'), CheckIn.Status.PARTIAL)

        second = services.log_checkin(user, water, day(3), value=1)
        assert second.pk == first.pk
        assert second.value == Decimal('3.00')
        assert CheckIn.objects.filter(trackable=water, date=day(3)).count() == 1

        done = services.log_checkin(user, water, day(3), value=Decimal('5'))
        assert (done.value, done.status) == (Decimal('8.00'), CheckIn.Status.DONE)
        assert CheckIn.objects.count() == 1

    def test_keeps_the_first_source(self, user, water):
        services.log_checkin(user, water, day(3), value=2, source=CheckIn.Source.VOICE)
        checkin = services.log_checkin(user, water, day(3), value=1, source=CheckIn.Source.MANUAL)
        assert checkin.source == CheckIn.Source.VOICE

    def test_other_days_get_their_own_rows(self, user, water):
        services.log_checkin(user, water, day(3), value=2)
        services.log_checkin(user, water, day(4), value=2)
        assert CheckIn.objects.count() == 2

    def test_boolean_logged_twice_stays_one_done_row(self, user, habit):
        services.log_checkin(user, habit, day(3))
        checkin = services.log_checkin(user, habit, day(3), value=5)
        assert CheckIn.objects.count() == 1
        assert (checkin.status, checkin.value) == (CheckIn.Status.DONE, None)

    def test_notes_are_appended(self, user, water):
        services.log_checkin(user, water, day(3), value=1, note='morning')
        checkin = services.log_checkin(user, water, day(3), value=1, note='lunch')
        assert checkin.note == 'morning\nlunch'

    def test_quantity_requires_a_positive_value(self, user, water):
        for value in (None, 0, -1, 'abc', 'NaN'):
            with pytest.raises(ValidationError):
                services.log_checkin(user, water, day(3), value=value)
        assert not CheckIn.objects.exists()

    def test_refuses_another_users_trackable(self, make_user, habit):
        intruder = make_user()
        with pytest.raises(ValidationError):
            services.log_checkin(intruder, habit, day(3))
        assert not CheckIn.objects.exists()

    def test_refuses_future_and_out_of_range_dates(self, user, habit):
        with pytest.raises(ValidationError):
            services.log_checkin(user, habit, services.user_today(user) + timedelta(days=1))
        with pytest.raises(ValidationError):
            services.log_checkin(user, habit, D - timedelta(days=1))

    def test_refuses_unknown_source(self, user, habit):
        with pytest.raises(ValidationError):
            services.log_checkin(user, habit, day(3), source='telepathy')

    def test_logging_a_skipped_day_turns_it_into_a_checkin(self, user, habit):
        services.skip_checkin(user, habit, day(3))
        checkin = services.log_checkin(user, habit, day(3))
        assert checkin.status == CheckIn.Status.DONE
        assert CheckIn.objects.count() == 1

    def test_queues_streak_recompute_on_commit(self, user, habit, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            services.log_checkin(user, habit, services.user_today(user))
        assert len(callbacks) == 1
        assert StreakCache.objects.get(trackable=habit).current == 1  # eager Celery in tests


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_concurrent_first_logs_accumulate_into_one_row(user):
    """Two devices log water at the same moment: one row, both values counted."""
    water = services.create_trackable(user, name='Water', kind=Trackable.Kind.QUANTITY, start_date=D)
    barrier = threading.Barrier(2)
    errors = []

    def log(value):
        try:
            barrier.wait()
            services.log_checkin(user, water, day(3), value=value)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=log, args=(v,)) for v in (2, 3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    checkin = CheckIn.objects.get(trackable=water, date=day(3))
    assert checkin.value == Decimal('5.00')


# ----------------------------- skip_checkin -------------------------------- #
class TestSkip:
    def test_skip_records_a_skipped_row(self, user, habit):
        assert services.skip_checkin(user, habit, day(3)).status == CheckIn.Status.SKIPPED

    def test_cannot_skip_a_day_already_checked_in(self, user, habit):
        services.log_checkin(user, habit, day(3))
        with pytest.raises(ValidationError):
            services.skip_checkin(user, habit, day(3))


# ----------------------------- pauses -------------------------------------- #
class TestPauses:
    def test_pause_never_touches_checkins(self, user, habit):
        services.log_checkin(user, habit, day(3))
        before = list(CheckIn.objects.values())
        services.create_pause(user, start_date=day(1), end_date=day(5))
        services.create_pause(user, start_date=day(2), trackable=habit)
        assert list(CheckIn.objects.values()) == before

    def test_end_before_start_is_refused(self, user):
        with pytest.raises(ValidationError):
            services.create_pause(user, start_date=day(5), end_date=day(4))

    def test_cannot_pause_another_users_trackable(self, make_user, habit):
        with pytest.raises(ValidationError):
            services.create_pause(make_user(), start_date=day(1), trackable=habit)

    def test_paused_dates_cover_global_and_specific_pauses(self, user, habit, water):
        services.create_pause(user, start_date=day(1), end_date=day(2))           # everything
        services.create_pause(user, start_date=day(5), end_date=day(5), trackable=water)
        services.create_pause(user, start_date=day(8))                             # open-ended
        assert selectors.paused_dates(user, habit, D, day(9)) == {day(1), day(2), day(8), day(9)}
        assert selectors.paused_dates(user, water, D, day(9)) == {day(1), day(2), day(5), day(8), day(9)}

    def test_paused_dates_ignore_other_users(self, make_user, habit, user):
        services.create_pause(make_user(), start_date=day(1))
        assert selectors.paused_dates(user, habit, D, day(9)) == set()

    def test_end_pause_defaults_to_yesterday(self, user, freeze_utc):
        freeze_utc(2026, 1, 20, 12, 0)
        pause = services.create_pause(user, start_date=day(1))
        pause = services.end_pause(user, pause)
        assert pause.end_date == date(2026, 1, 19)

    def test_end_pause_that_never_started_deletes_it(self, user, freeze_utc):
        freeze_utc(2026, 1, 20, 12, 0)
        pause = services.create_pause(user, start_date=date(2026, 1, 20))
        assert services.end_pause(user, pause) is None
        assert not Pause.objects.exists()


# ----------------------------- schedule & due_today ------------------------ #
class TestSchedule:
    def test_weekdays_mask(self, user):
        # Mon=1, Wed=4, Fri=16. D (2026-01-01) is a Thursday.
        t = services.create_trackable(user, name='Gym', start_date=D,
                                      schedule={'frequency': 'weekdays', 'weekdays_mask': 1 | 4 | 16})
        assert selectors.scheduled_dates(t.schedule, D, day(6)) == {day(1), day(4), day(6)}  # Fri, Mon, Wed

    def test_every_n_days(self, user):
        t = services.create_trackable(user, name='Water plants', start_date=D,
                                      schedule={'frequency': 'every_n_days', 'interval_days': 3})
        assert selectors.scheduled_dates(t.schedule, D, day(7)) == {D, day(3), day(6)}

    def test_rrule(self, user):
        t = services.create_trackable(user, name='Fast', start_date=D,
                                      schedule={'frequency': 'rrule', 'rrule': 'FREQ=WEEKLY;BYDAY=MO,TH'})
        assert selectors.scheduled_dates(t.schedule, D, day(7)) == {D, day(4), day(7)}

    def test_outside_trackable_dates(self, user):
        t = services.create_trackable(user, name='Course', start_date=day(2), end_date=day(4))
        assert selectors.scheduled_dates(t.schedule, D, day(9)) == {day(2), day(3), day(4)}

    def test_due_today(self, user, make_user, freeze_utc):
        freeze_utc(2026, 1, 5, 12, 0)  # Monday
        daily = services.create_trackable(user, name='Daily', start_date=D)
        services.create_trackable(user, name='Fridays', start_date=D,
                                  schedule={'frequency': 'weekdays', 'weekdays_mask': 16})
        paused = services.create_trackable(user, name='Paused', start_date=D)
        services.create_pause(user, start_date=D, trackable=paused)
        services.create_trackable(make_user(), name='Not mine', start_date=D)
        assert selectors.due_today(user) == [daily]

        services.create_pause(user, start_date=date(2026, 1, 5), end_date=date(2026, 1, 5))
        assert selectors.due_today(user) == []


# ----------------------------- streaks ------------------------------------- #
class TestStreak:
    def test_consecutive_days(self, user, habit):
        for n in range(5):
            services.log_checkin(user, habit, day(n))
        stats = selectors.compute_streak(habit, today=day(4))
        assert (stats.current, stats.longest) == (5, 5)

    def test_missed_day_breaks_the_streak(self, user, habit):
        for n in (0, 1, 2, 4, 5):
            services.log_checkin(user, habit, day(n))
        stats = selectors.compute_streak(habit, today=day(5))
        assert (stats.current, stats.longest) == (2, 3)

    def test_today_not_done_yet_does_not_break(self, user, habit):
        for n in range(3):
            services.log_checkin(user, habit, day(n))
        assert selectors.compute_streak(habit, today=day(3)).current == 3

    def test_partial_day_breaks(self, user, water):
        services.log_checkin(user, water, day(0), value=8)
        services.log_checkin(user, water, day(1), value=3)  # partial
        services.log_checkin(user, water, day(2), value=8)
        assert selectors.compute_streak(water, today=day(2)).current == 1

    def test_paused_days_are_skipped(self, user, habit):
        # done 0-1, paused 2-4 (no check-ins), done 5-6: streak continues through the pause.
        for n in (0, 1, 5, 6):
            services.log_checkin(user, habit, day(n))
        services.create_pause(user, start_date=day(2), end_date=day(4), reason=Pause.Reason.VACATION)
        stats = selectors.compute_streak(habit, today=day(6))
        assert (stats.current, stats.longest) == (4, 4)  # paused days don't extend it either

    def test_trackable_specific_and_open_ended_pauses(self, user, habit, water):
        for n in (0, 1):
            services.log_checkin(user, habit, day(n))
        services.create_pause(user, start_date=day(2), trackable=habit)  # open-ended
        assert selectors.compute_streak(habit, today=day(9)).current == 2
        # another trackable's pause doesn't protect this one
        services.log_checkin(user, water, day(0), value=8)
        assert selectors.compute_streak(water, today=day(9)).current == 0

    def test_checkin_during_pause_is_kept_but_neutral(self, user, habit):
        services.log_checkin(user, habit, day(0))
        services.log_checkin(user, habit, day(1))
        services.create_pause(user, start_date=day(1), end_date=day(1))
        assert CheckIn.objects.filter(date=day(1)).exists()  # history untouched
        assert selectors.compute_streak(habit, today=day(1)).current == 1

    def test_non_scheduled_days_are_skipped(self, user):
        # Mon/Wed/Fri. D is Thursday: Fri day(1), Mon day(4), Wed day(6), Fri day(8).
        t = services.create_trackable(user, name='Gym', start_date=D,
                                      schedule={'frequency': 'weekdays', 'weekdays_mask': 1 | 4 | 16})
        for n in (1, 4, 6, 8):
            services.log_checkin(user, t, day(n))
        stats = selectors.compute_streak(t, today=day(9))
        assert (stats.current, stats.longest) == (4, 4)

    def test_missed_scheduled_day_breaks_but_unscheduled_does_not(self, user):
        t = services.create_trackable(user, name='Gym', start_date=D,
                                      schedule={'frequency': 'weekdays', 'weekdays_mask': 1 | 4 | 16})
        for n in (1, 6, 8):  # missed Monday day(4)
            services.log_checkin(user, t, day(n))
        assert selectors.compute_streak(t, today=day(9)).current == 2

    def test_skipped_days_neither_break_nor_extend(self, user, habit):
        services.log_checkin(user, habit, day(0))
        services.skip_checkin(user, habit, day(1))
        services.log_checkin(user, habit, day(2))
        stats = selectors.compute_streak(habit, today=day(2))
        assert (stats.current, stats.longest) == (2, 2)

    def test_longest_is_rebuilt_from_history(self, user, habit):
        """Rule 5: deleting a check-in lowers `longest` on the next rebuild."""
        for n in range(4):
            services.log_checkin(user, habit, day(n))
        assert selectors.compute_streak(habit, today=day(3)).longest == 4
        CheckIn.objects.filter(date=day(1)).delete()
        assert selectors.compute_streak(habit, today=day(3)).longest == 2

    def test_completion_rate_30d(self, user, habit):
        today = day(40)
        # Window: day(11)..day(40). Done on even days 12..40 (today included).
        for n in range(12, 41, 2):
            services.log_checkin(user, habit, day(n))
        services.create_pause(user, start_date=day(12), end_date=day(21))  # 10 days are neutral
        # Counted: day(11) + day(22)..day(40) = 20 days, of which 10 done.
        assert selectors.compute_streak(habit, today=today).completion_rate_30d == Decimal('50.00')

    def test_per_period_frequencies_report_zero_for_now(self, user):
        t = services.create_trackable(user, name='Run', start_date=D,
                                      schedule={'frequency': 'times_week', 'times_per_period': 3})
        services.log_checkin(user, t, day(0))
        assert selectors.compute_streak(t, today=day(0)).current == 0


class TestRecomputeTask:
    def test_rebuilds_the_cache(self, user, habit, freeze_utc):
        from apps.tracking.tasks import recompute_streak

        freeze_utc(2026, 1, 3, 12, 0)  # day(2)
        for n in range(3):
            services.log_checkin(user, habit, day(n))
        recompute_streak.delay(str(habit.pk))
        cache = StreakCache.objects.get(trackable=habit)
        assert (cache.current, cache.longest, cache.computed_for_date) == (3, 3, day(2))

        # Overwrites a stale (wrong) cache rather than taking max() of it.
        StreakCache.objects.filter(pk=cache.pk).update(longest=99)
        recompute_streak(str(habit.pk))
        cache.refresh_from_db()
        assert cache.longest == 3

    def test_uses_the_users_local_today(self, make_user, freeze_utc):
        from apps.tracking.tasks import recompute_streak

        freeze_utc(2026, 1, 12, 23, 30)  # Tuesday 01:30 in Cairo
        cairo = make_user(timezone='Africa/Cairo')
        t = services.create_trackable(cairo, name='Read', start_date=date(2026, 1, 12))
        services.log_checkin(cairo, t, date(2026, 1, 12))
        recompute_streak(str(t.pk))
        cache = StreakCache.objects.get(trackable=t)
        assert cache.computed_for_date == date(2026, 1, 13)
        assert cache.current == 1  # Tuesday not done yet doesn't break Monday's streak

    def test_deleted_trackable_is_ignored(self, user, habit):
        from apps.tracking.tasks import recompute_streak

        pk = str(habit.pk)
        habit.delete()
        recompute_streak(pk)  # no error
        assert not StreakCache.objects.exists()

    def test_pause_queues_recompute_for_all_trackables(self, user, habit, water,
                                                       django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks() as callbacks:
            services.create_pause(user, start_date=day(1))
        assert len(callbacks) == 2
