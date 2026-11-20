"""Amazon Cognito: renewing tokens (``REFRESH_TOKEN_AUTH``) and the SRP password login.

What pycognito sends through boto3, which we send ourselves: a POST to
``https://cognito-idp.us-east-1.amazonaws.com/`` with ``Content-Type:
application/x-amz-json-1.1`` and ``X-Amz-Target: AWSCognitoIdentityProviderService.<Op>``.
Both operations are unsigned (``"auth": ["smithy.api#noAuth"]`` in botocore's model), so no
AWS credentials are involved. Errors come back as HTTP 400 with ``{"__type": code,
"message": text}`` and the code in the ``x-amzn-ErrorType`` header.
"""

import json

from arthur.wire import errors, http, srp, util
from arthur.wire.const import CLIENT_ID, COGNITO_URL, REQUEST_TIMEOUT_S, USER_POOL_ID

#: Codes that mean the login or the refresh token was refused (``arthur.spikelog``'s list).
AUTH_CODES = ("NotAuthorizedException", "UserNotFoundException",
              "UserNotConfirmedException", "PasswordResetRequiredException")


def _error_code(resp, body):
    code = resp.headers.get("x-amzn-errortype", "")
    if not code and isinstance(body, dict):
        code = str(body.get("__type", ""))
    code = code.split(":", 1)[0]
    return code.rsplit("#", 1)[-1]


async def call(op, payload, tls, url=COGNITO_URL, timeout=REQUEST_TIMEOUT_S):
    """One Cognito JSON call. Returns the answer dict; raises ``auth`` for a refused login or
    token, ``api`` for anything else."""
    resp = await http.request(
        "POST", url, tls,
        headers={"Content-Type": "application/x-amz-json-1.1",
                 "X-Amz-Target": "AWSCognitoIdentityProviderService." + op},
        body=json.dumps(payload), timeout=timeout)
    if resp.status == 200:
        body = resp.json()
        if not isinstance(body, dict):
            raise errors.api_error("Cognito " + op + " answered something not an object")
        return body
    try:
        body = json.loads(resp.body)
    except ValueError:
        body = None
    code = _error_code(resp, body)
    message = body.get("message", "") if isinstance(body, dict) else ""
    text = f"Cognito {op}: HTTP {resp.status} {code} {message}"
    if code in AUTH_CODES:
        raise errors.auth_error(text.strip(), resp.status, code)
    raise errors.api_error(text.strip(), resp.status, code)


def _tokens(result, old_refresh=None):
    try:
        tokens = {
            "access_token": result["AccessToken"],
            "id_token": result["IdToken"],
            "refresh_token": result.get("RefreshToken") or old_refresh,
        }
    except (KeyError, TypeError):
        raise errors.api_error("Cognito answer lacks tokens") from None
    util.jwt_claims(tokens["id_token"])  # must decode
    return tokens


async def refresh(refresh_token, tls, url=COGNITO_URL, timeout=REQUEST_TIMEOUT_S):
    """``Cognito.renew_access_token``: new access and id tokens; the refresh token is kept
    (Cognito returns no new one unless rotation is on, and pycognito keeps the old one)."""
    answer = await call("InitiateAuth", {
        "AuthFlow": "REFRESH_TOKEN_AUTH",
        "AuthParameters": {"REFRESH_TOKEN": refresh_token},
        "ClientId": CLIENT_ID,
    }, tls, url, timeout)
    return _tokens(answer.get("AuthenticationResult"), refresh_token)


async def login(username, password, tls, url=COGNITO_URL, timeout=REQUEST_TIMEOUT_S,
                now_fn=util.now, window=16, pool_id=USER_POOL_ID):
    """``AWSSRP.authenticate_user``: USER_SRP_AUTH, then PASSWORD_VERIFIER. Any further
    challenge (a new password, MFA, a device) is an ``auth`` error: a person has to act."""
    client = srp.SrpClient(pool_id)
    srp_a = await client.start(window)
    answer = await call("InitiateAuth", {
        "AuthFlow": "USER_SRP_AUTH",
        "AuthParameters": {"USERNAME": username, "SRP_A": srp_a},
        "ClientId": CLIENT_ID,
    }, tls, url, timeout)
    challenge = answer.get("ChallengeName")
    if challenge != "PASSWORD_VERIFIER":
        raise errors.auth_error("Cognito asked for the " + str(challenge)
                                + " challenge, which this client does not answer")
    try:
        responses = await client.respond(answer["ChallengeParameters"], username, password,
                                         now_fn(), window)
    except (KeyError, TypeError, ValueError) as exc:
        raise errors.api_error("bad PASSWORD_VERIFIER challenge: " + type(exc).__name__) \
            from None
    answer = await call("RespondToAuthChallenge", {
        "ChallengeName": "PASSWORD_VERIFIER",
        "ClientId": CLIENT_ID,
        "ChallengeResponses": responses,
    }, tls, url, timeout)
    if answer.get("ChallengeName"):
        raise errors.auth_error("Cognito asked for the " + str(answer["ChallengeName"])
                                + " challenge, which this client does not answer")
    return _tokens(answer.get("AuthenticationResult"))
