"""Local development: DEBUG on, permissive hosts. Postgres/Redis from docker-compose."""

from .base import *  # noqa: F401,F403
from .base import env

DEBUG = env.bool('DEBUG', default=True)
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1', '0.0.0.0'])
