"""
Settings for the Unfold admin theme (``settings.UNFOLD``).

This is the only place where admin colours are defined; the single exception is the
light sage accent in ``static/admin_theme/css/admin.css``, because Unfold has no
setting for it.

The scales are derived from the app's two brand colours in OKLCH (hue from the
anchor, lightness stepped, chroma reduced until the colour fits sRGB). They meet
WCAG AA (contrast of at least 4.5:1) for every
text/background pair Unfold uses. ``accounts/test_admin.py`` measures those
pairs, so a palette change that breaks contrast fails the tests.

* ``base-900`` indigo ``#2B2350`` (brand): dark surfaces and important text.
* ``primary-500`` sage ``#728668`` (brand): focus rings and highlights. It is
  only 3.94:1 on white, so buttons and links use the darker ``primary-600``
  ``#5B7051`` (5.41:1).

Values that need a request or the URL configuration (static paths, reverses,
permission checks) are callables or lazy objects, because settings are loaded
before either exists.
"""

from django.templatetags.static import static
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

BRAND_INDIGO = '#2B2350'
BRAND_SAGE = '#728668'

BASE_COLORS = {
    '50': '#F4F3FF',
    '100': '#E9E7FB',
    '200': '#D7D6EE',
    '300': '#BEBCD9',
    '400': '#9D9ABE',
    '500': '#6B6791',
    '600': '#56517F',
    '700': '#464070',
    '800': '#383062',
    '900': BRAND_INDIGO,
    '950': '#181134',
}

PRIMARY_COLORS = {
    '50': '#F5F6F4',
    '100': '#EAEDE8',
    '200': '#D8DED5',
    '300': '#BCC6B7',
    '400': '#95A48D',
    '500': BRAND_SAGE,
    '600': '#5B7051',
    '700': '#495C40',
    '800': '#3B4A34',
    '900': '#2E3928',
    '950': '#1E271A',
}


def _has_perm(permission):
    """Return a sidebar permission check for ``permission`` (``app_label.codename``)."""
    def check(request):
        return request.user.has_perm(permission)
    return check


def _nav_item(title, icon, url_name, permission):
    return {
        'title': title,
        'icon': icon,
        'link': reverse_lazy(url_name),
        'permission': _has_perm(permission),
    }


UNFOLD = {
    # Brand name: never translated.
    'SITE_TITLE': 'Routine',
    'SITE_HEADER': 'Routine',
    'SITE_SYMBOL': 'event_repeat',
    'SHOW_HISTORY': True,
    'SHOW_VIEW_ON_SITE': False,
    # Sidebar user menu offers settings.LANGUAGES (ar, en, fr) through set_language.
    'SHOW_LANGUAGES': True,
    'LOGIN': {
        'image': lambda request: static('admin_theme/img/login.svg'),
    },
    'STYLES': [
        lambda request: static('admin_theme/css/admin.css'),
    ],
    'COLORS': {
        'base': BASE_COLORS,
        'primary': PRIMARY_COLORS,
    },
    'SIDEBAR': {
        'show_search': True,
        'show_all_applications': False,
        'navigation': [
            {
                'title': _('Accounts'),
                'separator': True,
                'items': [
                    _nav_item(_('Users'), 'person', 'admin:accounts_user_changelist', 'accounts.view_user'),
                    _nav_item(_('Profiles'), 'account_circle', 'admin:profiles_profile_changelist',
                              'profiles.view_profile'),
                    _nav_item(_('Roles'), 'group', 'admin:auth_group_changelist', 'auth.view_group'),
                ],
            },
            {
                'title': _('Tracking'),
                'separator': True,
                'items': [
                    _nav_item(_('Trackables'), 'checklist', 'admin:tracking_trackable_changelist',
                              'tracking.view_trackable'),
                    _nav_item(_('Check-ins'), 'task_alt', 'admin:tracking_checkin_changelist',
                              'tracking.view_checkin'),
                    _nav_item(_('Pauses'), 'pause_circle', 'admin:tracking_pause_changelist',
                              'tracking.view_pause'),
                    _nav_item(_('Streaks'), 'local_fire_department', 'admin:tracking_streakcache_changelist',
                              'tracking.view_streakcache'),
                ],
            },
            {
                'title': _('Health'),
                'separator': True,
                'items': [
                    _nav_item(_('Plans'), 'monitor_heart', 'admin:health_healthplan_changelist',
                              'health.view_healthplan'),
                    _nav_item(_('Weight'), 'monitor_weight', 'admin:health_weightentry_changelist',
                              'health.view_weightentry'),
                    _nav_item(_('Foods'), 'restaurant', 'admin:health_food_changelist', 'health.view_food'),
                    _nav_item(_('Meals'), 'lunch_dining', 'admin:health_meal_changelist', 'health.view_meal'),
                    _nav_item(_('Metric trackables'), 'link', 'admin:health_healthmetric_changelist',
                              'health.view_healthmetric'),
                ],
            },
            {
                'title': _('AI'),
                'separator': True,
                'items': [
                    _nav_item(_('Feature configuration'), 'tune', 'admin:ai_aifeatureconfig_changelist',
                              'ai.view_aifeatureconfig'),
                    _nav_item(_('Prices'), 'payments', 'admin:ai_providerprice_changelist', 'ai.view_providerprice'),
                    _nav_item(_('Usage'), 'receipt_long', 'admin:ai_aiusage_changelist', 'ai.view_aiusage'),
                    _nav_item(_('Daily usage'), 'bar_chart', 'admin:ai_aiusage_daily', 'ai.view_aiusage'),
                    _nav_item(_('Meal drafts'), 'edit_note', 'admin:ai_mealdraft_changelist', 'ai.view_mealdraft'),
                ],
            },
            {
                'title': _('Security'),
                'separator': True,
                'items': [
                    _nav_item(_('Outstanding tokens'), 'key', 'admin:token_blacklist_outstandingtoken_changelist',
                              'token_blacklist.view_outstandingtoken'),
                    _nav_item(_('Blacklisted tokens'), 'block', 'admin:token_blacklist_blacklistedtoken_changelist',
                              'token_blacklist.view_blacklistedtoken'),
                ],
            },
        ],
    },
}
