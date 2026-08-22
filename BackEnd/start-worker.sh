#!/bin/sh
set -e
# Must be invoked as `python -m celery`, not bare `celery` -- Celery's
# console-script entry point doesn't add the working directory to sys.path,
# which breaks tasks.py's deferred `from services...` imports. See CLAUDE.md.
#
# --concurrency=1 is required here: with no explicit value, Celery's prefork
# pool defaults to os.cpu_count(), which on Render's containers reports the
# host machine's core count rather than the ~0.5 vCPU actually allocated to
# the Starter worker instance. Each forked child independently imports
# pandas/numpy (~250MB+) the first time it runs any task, and that baseline
# persists for the child's lifetime -- with more than one child alive at
# once, cumulative RSS exceeds the instance's 512MB cap and Render kills the
# whole instance ("Ran out of memory (used over 512MB)"). Keep the prefork
# pool (not --pool=solo) since celery_app.py's task_time_limit/
# task_soft_time_limit enforcement requires it.
# --without-gossip/--without-mingle/--without-heartbeat disable Celery's
# multi-worker coordination features (peer discovery, cross-worker
# heartbeats) that this single-worker deployment has no other workers to
# coordinate with -- left on, they periodically publish/subscribe over Redis
# purely to announce/discover peers, which on a metered broker (e.g.
# Upstash) is billed as commands for zero functional benefit here.
exec uv run python -m celery -A tasks.celery_app worker --loglevel=info --concurrency=1 \
    --without-gossip --without-mingle --without-heartbeat
