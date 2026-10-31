"""The admin guide shown inside the admin (/admin/help/health/) and the "How to use this page" buttons."""

import re

import pytest
from django.urls import reverse

from apps.health.models import Food, HealthMetric, HealthPlan, Meal, WeightEntry
from config.admin_help import GUIDES_DIR, render_guide

pytestmark = pytest.mark.django_db

PAGES = {  # admin page -> section of the guide its button opens
    'healthplan': '3-plans',
    'weightentry': '4-weight',
    'food': '5-foods',
    'meal': '6-meals',
    'healthmetric': '7-metric-trackables',
}


@pytest.fixture
def admin_client(client):
    from apps.accounts.models import User

    client.force_login(User.objects.create_superuser('admin@example.com', 'S3cure-pass!'))
    return client


def test_help_page_renders_inside_the_admin(admin_client):
    r = admin_client.get('/admin/help/health/')
    assert r.status_code == 200
    html = r.content.decode()
    assert 'How to use the Health pages' in html            # title
    assert '<table>' in html and '<h2 id="5-foods">' in html  # rendered Markdown, with anchors
    assert 'admin-help-toc' in html                         # table of contents
    assert 'Health' in html and 'admin/help' in html        # inside the admin layout (sidebar)


def test_every_section_a_button_points_to_exists():
    html, _ = render_guide('health')
    ids = set(re.findall(r'id="([^"]+)"', html))
    assert set(PAGES.values()) <= ids


def test_every_link_inside_the_guide_works():
    html, _ = render_guide('health')
    ids = set(re.findall(r'id="([^"]+)"', html))
    for href in re.findall(r'href="([^"]+)"', html):
        assert href.startswith('#'), f'{href}: links outside the guide do not work in the admin'
        assert href[1:] in ids, f'{href}: no such heading'


def test_the_guide_has_no_links_to_developer_docs():
    assert '.md)' not in (GUIDES_DIR / 'health.md').read_text(encoding='utf-8')


def test_help_requires_staff_login(client):
    r = client.get('/admin/help/health/')
    assert r.status_code == 302 and '/admin/login/' in r['Location']


def test_unknown_guide(admin_client):
    assert admin_client.get('/admin/help/nothing/').status_code == 404


@pytest.mark.parametrize(('name', 'section'), PAGES.items())
def test_each_health_list_has_its_help_button(admin_client, name, section):
    html = admin_client.get(reverse(f'admin:health_{name}_changelist')).content.decode()
    assert f'href="/admin/help/health/#{section}"' in html
    assert 'How to use this page' in html


def test_detail_pages_have_the_help_button_too(admin_client, user):
    from apps.health import services

    food = Food.objects.create(key='ful', source='nni', name_en='Ful', kcal=1, protein_g=0, carbs_g=0, fat_g=0)
    meal = services.log_meal(user, date=__import__('apps.tracking.dates', fromlist=['x']).user_today(user),
                             meal_type='lunch', items=[{'food': food, 'grams': 10}])
    for name, obj, section in (('food', food, '5-foods'), ('meal', meal, '6-meals')):
        html = admin_client.get(reverse(f'admin:health_{name}_change', args=[obj.pk])).content.decode()
        assert f'href="/admin/help/health/#{section}"' in html, name
    assert admin_client.get(reverse('admin:health_food_add')).content.decode().count('/admin/help/health/#5-foods') == 1


def test_side_panel_has_number_badges_and_no_duplicate_contents(admin_client):
    html = admin_client.get('/admin/help/health/').content.decode()
    assert 'On this page' in html
    assert '<span class="admin-help-toc-num">5</span><span class="admin-help-toc-text">Foods</span>' in html
    assert '<span class="admin-help-toc-subnum">5.4</span>' in html
    assert 'id="contents"' not in html and 'href="#contents"' not in html  # the side panel replaces it


def test_guide_stays_left_to_right_in_arabic(admin_client):
    html = admin_client.get('/admin/help/health/', HTTP_ACCEPT_LANGUAGE='ar').content.decode()
    assert '<div class="admin-help" dir="ltr" lang="en">' in html


def test_nested_lists_render_as_lists():
    """Nested blocks in the guide must be indented 4 spaces, or they collapse into the line above."""
    html, _ = render_guide('health')
    assert '- Forgot your password?' not in html
    assert '<li>Forgot your password?' in html
