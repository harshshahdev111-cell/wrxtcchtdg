"""Docker runtime settings; debug stays disabled regardless of .env."""

from .base import *  # noqa: F403

DEBUG = False
