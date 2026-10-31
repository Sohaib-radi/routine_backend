import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(client):
    from apps.accounts.models import User

    client.force_login(User.objects.create_superuser('admin@example.com', 'S3cure-pass!'))
    return client


@pytest.mark.parametrize('name', ['healthplan', 'weightentry', 'healthmetric'])
def test_admin_is_read_only(admin_client, name):
    assert admin_client.get(reverse(f'admin:health_{name}_changelist')).status_code == 200
    assert admin_client.get(reverse(f'admin:health_{name}_add')).status_code == 403
