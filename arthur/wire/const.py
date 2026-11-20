"""Endpoints, ids and timings. Each one is cited in ``docs/reference/whisker-wire.md``."""

#: Who we say we are. Ours, not pylitterbot's. Whether Whisker accepts it is unknown until the
#: supervised live step (Whisker blocked a default User-Agent in April 2026).
USER_AGENT = "ProjectArthur/0.1 (+https://github.com/mcgaheyk/ProjectArthur)"

#: Amazon Cognito user pool and app client (pylitterbot ``session.py``,
#: ``LitterRobotSession.USER_POOL_ID`` / ``CLIENT_ID``, base64-decoded).
USER_POOL_ID = "us-east-1_rjhNnZVAm"
CLIENT_ID = "4552ujeu3aic90nf8qn53levmn"
COGNITO_URL = "https://cognito-idp.us-east-1.amazonaws.com/"

#: The Litter-Robot 4 GraphQL endpoint and its realtime WebSocket
#: (pylitterbot ``robot/litterrobot4.py``, ``LR4_ENDPOINT`` and ``_ws_config_factory``).
LR4_URL = "https://lr4.iothings.site/graphql"
LR4_HOST = "lr4.iothings.site"

#: The CA bundle on the clock (``docs/reference/awtrix-ng.md``).
CLOCK_CA_FILE = "/tmp/awtrix-release/share/ca-certificates.crt"

#: Renew the id token this long before it expires (pylitterbot ``is_token_valid``,
#: ``jwt.decode(..., leeway=-30)``).
TOKEN_LEEWAY_S = 30

#: The live connection: back-off from 1 s doubling to 300 s, and a socket with no message for
#: 180 s is closed and opened again (pylitterbot ``transport.py``).
WS_BACKOFF_START = 1.0
WS_BACKOFF_MAX = 300.0
WS_STALE_S = 180.0

#: Our own politeness (arthur ``spikelog.py`` and ``sources/whisker.py``).
POLL_S = 300.0
MIN_POLL_S = 120.0
API_BACKOFF_START = 30.0
API_BACKOFF_MAX = 600.0
AUTH_BACKOFF_START = 600.0
AUTH_BACKOFF_MAX = 3600.0
HEARTBEAT_S = 15 * 60.0

#: A request that has not finished in this long is a ``timeout`` (pylitterbot has none of its
#: own; aiohttp's default total is 300 s, far too long to keep a screen honest).
REQUEST_TIMEOUT_S = 30.0

#: Activity history is fetched in pages this big, so one page parses in well under 100 KB on
#: MicroPython (measured: see the reference note).
HISTORY_PAGE = 25
