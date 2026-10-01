"""Shared test setup.

server.py reads its configuration from the environment at import time (and exits
if GITLAB_TOKEN / GROQ_API_KEY are missing — fail-fast in production). Tests don't
talk to the network, so we pin throwaway values and CLEAR every other setting
*before* server is imported anywhere: a GITLAB_PROJECT_ID or RATE_LIMIT exported
in the developer's shell must not change what the tests see. We also make the
chatbox-cloud directory importable regardless of the current working directory.
"""

import os
import sys

os.environ["GITLAB_TOKEN"] = "test-token"
os.environ["GROQ_API_KEY"] = "test-key"
for _name in ("GITLAB_PROJECT_ID", "GITLAB_BASE", "GITLAB_PAGE_LIMIT", "CACHE_TTL",
              "GROQ_MODEL", "GROQ_REASONING_EFFORT", "GROQ_URL", "RATE_LIMIT",
              "WRITE_RATE_LIMIT", "READ_RATE_LIMIT", "MAX_CONCURRENT_PER_IP",
              "APP_ACCESS_KEY", "README_MAX", "PORT",
              "PUBLIC_URL", "TRUSTED_PROXY_HOPS", "MAX_BODY_BYTES",
              "MAX_CONNECTIONS", "REQUEST_TIMEOUT", "GITLAB_FANOUT_TIMEOUT",
              "GITLAB_ALLOW_PRIVATE", "LOG_LEVEL", "REQUEST_READ_TIMEOUT",
              "GITLAB_WORKERS", "GITLAB_WORKERS_PER_CLIENT"):
    os.environ.pop(_name, None)

# tests/ lives inside chatbox-cloud/ — put the parent on the path so `import server`
# works whether pytest is run from the repo root or from tests/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
