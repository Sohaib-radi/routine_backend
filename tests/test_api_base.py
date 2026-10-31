"""Project-wide API guarantees: versioned URLs and an accurate OpenAPI schema."""

import pytest
from django.core.management import call_command
from django.urls import Resolver404, resolve
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.validation import validate_schema

pytestmark = pytest.mark.django_db


def test_schema_is_valid_openapi_without_warnings(tmp_path):
    # --fail-on-warn: any view spectacular cannot describe fails the build.
    call_command('spectacular', '--validate', '--fail-on-warn', '--file', str(tmp_path / 'schema.yml'))


def test_every_api_path_is_versioned():
    schema = SchemaGenerator().get_schema(request=None, public=True)
    validate_schema(schema)
    assert schema['paths']
    assert all(path.startswith('/api/v1/') for path in schema['paths'])


@pytest.mark.parametrize('path', ['/api/me/', '/api/auth/token/', '/api/profiles/'])
def test_unversioned_api_is_gone(path):
    with pytest.raises(Resolver404):
        resolve(path)


def test_auth_endpoints_exist():
    for path in ('/api/v1/auth/register/', '/api/v1/auth/token/', '/api/v1/auth/token/refresh/',
                 '/api/v1/auth/logout/', '/api/v1/me/'):
        resolve(path)


def test_me_schema_exposes_timezone_and_profile():
    schema = SchemaGenerator().get_schema(request=None, public=True)
    components = schema['components']['schemas']
    assert {'timezone', 'profile_id'} <= set(components['PatchedUserRequest']['properties'])
    assert {'timezone', 'profile'} <= set(components['User']['properties'])
    assert 'profile_id' not in components['User']['properties']  # write-only


def test_docs_page_renders(client):
    assert client.get('/api/v1/docs/').status_code == 200
    assert client.get('/api/v1/schema/').status_code == 200
