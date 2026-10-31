from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.tracking import services
from apps.tracking.models import CheckIn, Pause, Trackable

from .test_services import D, log, pause

pytestmark = pytest.mark.django_db

TODAY = date(2026, 1, 12)  # Monday
YESTERDAY = TODAY - timedelta(days=1)
BASE = '/api/v1/tracking'


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def today(freeze_utc):
    freeze_utc(TODAY.year, TODAY.month, TODAY.day, 12, 0)
    return TODAY


@pytest.fixture
def api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def other(make_user):
    return make_user()


@pytest.fixture
def water(user):
    return services.create_trackable(user, name='Water', kind=Trackable.Kind.QUANTITY, target_value=8,
                                     unit='glasses', start_date=D)


@pytest.fixture
def habit(user):
    return services.create_trackable(user, name='Pray', start_date=D)


@pytest.fixture
def foreign(other):
    return services.create_trackable(other, name='Not mine', start_date=D)


# ----------------------------- auth ---------------------------------------- #
@pytest.mark.parametrize('method, path', [
    ('get', '/trackables/'), ('post', '/trackables/'), ('get', '/today/'),
    ('post', '/checkins/'), ('post', '/checkins/skip/'), ('get', '/pauses/'), ('post', '/pauses/'),
])
def test_authentication_required(method, path):
    assert getattr(APIClient(), method)(BASE + path).status_code == 401


# ----------------------------- trackables ---------------------------------- #
class TestTrackables:
    def test_create_with_schedule(self, api, user, today):
        res = api.post(f'{BASE}/trackables/', {
            'name': 'Gym', 'kind': 'duration', 'target_value': '45', 'unit': 'min',
            'schedule': {'frequency': 'weekdays', 'weekdays_mask': 21},
        }, format='json')
        assert res.status_code == 201, res.data
        assert res.data['schedule']['weekdays_mask'] == 21
        assert res.data['start_date'] == str(today)  # defaults to the user's today
        assert res.data['streak'] is None
        assert Trackable.objects.get(pk=res.data['id']).user == user

    def test_create_defaults_to_daily(self, api):
        res = api.post(f'{BASE}/trackables/', {'name': 'Read'}, format='json')
        assert res.data['schedule']['frequency'] == 'daily'

    def test_create_validates_schedule(self, api):
        res = api.post(f'{BASE}/trackables/', {'name': 'Bad', 'schedule': {'frequency': 'every_n_days'}},
                       format='json')
        assert res.status_code == 400
        assert not Trackable.objects.exists()

    def test_client_cannot_set_owner_or_archived(self, api, user, other):
        res = api.post(f'{BASE}/trackables/', {'name': 'Read', 'user': str(other.pk), 'is_archived': True},
                       format='json')
        t = Trackable.objects.get(pk=res.data['id'])
        assert t.user == user and not t.is_archived

    def test_list_is_scoped_and_hides_archived(self, api, user, habit, water, foreign):
        services.archive_trackable(user, water)
        names = [t['name'] for t in api.get(f'{BASE}/trackables/').data['results']]
        assert names == ['Pray']
        archived = [t['name'] for t in api.get(f'{BASE}/trackables/?archived=true').data['results']]
        assert archived == ['Water']

    def test_list_filters_domain(self, api, user, habit):
        services.create_trackable(user, name='Quran', domain='spiritual', start_date=D)
        names = [t['name'] for t in api.get(f'{BASE}/trackables/?domain=spiritual').data['results']]
        assert names == ['Quran']

    def test_other_users_trackable_is_404(self, api, foreign):
        assert api.get(f'{BASE}/trackables/{foreign.pk}/').status_code == 404
        assert api.patch(f'{BASE}/trackables/{foreign.pk}/', {'name': 'x'}, format='json').status_code == 404
        assert api.post(f'{BASE}/trackables/{foreign.pk}/archive/').status_code == 404
        assert api.get(f'{BASE}/trackables/{foreign.pk}/checkins/').status_code == 404

    def test_patch_fields_and_schedule(self, api, habit):
        res = api.patch(f'{BASE}/trackables/{habit.pk}/', {
            'name': 'Pray Fajr', 'schedule': {'frequency': 'weekdays', 'weekdays_mask': 1},
        }, format='json')
        assert res.status_code == 200, res.data
        assert (res.data['name'], res.data['schedule']['weekdays_mask']) == ('Pray Fajr', 1)

    def test_patch_kind_is_refused(self, api, habit):
        res = api.patch(f'{BASE}/trackables/{habit.pk}/', {'kind': 'quantity'}, format='json')
        assert res.status_code == 400 and 'kind' in res.data

    def test_put_and_delete_are_not_allowed(self, api, habit):
        assert api.put(f'{BASE}/trackables/{habit.pk}/', {'name': 'x'}, format='json').status_code == 405
        assert api.delete(f'{BASE}/trackables/{habit.pk}/').status_code == 405

    def test_archive_and_unarchive(self, api, habit):
        assert api.post(f'{BASE}/trackables/{habit.pk}/archive/').data['is_archived'] is True
        assert api.post(f'{BASE}/trackables/{habit.pk}/unarchive/').data['is_archived'] is False

    def test_history(self, api, user, habit, today):
        for n in range(1, 6):
            log(user, habit, today - timedelta(days=n))
        res = api.get(f'{BASE}/trackables/{habit.pk}/checkins/',
                      {'from': str(today - timedelta(days=3)), 'to': str(today - timedelta(days=1))})
        assert res.status_code == 200
        assert [c['date'] for c in res.data] == [str(today - timedelta(days=n)) for n in (1, 2, 3)]

    def test_history_rejects_bad_ranges(self, api, habit):
        assert api.get(f'{BASE}/trackables/{habit.pk}/checkins/', {'from': '2026-01-05', 'to': '2026-01-01'}
                       ).status_code == 400
        assert api.get(f'{BASE}/trackables/{habit.pk}/checkins/', {'from': '2024-01-01', 'to': '2026-01-01'}
                       ).status_code == 400
        assert api.get(f'{BASE}/trackables/{habit.pk}/checkins/', {'from': 'nope'}).status_code == 400


# ----------------------------- today --------------------------------------- #
class TestToday:
    def test_shape(self, api, user, water, habit, today):
        services.log_checkin(user, water, today, value=3)
        services.refresh_streak_cache(water.pk)
        res = api.get(f'{BASE}/today/')
        assert res.status_code == 200
        assert res.data['date'] == str(today)
        first, second = res.data['items']
        assert first['trackable']['name'] == 'Water' and 'streak' not in first['trackable']
        assert first['checkin']['value'] == '3.00' and first['checkin']['status'] == 'partial'
        assert first['streak']['computed_for_date'] == str(today)
        assert second['checkin'] is None and second['streak'] is None

    def test_uses_the_users_local_date(self, make_user, freeze_utc):
        freeze_utc(2026, 1, 12, 23, 30)  # Tuesday 01:30 in Cairo
        cairo = make_user(timezone='Africa/Cairo')
        client = APIClient()
        client.force_authenticate(cairo)
        assert client.get(f'{BASE}/today/').data['date'] == '2026-01-13'

    def test_no_n_plus_one(self, make_user, today):
        """Same number of queries for 1 trackable and for 20."""

        def build(n):
            owner = make_user()
            for i in range(n):
                t = services.create_trackable(owner, name=f'T{i}', kind='quantity', target_value=5, start_date=D,
                                              schedule={'frequency': 'rrule', 'rrule': 'FREQ=DAILY'} if i % 3 else None)
                if i % 2:
                    services.log_checkin(owner, t, today, value=2)
                services.refresh_streak_cache(t.pk)
            services.create_pause(owner, start_date=today + timedelta(days=5))  # planned: not active
            return owner

        counts = {}
        for n in (1, 20):
            owner = build(n)
            client = APIClient()
            client.force_authenticate(owner)
            with CaptureQueriesContext(connection) as ctx:
                res = client.get(f'{BASE}/today/')
            assert res.status_code == 200 and len(res.data['items']) == n
            counts[n] = len(ctx.captured_queries)
        assert counts[1] == counts[20], counts
        assert counts[20] <= 3, [q['sql'] for q in ctx.captured_queries]


# ----------------------------- check-ins ----------------------------------- #
class TestLogCheckin:
    def test_log_defaults_to_today_and_accumulates(self, api, water, today):
        first = api.post(f'{BASE}/checkins/', {'trackable_id': str(water.pk), 'value': '2'}, format='json')
        assert first.status_code == 200, first.data
        assert (first.data['date'], first.data['value'], first.data['status']) == (str(today), '2.00', 'partial')
        second = api.post(f'{BASE}/checkins/', {'trackable_id': str(water.pk), 'value': '6'}, format='json')
        assert (second.data['id'], second.data['value'], second.data['status']) == (first.data['id'], '8.00', 'done')

    def test_yesterday_ok_older_refused(self, api, water, today):
        ok = api.post(f'{BASE}/checkins/', {'trackable_id': str(water.pk), 'value': 1, 'date': str(YESTERDAY)},
                      format='json')
        assert ok.status_code == 200
        old = api.post(f'{BASE}/checkins/', {'trackable_id': str(water.pk), 'value': 1,
                                             'date': str(TODAY - timedelta(days=2))}, format='json')
        assert old.status_code == 400 and 'date' in old.data

    def test_source_is_always_manual(self, api, water, today):
        res = api.post(f'{BASE}/checkins/', {'trackable_id': str(water.pk), 'value': 1, 'source': 'agent'},
                       format='json')
        assert res.data['source'] == 'manual'

    def test_other_users_trackable_is_refused(self, api, foreign, today):
        res = api.post(f'{BASE}/checkins/', {'trackable_id': str(foreign.pk)}, format='json')
        assert res.status_code == 400 and 'trackable_id' in res.data
        assert not CheckIn.objects.exists()

    def test_quantity_needs_value(self, api, water, today):
        assert api.post(f'{BASE}/checkins/', {'trackable_id': str(water.pk)}, format='json').status_code == 400

    def test_skip(self, api, habit, today):
        res = api.post(f'{BASE}/checkins/skip/', {'trackable_id': str(habit.pk)}, format='json')
        assert res.status_code == 200 and res.data['status'] == 'skipped'


class TestIdempotency:
    def post(self, api, body, key):
        return api.post(f'{BASE}/checkins/', body, format='json', headers={'Idempotency-Key': key})

    def test_retry_replays_instead_of_double_counting(self, api, water, today):
        body = {'trackable_id': str(water.pk), 'value': '2'}
        first = self.post(api, body, 'key-1')
        retry = self.post(api, body, 'key-1')
        assert retry.status_code == 200
        assert retry.data == first.data
        assert retry.headers['Idempotent-Replayed'] == 'true'
        assert CheckIn.objects.get().value == Decimal('2.00')  # not 4

    def test_new_key_is_a_new_action(self, api, water, today):
        body = {'trackable_id': str(water.pk), 'value': '2'}
        self.post(api, body, 'key-1')
        self.post(api, body, 'key-2')
        assert CheckIn.objects.get().value == Decimal('4.00')

    def test_no_key_runs_every_time(self, api, water, today):
        body = {'trackable_id': str(water.pk), 'value': '2'}
        api.post(f'{BASE}/checkins/', body, format='json')
        api.post(f'{BASE}/checkins/', body, format='json')
        assert CheckIn.objects.get().value == Decimal('4.00')

    def test_same_key_other_body_is_422(self, api, water, today):
        self.post(api, {'trackable_id': str(water.pk), 'value': '2'}, 'key-1')
        res = self.post(api, {'trackable_id': str(water.pk), 'value': '3'}, 'key-1')
        assert res.status_code == 422
        assert CheckIn.objects.get().value == Decimal('2.00')

    def test_keys_are_per_user(self, api, water, other, today):
        theirs = services.create_trackable(other, name='Water', kind='quantity', start_date=D)
        client = APIClient()
        client.force_authenticate(other)
        self.post(api, {'trackable_id': str(water.pk), 'value': '2'}, 'shared-key')
        res = self.post(client, {'trackable_id': str(theirs.pk), 'value': '2'}, 'shared-key')
        assert res.status_code == 200 and res.data['trackable_id'] == str(theirs.pk)

    def test_errors_are_not_stored(self, api, user, water, today):
        import hashlib
        bad = self.post(api, {'trackable_id': str(water.pk)}, 'key-1')  # no value: 400
        assert bad.status_code == 400
        assert cache.get(f'idempotency:{user.pk}:{hashlib.sha256(b"key-1").hexdigest()}') is None
        assert CheckIn.objects.count() == 0
        # the same request again runs again (and fails again) rather than replaying
        assert self.post(api, {'trackable_id': str(water.pk)}, 'key-1').status_code == 400

    def test_concurrent_duplicate_is_409(self, api, user, water, today):
        import hashlib
        cache.add(f'idempotency:{user.pk}:{hashlib.sha256(b"key-1").hexdigest()}:lock', 1)
        res = self.post(api, {'trackable_id': str(water.pk), 'value': '2'}, 'key-1')
        assert res.status_code == 409
        assert not CheckIn.objects.exists()

    def test_overlong_key_is_400(self, api, water, today):
        assert self.post(api, {'trackable_id': str(water.pk), 'value': '2'}, 'k' * 256).status_code == 400

    def test_skip_is_idempotent_too(self, api, habit, today):
        for _ in range(2):
            res = api.post(f'{BASE}/checkins/skip/', {'trackable_id': str(habit.pk)}, format='json',
                           headers={'Idempotency-Key': 'skip-1'})
        assert res.headers.get('Idempotent-Replayed') == 'true'


class TestFixCheckin:
    def test_patch_sets_value(self, api, user, water, today):
        checkin = services.log_checkin(user, water, today, value=5)
        res = api.patch(f'{BASE}/checkins/{checkin.pk}/', {'value': '2'}, format='json')
        assert res.status_code == 200 and res.data['value'] == '2.00' and res.data['status'] == 'partial'

    def test_patch_old_checkin_is_refused(self, api, user, water, today):
        checkin = log(user, water, TODAY - timedelta(days=3), value=5)
        assert api.patch(f'{BASE}/checkins/{checkin.pk}/', {'value': '2'}, format='json').status_code == 400

    def test_delete(self, api, user, habit, today):
        checkin = services.log_checkin(user, habit, YESTERDAY)
        assert api.delete(f'{BASE}/checkins/{checkin.pk}/').status_code == 204
        assert not CheckIn.objects.exists()

    def test_other_users_checkin_is_404(self, api, other, foreign, today):
        checkin = services.log_checkin(other, foreign, today)
        assert api.patch(f'{BASE}/checkins/{checkin.pk}/', {'value': '2'}, format='json').status_code == 404
        assert api.delete(f'{BASE}/checkins/{checkin.pk}/').status_code == 404
        assert CheckIn.objects.filter(pk=checkin.pk).exists()


# ----------------------------- pauses -------------------------------------- #
class TestPauses:
    def test_create_global_and_specific(self, api, habit, today):
        everything = api.post(f'{BASE}/pauses/', {'start_date': str(today), 'reason': 'sick'}, format='json')
        assert everything.status_code == 201, everything.data
        assert everything.data['trackable_id'] is None
        one = api.post(f'{BASE}/pauses/', {'start_date': str(YESTERDAY), 'trackable_id': str(habit.pk)},
                       format='json')
        assert one.status_code == 201 and one.data['trackable_id'] == str(habit.pk)

    def test_backdated_start_is_refused(self, api, today):
        res = api.post(f'{BASE}/pauses/', {'start_date': str(TODAY - timedelta(days=2))}, format='json')
        assert res.status_code == 400 and 'start_date' in res.data

    def test_planned_vacation(self, api, today):
        res = api.post(f'{BASE}/pauses/', {'start_date': '2026-02-01', 'end_date': '2026-02-14',
                                           'reason': 'vacation'}, format='json')
        assert res.status_code == 201

    def test_other_users_trackable_is_refused(self, api, foreign, today):
        res = api.post(f'{BASE}/pauses/', {'start_date': str(today), 'trackable_id': str(foreign.pk)},
                       format='json')
        assert res.status_code == 400

    def test_list_active(self, api, user, other, today):
        pause(user, start_date=D, end_date=D + timedelta(days=2))  # over
        running = services.create_pause(user, start_date=today)
        pause(other, start_date=today)
        assert len(api.get(f'{BASE}/pauses/').data['results']) == 2
        active = api.get(f'{BASE}/pauses/?active=true').data['results']
        assert [p['id'] for p in active] == [str(running.pk)]

    def test_end(self, api, user):
        p = pause(user, start_date=TODAY - timedelta(days=5))
        with_today = APIClient()
        with_today.force_authenticate(user)
        from .test_services import as_of
        with as_of(TODAY):
            res = with_today.post(f'{BASE}/pauses/{p.pk}/end/', {}, format='json')
        assert res.status_code == 200 and res.data['end_date'] == str(YESTERDAY)

    def test_end_planned_cancels_it(self, api, today):
        created = api.post(f'{BASE}/pauses/', {'start_date': '2026-02-01'}, format='json').data
        assert api.post(f'{BASE}/pauses/{created["id"]}/end/', {}, format='json').status_code == 204
        assert not Pause.objects.exists()

    def test_other_users_pause_is_404(self, api, other, today):
        p = services.create_pause(other, start_date=today)
        assert api.get(f'{BASE}/pauses/{p.pk}/').status_code == 404
        assert api.post(f'{BASE}/pauses/{p.pk}/end/', {}, format='json').status_code == 404
