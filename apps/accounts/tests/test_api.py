from django.contrib.auth.models import Group, Permission
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.profiles.models import Profile

from ..models import User


class AuthTests(APITestCase):
    def test_register_starts_on_default_profile(self):
        res = self.client.post(reverse('register'), {
            'email': 'sara@example.com', 'password': 'S3cure-pass!', 'first_name': 'Sara',
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(email='sara@example.com')
        self.assertEqual(user.profile, Profile.objects.default())

    def test_register_rejects_weak_password(self):
        res = self.client.post(reverse('register'), {'email': 'a@example.com', 'password': '123'})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_obtain_token_and_access_me(self):
        User.objects.create_user('sara@example.com', 'S3cure-pass!')
        res = self.client.post(reverse('token_obtain_pair'), {
            'email': 'sara@example.com', 'password': 'S3cure-pass!',
        })
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        me = self.client.get(reverse('me'))
        self.assertEqual(me.data['email'], 'sara@example.com')
        self.assertEqual(me.data['profile']['name'], 'Default')

    def test_register_with_timezone(self):
        res = self.client.post(reverse('register'), {
            'email': 'sara@example.com', 'password': 'S3cure-pass!', 'timezone': 'Africa/Cairo',
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(User.objects.get(email='sara@example.com').timezone, 'Africa/Cairo')

    def test_register_rejects_invalid_timezone(self):
        res = self.client.post(reverse('register'), {
            'email': 'sara@example.com', 'password': 'S3cure-pass!', 'timezone': 'Cairo',
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('timezone', res.data)

    def test_register_rejects_duplicate_email(self):
        User.objects.create_user('sara@example.com', 'S3cure-pass!')
        res = self.client.post(reverse('register'), {'email': 'sara@example.com', 'password': 'S3cure-pass!'})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_refresh_token(self):
        User.objects.create_user('sara@example.com', 'S3cure-pass!')
        tokens = self.client.post(reverse('token_obtain_pair'), {
            'email': 'sara@example.com', 'password': 'S3cure-pass!',
        }).data
        res = self.client.post(reverse('token_refresh'), {'refresh': tokens['refresh']})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('access', res.data)

    def test_me_update_timezone_and_profile(self):
        calm = Profile.objects.create(name='Calm')
        user = User.objects.create_user('sara@example.com', 'S3cure-pass!')
        self.client.force_authenticate(user)
        res = self.client.patch(reverse('me'), {'timezone': 'Africa/Cairo', 'profile_id': str(calm.pk)})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['timezone'], 'Africa/Cairo')
        self.assertEqual(res.data['profile']['name'], 'Calm')
        user.refresh_from_db()
        self.assertEqual((user.timezone, user.profile), ('Africa/Cairo', calm))

    def test_me_rejects_invalid_timezone_and_inactive_profile(self):
        hidden = Profile.objects.create(name='Hidden', is_active=False)
        self.client.force_authenticate(User.objects.create_user('sara@example.com', 'S3cure-pass!'))
        self.assertEqual(
            self.client.patch(reverse('me'), {'timezone': 'Mars/Base'}).status_code, status.HTTP_400_BAD_REQUEST
        )
        self.assertEqual(
            self.client.patch(reverse('me'), {'profile_id': str(hidden.pk)}).status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_me_cannot_escalate(self):
        user = User.objects.create_user('sara@example.com', 'S3cure-pass!')
        self.client.force_authenticate(user)
        self.client.patch(reverse('me'), {'is_staff': True, 'email': 'other@example.com'})
        user.refresh_from_db()
        self.assertFalse(user.is_staff)
        self.assertEqual(user.email, 'sara@example.com')

    def test_me_requires_auth(self):
        self.assertEqual(self.client.get(reverse('me')).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_urls_are_versioned(self):
        self.assertEqual(reverse('me'), '/api/v1/me/')
        self.assertEqual(reverse('register'), '/api/v1/auth/register/')

    def test_change_password(self):
        user = User.objects.create_user('sara@example.com', 'S3cure-pass!')
        self.client.force_authenticate(user)
        res = self.client.post(reverse('change_password'), {
            'old_password': 'S3cure-pass!', 'new_password': 'An0ther-pass!',
        })
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        user.refresh_from_db()
        self.assertTrue(user.check_password('An0ther-pass!'))


class PermissionManagementTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user('sara@example.com', 'S3cure-pass!')
        self.client.force_authenticate(self.user)

    def test_regular_user_cannot_list_users(self):
        self.assertEqual(self.client.get(reverse('user-list')).status_code, status.HTTP_403_FORBIDDEN)

    def test_role_grants_access(self):
        role = Group.objects.create(name='Support')
        role.permissions.add(Permission.objects.get(codename='view_user'))
        self.user.groups.add(role)
        self.user = User.objects.get(pk=self.user.pk)  # clear permission cache
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get(reverse('user-list')).status_code, status.HTTP_200_OK)
        # view permission does not grant write access
        res = self.client.post(reverse('user-list'), {'email': 'x@example.com', 'password': 'S3cure-pass!'})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_superuser_manages_roles_and_users(self):
        admin = User.objects.create_superuser('admin@example.com', 'S3cure-pass!')
        self.client.force_authenticate(admin)
        perm = Permission.objects.get(codename='view_user')
        role = self.client.post(reverse('role-list'), {'name': 'Support', 'permissions': [perm.pk]}).data
        res = self.client.patch(reverse('user-detail', args=[self.user.pk]), {'groups': [role['id']]})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(User.objects.get(pk=self.user.pk).has_perm('accounts.view_user'))
