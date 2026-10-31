from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.tracking import selectors, services
from apps.tracking.models import Trackable

from .test_services import D, day, log, pause

pytestmark = pytest.mark.django_db

TODAY = date(2026, 1, 12)  # Monday


@pytest.fixture
def today(freeze_utc):
    freeze_utc(TODAY.year, TODAY.month, TODAY.day, 12, 0)
    return TODAY


# ----------------------------- today_screen -------------------------------- #
class TestTodayScreen:
    def test_items_with_checkin_and_streak(self, user, today):
        water = services.create_trackable(user, name='Water', kind=Trackable.Kind.QUANTITY, target_value=8,
                                          start_date=D)
        habit = services.create_trackable(user, name='Pray', start_date=D)
        checkin = services.log_checkin(user, water, today, value=3)
        services.refresh_streak_cache(water.pk)

        screen = selectors.today_screen(user)

        assert screen.date == today
        assert [item.trackable for item in screen.items] == [water, habit]
        assert screen.items[0].checkin == checkin
        assert screen.items[0].streak.computed_for_date == today
        assert screen.items[1].checkin is None and screen.items[1].streak is None

    def test_excludes_unscheduled_paused_archived_and_others(self, user, make_user, today):
        due = services.create_trackable(user, name='Daily', start_date=D)
        services.create_trackable(user, name='Fridays', start_date=D,
                                  schedule={'frequency': 'weekdays', 'weekdays_mask': 16})
        paused = services.create_trackable(user, name='Paused', start_date=D)
        services.create_pause(user, start_date=today, trackable=paused)
        archived = services.create_trackable(user, name='Archived', start_date=D)
        services.archive_trackable(user, archived)
        services.create_trackable(make_user(), name='Not mine', start_date=D)

        assert [i.trackable for i in selectors.today_screen(user).items] == [due]

    def test_global_pause_empties_the_screen(self, user, today):
        services.create_trackable(user, name='Daily', start_date=D)
        services.create_pause(user, start_date=today)
        assert selectors.today_screen(user).items == []

    def test_only_todays_checkin(self, user, today):
        habit = services.create_trackable(user, name='Pray', start_date=D)
        services.log_checkin(user, habit, today - timedelta(days=1))
        assert selectors.today_screen(user).items[0].checkin is None

    def test_constant_number_of_queries(self, user, make_user, today):
        """No N+1: 1 trackable or 20, the same three queries."""

        def build(owner, n):
            for i in range(n):
                t = services.create_trackable(
                    owner, name=f'T{i}', kind=Trackable.Kind.QUANTITY, target_value=5, start_date=D,
                    schedule={'frequency': 'rrule', 'rrule': 'FREQ=DAILY'} if i % 3 == 0 else None,
                )
                services.log_checkin(owner, t, today, value=1)
                services.refresh_streak_cache(t.pk)
                if i % 5 == 4:
                    services.create_pause(owner, start_date=today + timedelta(days=3), trackable=t)  # planned

        small, large = make_user(), make_user()
        build(small, 1)
        build(large, 20)

        counts = []
        for owner, expected_items in ((small, 1), (large, 20)):
            owner.refresh_from_db()
            with CaptureQueriesContext(connection) as ctx:
                screen = selectors.today_screen(owner)
                for item in screen.items:  # touch everything a serializer would
                    _ = (item.trackable.schedule.frequency, item.checkin and item.checkin.value,
                         item.streak and item.streak.current)
            assert len(screen.items) == expected_items
            counts.append(len(ctx.captured_queries))
        assert counts[0] == counts[1] == 3


# ----------------------------- history & pauses ---------------------------- #
class TestCheckinHistory:
    def test_range_newest_first_and_scoped(self, user, make_user):
        habit = services.create_trackable(user, name='Pray', start_date=D)
        for n in range(6):
            log(user, habit, day(n))
        rows = selectors.checkin_history(user, habit, day(1), day(4))
        assert [c.date for c in rows] == [day(4), day(3), day(2), day(1)]
        assert not selectors.checkin_history(make_user(), habit, day(0), day(5)).exists()

    def test_defaults_to_last_30_days(self, user, today):
        habit = services.create_trackable(user, name='Pray', start_date=D)
        for d in (today, today - timedelta(days=1)):
            services.log_checkin(user, habit, d)
        assert selectors.checkin_history(user, habit).count() == 2

    def test_range_limits(self, user):
        habit = services.create_trackable(user, name='Pray', start_date=D)
        with pytest.raises(ValidationError):
            selectors.checkin_history(user, habit, day(5), day(1))
        with pytest.raises(ValidationError):
            selectors.checkin_history(user, habit, D, D + timedelta(days=366))
        selectors.checkin_history(user, habit, D, D + timedelta(days=365))  # 366 days: fine


class TestPausesFor:
    def test_active_and_past(self, user, make_user, today):
        over = pause(user, start_date=day(1), end_date=day(2))
        running = services.create_pause(user, start_date=today)
        planned = services.create_pause(user, start_date=today + timedelta(days=10))
        pause(make_user(), start_date=today)

        assert set(selectors.pauses_for(user)) == {over, running, planned}
        assert set(selectors.pauses_for(user, active=True)) == {running, planned}
        assert set(selectors.pauses_for(user, active=False)) == {over}
