"""Local development settings, with debug enabled unless overridden in .env."""

import os

from .base import *  # noqa: F403

DEBUG = os.getenv("DEBUG", "1") == "1"
