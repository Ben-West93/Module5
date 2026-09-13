# app/config.py
#
# Settings and environment variables.
#
# Keeping configuration in one module means values are defined once and
# imported where needed, instead of being retyped as literals across the
# codebase.

import os


class Settings:
    """
    Application settings.

    Each value falls back to a default, so the app runs with no environment
    configured at all. Override any of them by exporting the matching
    variable before starting the server, e.g.:

        export APP_NAME="My Recipe Box"
    """

    app_name: str = os.getenv("APP_NAME", "Recipe API")
    version: str = os.getenv("APP_VERSION", "0.1.0")
    description: str = os.getenv(
        "APP_DESCRIPTION",
        "A structured FastAPI project with recipe and ingredient resources.",
    )
    # os.getenv always returns a string, so compare rather than cast to bool —
    # bool("False") is True, which is a classic source of confusion here.
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"


# A single shared instance, imported elsewhere as:
#     from app.config import settings
settings = Settings()

