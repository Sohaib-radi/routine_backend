from django.contrib.auth.models import Permission
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User

from ..models import Profile


class ProfileSelectionTests(APITestCase):
    """What every signed-in user can do: see the active profiles and pick one."""

    def setUp(self):
        self.default = Profile.objects.default()  # created by the data migration
        self.calm = Profile.objects.create(name='Calm', color='#2B2350', notification_style='silent')
        self.hidden = Profile.objects.create(name='Hidden', is_active=False)
        self.user = User.objects.create_user('sara@example.com', 'S3cure-pass!')
        self.client.force_authenticate(self.user)

    def test_new_user_gets_default_profile(self):
        self.assertEqual(self.user.profile, self.default)

    def test_list_shows_only_active_profiles(self):
        res = self.client.get(reverse('profile-list'))
        self.assertEqual({p['name'] for p in res.data}, {'Default', 'Calm'})

    def test_list_requires_authentication(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(reverse('profile-list')).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_select_profile(self):
        res = self.client.post(reverse('profile-select', args=[self.calm.pk]))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.profile, self.calm)
        me = self.client.get(reverse('me'))
        self.assertEqual(me.data['profile']['name'], 'Calm')
        self.assertEqual(me.data['profile']['notification_style'], 'silent')

    def test_cannot_select_inactive_profile(self):
        res = self.client.post(reverse('profile-select', args=[self.hidden.pk]))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_regular_user_cannot_create_edit_or_delete(self):
        self.assertEqual(
            self.client.post(reverse('profile-list'), {'name': 'Mine'}).status_code, status.HTTP_403_FORBIDDEN
        )
        self.assertEqual(
            self.client.patch(reverse('profile-detail', args=[self.calm.pk]), {'color': '#000000'}).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.delete(reverse('profile-detail', args=[self.calm.pk])).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.post(reverse('profile-set-default', args=[self.calm.pk])).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.post(reverse('profile-deactivate', args=[self.calm.pk])).status_code,
            status.HTTP_403_FORBIDDEN,
        )


class ProfileAdministrationTests(APITestCase):
    """What administrators can do through the API."""

    def setUp(self):
        self.default = Profile.objects.default()
        self.admin = User.objects.create_superuser('admin@example.com', 'S3cure-pass!')
        self.client.force_authenticate(self.admin)

    def test_create_profile(self):
        res = self.client.post(reverse('profile-list'), {
            'name': 'Focus', 'color': '#728668', 'notification_style': 'alert',
            'notification_sound': 'chime', 'notification_vibrate': False,
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertFalse(res.data['is_default'])

    def test_invalid_color_rejected(self):
        res = self.client.post(reverse('profile-list'), {'name': 'Bad', 'color': 'green'})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_sees_inactive_profiles(self):
        Profile.objects.create(name='Hidden', is_active=False)
        names = {p['name'] for p in self.client.get(reverse('profile-list')).data}
        self.assertIn('Hidden', names)

    def test_set_default_moves_the_flag(self):
        focus = Profile.objects.create(name='Focus')
        res = self.client.post(reverse('profile-set-default', args=[focus.pk]))
        self.assertTrue(res.data['is_default'])
        self.assertEqual(Profile.objects.default(), focus)
        self.assertEqual(Profile.objects.filter(is_default=True).count(), 1)

    def test_is_active_is_read_only(self):
        focus = Profile.objects.create(name='Focus')
        res = self.client.patch(reverse('profile-detail', args=[focus.pk]), {'is_active': False})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        focus.refresh_from_db()
        self.assertTrue(focus.is_active)

    def test_deactivate_moves_users(self):
        focus = Profile.objects.create(name='Focus')
        user = User.objects.create_user('sara@example.com', 'S3cure-pass!', profile=focus)
        res = self.client.post(reverse('profile-deactivate', args=[focus.pk]))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.data['is_active'])
        user.refresh_from_db()
        self.assertEqual(user.profile, self.default)

    def test_cannot_deactivate_default(self):
        res = self.client.post(reverse('profile-deactivate', args=[self.default.pk]))
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.default.refresh_from_db()
        self.assertTrue(self.default.is_active)

    def test_activate(self):
        hidden = Profile.objects.create(name='Hidden', is_active=False)
        res = self.client.post(reverse('profile-activate', args=[hidden.pk]))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data['is_active'])

    def test_cannot_delete_default(self):
        res = self.client.delete(reverse('profile-detail', args=[self.default.pk]))
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_delete_profile_in_use(self):
        focus = Profile.objects.create(name='Focus')
        User.objects.create_user('sara@example.com', 'S3cure-pass!', profile=focus)
        res = self.client.delete(reverse('profile-detail', args=[focus.pk]))
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_delete_unused_profile(self):
        focus = Profile.objects.create(name='Focus')
        res = self.client.delete(reverse('profile-detail', args=[focus.pk]))
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

    def test_permission_grants_management(self):
        staff = User.objects.create_user('staff@example.com', 'S3cure-pass!')
        staff.user_permissions.add(Permission.objects.get(codename='add_profile'))
        self.client.force_authenticate(User.objects.get(pk=staff.pk))
        res = self.client.post(reverse('profile-list'), {'name': 'Focus'})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)


class ProfileAdminPagesTests(APITestCase):
    """The Unfold admin pages for profiles."""

    def setUp(self):
        self.default = Profile.objects.default()
        self.admin = User.objects.create_superuser('admin@example.com', 'S3cure-pass!')
        self.client.force_login(self.admin)

    def test_pages_render(self):
        for url in (
            reverse('admin:profiles_profile_changelist'),
            reverse('admin:profiles_profile_add'),
            reverse('admin:profiles_profile_change', args=[self.default.pk]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_make_default_action(self):
        focus = Profile.objects.create(name='Focus')
        res = self.client.get(reverse('admin:profiles_profile_make_default_detail', args=[focus.pk]))
        self.assertEqual(res.status_code, 302)
        self.assertEqual(Profile.objects.default(), focus)

    def test_is_active_not_editable_in_admin_form(self):
        res = self.client.get(reverse('admin:profiles_profile_change', args=[self.default.pk]))
        self.assertNotIn('is_active', res.context['adminform'].form.fields)

    def test_deactivate_action_moves_users(self):
        focus = Profile.objects.create(name='Focus')
        user = User.objects.create_user('sara@example.com', 'S3cure-pass!', profile=focus)
        res = self.client.get(reverse('admin:profiles_profile_deactivate_detail', args=[focus.pk]))
        self.assertEqual(res.status_code, 302)
        focus.refresh_from_db()
        user.refresh_from_db()
        self.assertFalse(focus.is_active)
        self.assertEqual(user.profile, self.default)

    def test_deactivate_action_refuses_default(self):
        res = self.client.get(reverse('admin:profiles_profile_deactivate_detail', args=[self.default.pk]),
                              follow=True)
        self.assertContains(res, 'The default profile cannot be deactivated.')
        self.default.refresh_from_db()
        self.assertTrue(self.default.is_active)

    def test_activate_action(self):
        hidden = Profile.objects.create(name='Hidden', is_active=False)
        res = self.client.get(reverse('admin:profiles_profile_activate_detail', args=[hidden.pk]))
        self.assertEqual(res.status_code, 302)
        hidden.refresh_from_db()
        self.assertTrue(hidden.is_active)

    def test_add_and_edit_through_admin(self):
        form = {
            'name': 'Focus', 'description': '', 'position': 1, 'color': '#728668',
            'notification_style': 'banner', 'notification_sound': 'default', 'notification_vibrate': 'on',
        }
        self.assertEqual(self.client.post(reverse('admin:profiles_profile_add'), form).status_code, 302)
        focus = Profile.objects.get(name='Focus')
        form['color'] = '#000000'
        res = self.client.post(reverse('admin:profiles_profile_change', args=[focus.pk]), form)
        self.assertEqual(res.status_code, 302)
        focus.refresh_from_db()
        self.assertEqual(focus.color, '#000000')
