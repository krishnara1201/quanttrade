#!/bin/sh
set -e
# Must be invoked as `python -m celery`, not bare `celery` -- Celery's
# console-script entry point doesn't add the working directory to sys.path,
# which breaks tasks.py's deferred `from services...` imports. See CLAUDE.md.
exec uv run python -m celery -A tasks.celery_app worker --loglevel=info
