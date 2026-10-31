"""Services added for the tracking API: trackable edits, archive, check-in fixes, pause rules."""

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.tracking import services
from apps.tracking.models import CheckIn, Pause, StreakCache, Trackable

from .test_services import D, as_of, day, log, pause, skip

pytestmark = pytest.mark.django_db

TODAY = date(2026, 1, 10)  # frozen "today" for a UTC user
YESTERDAY = date(2026, 1, 9)


@pytest.fixture
def today(freeze_utc):
    freeze_utc(TODAY.year, TODAY.month, TODAY.day, 12, 0)
    return TODAY


@pytest.fixture
def habit(user):
    return services.create_trackable(user, name='Pray', start_date=D)


@pytest.fixture
def water(user):
    return services.create_trackable(user, name='Water', kind=Trackable.Kind.QUANTITY, target_value=8,
                                     start_date=D)


# ----------------------------- update_trackable ---------------------------- #
class TestUpdateTrackable:
    def test_updates_fields_and_schedule(self, user, habit):
        t = services.update_trackable(user, habit, name='Pray Fajr', color='#112233',
                                      schedule={'frequency': 'weekdays', 'weekdays_mask': 31})
        t.refresh_from_db()
        assert (t.name, t.color, t.schedule.frequency, t.schedule.weekdays_mask) == (
            'Pray Fajr', '#112233', 'weekdays', 31)

    def test_kind_cannot_change(self, user, habit):
        with pytest.raises(ValidationError):
            services.update_trackable(user, habit, kind=Trackable.Kind.QUANTITY)
        services.update_trackable(user, habit, kind=Trackable.Kind.BOOLEAN)  # unchanged: fine

    def test_start_date_cannot_move_past_checkins(self, user, habit):
        log(user, habit, day(3))
        with pytest.raises(ValidationError):
            services.update_trackable(user, habit, start_date=day(4))
        services.update_trackable(user, habit, start_date=day(3))

    def test_invalid_schedule_is_rolled_back(self, user, habit):
        with pytest.raises(ValidationError):
            services.update_trackable(user, habit, name='Renamed', schedule={'frequency': 'every_n_days'})
        habit.refresh_from_db()
        assert habit.name == 'Pray' and habit.schedule.frequency == 'daily'

    def test_owner_only(self, make_user, habit):
        with pytest.raises(ValidationError):
            services.update_trackable(make_user(), habit, name='Mine')

    def test_rejects_unknown_fields(self, user, habit):
        with pytest.raises(ValidationError):
            services.update_trackable(user, habit, user_id=1, is_archived=True)

    def test_schedule_change_queues_recompute(self, user, habit, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks() as callbacks:
            services.update_trackable(user, habit, name='Renamed')
        assert callbacks == []
        with django_capture_on_commit_callbacks() as callbacks:
            services.update_trackable(user, habit, schedule={'frequency': 'weekdays', 'weekdays_mask': 1})
        assert len(callbacks) == 1


# ----------------------------- archive ------------------------------------- #
class TestArchive:
    def test_archive_and_unarchive(self, user, habit):
        assert services.archive_trackable(user, habit).is_archived
        assert not services.unarchive_trackable(user, habit).is_archived

    def test_archived_trackable_refuses_checkins(self, user, habit, today):
        services.archive_trackable(user, habit)
        with pytest.raises(ValidationError, match='archived'):
            services.log_checkin(user, habit, today)
        with pytest.raises(ValidationError, match='archived'):
            services.skip_checkin(user, habit, today)

    def test_archive_keeps_history(self, user, habit):
        log(user, habit, day(3))
        services.archive_trackable(user, habit)
        assert CheckIn.objects.filter(trackable=habit).count() == 1

    def test_owner_only(self, make_user, habit):
        with pytest.raises(ValidationError):
            services.archive_trackable(make_user(), habit)


# ----------------------------- set_checkin_value --------------------------- #
class TestSetCheckinValue:
    def test_sets_instead_of_accumulating(self, user, water, today):
        checkin = services.log_checkin(user, water, today, value=5)
        checkin = services.set_checkin_value(user, checkin, 2)
        assert (checkin.value, checkin.status) == (Decimal('2.00'), CheckIn.Status.PARTIAL)
        checkin = services.set_checkin_value(user, checkin, '8')
        assert (checkin.value, checkin.status) == (Decimal('8.00'), CheckIn.Status.DONE)
        assert CheckIn.objects.count() == 1

    def test_yesterday_is_allowed(self, user, water, today):
        checkin = services.log_checkin(user, water, YESTERDAY, value=5)
        assert services.set_checkin_value(user, checkin, 3).value == Decimal('3.00')

    def test_older_check_ins_are_frozen(self, user, water, today):
        checkin = log(user, water, date(2026, 1, 8), value=5)
        with as_of(TODAY):
            with pytest.raises(ValidationError, match='today or yesterday'):
                services.set_checkin_value(user, checkin, 1)
        checkin.refresh_from_db()
        assert checkin.value == Decimal('5.00')

    def test_owner_only(self, make_user, user, water, today):
        checkin = services.log_checkin(user, water, today, value=5)
        with pytest.raises(ValidationError):
            services.set_checkin_value(make_user(), checkin, 1)

    @pytest.mark.parametrize('value', [0, -1, 'abc', None])
    def test_value_must_be_positive(self, user, water, today, value):
        checkin = services.log_checkin(user, water, today, value=5)
        with pytest.raises(ValidationError):
            services.set_checkin_value(user, checkin, value)

    def test_boolean_has_no_value(self, user, habit, today):
        checkin = services.log_checkin(user, habit, today)
        with pytest.raises(ValidationError, match='delete'):
            services.set_checkin_value(user, checkin, 1)

    def test_turns_a_skip_into_a_checkin(self, user, water, today):
        checkin = services.skip_checkin(user, water, today)
        assert services.set_checkin_value(user, checkin, 8).status == CheckIn.Status.DONE

    def test_queues_recompute(self, user, water, today, django_capture_on_commit_callbacks):
        checkin = services.log_checkin(user, water, today, value=8)
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            services.set_checkin_value(user, checkin, 1)
        assert len(callbacks) == 1
        assert StreakCache.objects.get(trackable=water).current == 0  # 1 of 8: partial


# ----------------------------- delete_checkin ------------------------------ #
class TestDeleteCheckin:
    def test_deletes(self, user, habit, today):
        checkin = services.log_checkin(user, habit, today)
        services.delete_checkin(user, checkin)
        assert not CheckIn.objects.exists()

    def test_deletes_a_skip(self, user, habit, today):
        services.delete_checkin(user, services.skip_checkin(user, habit, YESTERDAY))
        assert not CheckIn.objects.exists()

    def test_older_check_ins_are_frozen(self, user, habit, today):
        checkin = log(user, habit, date(2026, 1, 8))
        with as_of(TODAY):
            with pytest.raises(ValidationError):
                services.delete_checkin(user, checkin)
        assert CheckIn.objects.exists()

    def test_owner_only(self, make_user, user, habit, today):
        checkin = services.log_checkin(user, habit, today)
        with pytest.raises(ValidationError):
            services.delete_checkin(make_user(), checkin)
        assert CheckIn.objects.exists()

    def test_queues_recompute(self, user, habit, today, django_capture_on_commit_callbacks):
        services.log_checkin(user, habit, YESTERDAY)
        checkin = services.log_checkin(user, habit, today)
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            services.delete_checkin(user, checkin)
        assert len(callbacks) == 1
        assert StreakCache.objects.get(trackable=habit).current == 1  # today not done: neutral


# ----------------------------- pause rules --------------------------------- #
class TestPauseRules:
    def test_start_yesterday_or_later(self, user, today):
        services.create_pause(user, start_date=YESTERDAY)
        services.create_pause(user, start_date=today)
        with pytest.raises(ValidationError, match='yesterday'):
            services.create_pause(user, start_date=date(2026, 1, 8))

    def test_planned_future_pause_is_allowed(self, user, today):
        p = services.create_pause(user, start_date=date(2026, 2, 1), end_date=date(2026, 2, 14),
                                  reason=Pause.Reason.VACATION)
        assert p.pk

    def test_start_window_uses_local_date(self, make_user, freeze_utc):
        freeze_utc(2026, 1, 12, 23, 30)  # Tuesday 01:30 in Cairo
        cairo = make_user(timezone='Africa/Cairo')
        services.create_pause(cairo, start_date=date(2026, 1, 12))  # Cairo's yesterday
        with pytest.raises(ValidationError):
            services.create_pause(cairo, start_date=date(2026, 1, 11))

    def test_cancel_a_planned_pause(self, user, today):
        p = services.create_pause(user, start_date=date(2026, 2, 1))
        assert services.end_pause(user, p) is None
        assert not Pause.objects.exists()

    def test_shorten_a_planned_pause(self, user, today):
        p = services.create_pause(user, start_date=date(2026, 2, 1), end_date=date(2026, 2, 14))
        assert services.end_pause(user, p, last_paused_day=date(2026, 2, 7)).end_date == date(2026, 2, 7)

    def test_cannot_end_before_yesterday(self, user):
        p = pause(user, start_date=day(1))
        with as_of(TODAY):
            with pytest.raises(ValidationError, match='yesterday'):
                services.end_pause(user, p, last_paused_day=date(2026, 1, 5))

    def test_cannot_end_a_pause_that_is_over(self, user):
        p = pause(user, start_date=day(1), end_date=day(3))
        with as_of(TODAY):
            with pytest.raises(ValidationError, match='over'):
                services.end_pause(user, p)

    def test_owner_only(self, make_user, user, today):
        p = services.create_pause(user, start_date=today)
        with pytest.raises(ValidationError):
            services.end_pause(make_user(), p)


def test_skip_helper_still_works(user, habit):
    assert skip(user, habit, day(2)).status == CheckIn.Status.SKIPPED
