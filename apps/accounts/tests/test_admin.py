"""
Tests for the Unfold admin theme (``config/unfold.py``) and the admin pages.

The contrast tests compute WCAG 2 contrast
ratios for the text/background pairs Unfold uses, so a palette change that
breaks AA (4.5:1) fails here.
"""

import re

from django.conf import settings
from django.contrib import admin
from django.contrib.staticfiles import finders
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from unfold.admin import ModelAdmin

from config.unfold import BASE_COLORS, BRAND_INDIGO, BRAND_SAGE, PRIMARY_COLORS, UNFOLD

from apps.profiles.models import Profile

from ..models import User

WHITE = '#FFFFFF'
ACCENT = '#BCC6B7'
AA = 4.5


def _linear(channel):
    channel /= 255
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def luminance(hex_colour):
    """WCAG relative luminance of ``#RRGGBB``."""
    red, green, blue = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _linear(red) + 0.7152 * _linear(green) + 0.0722 * _linear(blue)


def contrast(first, second):
    """WCAG contrast ratio between two ``#RRGGBB`` colours."""
    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


class PaletteTests(SimpleTestCase):
    """Brand anchors and WCAG AA contrast."""

    def test_brand_anchors(self):
        self.assertEqual(BASE_COLORS['900'], BRAND_INDIGO)
        self.assertEqual(PRIMARY_COLORS['500'], BRAND_SAGE)
        self.assertEqual(PRIMARY_COLORS['300'], ACCENT)
        self.assertIs(UNFOLD['COLORS']['base'], BASE_COLORS)
        self.assertIs(UNFOLD['COLORS']['primary'], PRIMARY_COLORS)

    def test_scales_are_complete_and_ordered_light_to_dark(self):
        steps = ['50', '100', '200', '300', '400', '500', '600', '700', '800', '900', '950']
        for name, scale in (('base', BASE_COLORS), ('primary', PRIMARY_COLORS)):
            with self.subTest(scale=name):
                self.assertEqual(list(scale), steps)
                values = [luminance(scale[step]) for step in steps]
                self.assertEqual(values, sorted(values, reverse=True))

    def test_text_pairs_meet_wcag_aa(self):
        # Unfold's font colours: subtle = base-500/400, default = base-600/300,
        # important = base-900/100 (light/dark). Primary is used for buttons
        # (white text) and links.
        pairs = {
            'subtle text on white': (BASE_COLORS['500'], WHITE),
            'subtle text on base-50': (BASE_COLORS['500'], BASE_COLORS['50']),
            'default text on white': (BASE_COLORS['600'], WHITE),
            'important text on white': (BASE_COLORS['900'], WHITE),
            'subtle text on dark base-900': (BASE_COLORS['400'], BASE_COLORS['900']),
            'subtle text on dark base-950': (BASE_COLORS['400'], BASE_COLORS['950']),
            'default text on dark base-900': (BASE_COLORS['300'], BASE_COLORS['900']),
            'important text on dark base-900': (BASE_COLORS['100'], BASE_COLORS['900']),
            'button text on primary-600': (WHITE, PRIMARY_COLORS['600']),
            'link primary-600 on white': (PRIMARY_COLORS['600'], WHITE),
            'link primary-600 on base-50': (PRIMARY_COLORS['600'], BASE_COLORS['50']),
            'dark-mode link primary-400 on base-900': (PRIMARY_COLORS['400'], BASE_COLORS['900']),
            'accent on indigo': (ACCENT, BRAND_INDIGO),
        }
        for name, (text, background) in pairs.items():
            with self.subTest(pair=name):
                self.assertGreaterEqual(contrast(text, background), AA)

    def test_admin_css_defines_no_colour_but_the_accent(self):
        css = open(finders.find('admin_theme/css/admin.css'), encoding='utf-8').read()
        self.assertEqual(set(re.findall(r'#[0-9A-Fa-f]{6}\b', css)), {ACCENT})


class AssetsTests(SimpleTestCase):
    """Typeface, licence and login artwork are present and wired in."""

    def test_font_files_and_licence(self):
        for name in ('readex-pro-arabic.woff2', 'readex-pro-latin.woff2', 'readex-pro-latin-ext.woff2'):
            with self.subTest(font=name):
                self.assertIsNotNone(finders.find(f'admin_theme/fonts/readex-pro/{name}'))
        licence = open(finders.find('admin_theme/fonts/readex-pro/OFL.txt'), encoding='utf-8').read()
        self.assertIn('SIL Open Font License, Version 1.1', licence)

    def test_admin_css_uses_readex_pro(self):
        css = open(finders.find('admin_theme/css/admin.css'), encoding='utf-8').read()
        self.assertIn('--font-sans: "Readex Pro"', css)
        self.assertEqual(css.count('@font-face'), 3)

    def test_login_image_and_styles_resolve_to_static_files(self):
        request = RequestFactory().get('/')
        self.assertEqual(UNFOLD['LOGIN']['image'](request), '/static/admin_theme/img/login.svg')
        self.assertEqual([style(request) for style in UNFOLD['STYLES']], ['/static/admin_theme/css/admin.css'])
        self.assertIsNotNone(finders.find('admin_theme/img/login.svg'))


class SettingsTests(SimpleTestCase):
    """App order, site identity and language switcher."""

    def test_unfold_precedes_django_admin(self):
        apps = settings.INSTALLED_APPS
        self.assertLess(apps.index('unfold'), apps.index('django.contrib.admin'))

    def test_site_identity_is_the_untranslated_brand(self):
        self.assertEqual(UNFOLD['SITE_TITLE'], 'Routine')
        self.assertEqual(UNFOLD['SITE_HEADER'], 'Routine')

    def test_language_switcher_is_enabled(self):
        self.assertTrue(UNFOLD['SHOW_LANGUAGES'])
        self.assertEqual([code for code, _ in settings.LANGUAGES], ['ar', 'en', 'fr'])


class SidebarTests(TestCase):
    """Sidebar groups, icons, links and permission checks."""

    def test_groups_and_items(self):
        layout = [
            ([str(item['title']) for item in group['items']], str(group['title']))
            for group in UNFOLD['SIDEBAR']['navigation']
        ]
        self.assertEqual(layout, [
            (['Users', 'Profiles', 'Roles'], 'Accounts'),
            (['Trackables', 'Check-ins', 'Pauses', 'Streaks'], 'Tracking'),
            (['Outstanding tokens', 'Blacklisted tokens'], 'Security'),
        ])

    def test_every_item_has_icon_link_and_permission(self):
        links = {
            '/admin/accounts/user/', '/admin/profiles/profile/', '/admin/auth/group/',
            '/admin/tracking/trackable/', '/admin/tracking/checkin/', '/admin/tracking/pause/',
            '/admin/tracking/streakcache/',
            '/admin/token_blacklist/outstandingtoken/', '/admin/token_blacklist/blacklistedtoken/',
        }
        seen = set()
        for group in UNFOLD['SIDEBAR']['navigation']:
            for item in group['items']:
                with self.subTest(item=str(item['title'])):
                    self.assertTrue(item['icon'])
                    self.assertTrue(callable(item['permission']))
                    seen.add(str(item['link']))
        self.assertEqual(seen, links)

    def test_permission_checks_follow_model_permissions(self):
        request = RequestFactory().get('/admin/')
        items = [item for group in UNFOLD['SIDEBAR']['navigation'] for item in group['items']]
        request.user = User.objects.create_user('staff@example.com', 'S3cure-pass!', is_staff=True)
        self.assertEqual([item['permission'](request) for item in items], [False] * len(items))
        request.user = User.objects.create_superuser('admin@example.com', 'S3cure-pass!')
        self.assertEqual([item['permission'](request) for item in items], [True] * len(items))


class AdminPagesTests(TestCase):
    """Every registered admin uses Unfold, and its pages render."""

    def setUp(self):
        self.admin = User.objects.create_superuser('admin@example.com', 'S3cure-pass!')
        self.client.force_login(self.admin)

    def test_every_admin_uses_unfold(self):
        for model, model_admin in admin.site._registry.items():
            with self.subTest(model=model._meta.label):
                self.assertIsInstance(model_admin, ModelAdmin)

    def test_changelists_render(self):
        for model in admin.site._registry:
            url = reverse(f'admin:{model._meta.app_label}_{model._meta.model_name}_changelist')
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_user_pages_render(self):
        for url in (
            reverse('admin:accounts_user_add'),
            reverse('admin:accounts_user_change', args=[self.admin.pk]),
            reverse('admin:auth_user_password_change', args=[self.admin.pk]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_create_user_from_admin(self):
        res = self.client.post(reverse('admin:accounts_user_add'), {
            'email': 'new@example.com', 'usable_password': 'true',
            'password1': 'S3cure-pass!', 'password2': 'S3cure-pass!',
        })
        self.assertEqual(res.status_code, 302)
        user = User.objects.get(email='new@example.com')
        self.assertEqual(user.profile, Profile.objects.default())

    def test_site_root_redirects_to_admin_login(self):
        self.client.logout()
        res = self.client.get('/', follow=True)
        self.assertEqual(res.redirect_chain[-1][0], '/admin/login/?next=/admin/')
        self.assertEqual(res.status_code, 200)

    def test_login_page_renders_in_arabic(self):
        self.client.logout()
        res = self.client.get(reverse('admin:login'), headers={'accept-language': 'ar'})
        self.assertContains(res, 'dir="rtl"')
        self.assertContains(res, '/static/admin_theme/css/admin.css')
