"""Single source of truth for the app name and version.

Bump __version__ here when cutting a release — everything else
(window title, header badge, /status output, CHANGELOG references)
should read from this instead of hardcoding a string.
"""

APP_NAME    = "Aether"
__version__ = "1.0.0"


def version_string() -> str:
    return f"{APP_NAME} v{__version__}"
