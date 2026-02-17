"""
Smart # car status reader via Hello Smart API.

Based on evcc-io/evcc vehicle/smart/hello/ (Go) and TA2k/ioBroker.smart-eq.
Provides HelloSmartClient for authentication and vehicle status queries,
including SOC (state of charge) readback.

Usage (as module):
    from src.smart_car import HelloSmartClient, get_soc

    client = HelloSmartClient(user, password)
    client.authenticate()
    soc = get_soc(client, vin)
"""

import base64
import hashlib
import hmac
import json
import logging
import random
import string
import time
from urllib.parse import parse_qs, urlencode, urlparse

import requests

logger = logging.getLogger(__name__)

# --- Constants (from evcc const.go) ---

API_URI = "https://api.ecloudeu.com"
API_URI_V2 = "https://apiv2.ecloudeu.com"  # Smart #5 (HY series)
API_KEY = "3_L94eyQ-wvJhWm7Afp1oBhfTGXZArUfSHHW9p9Pncg513hZELXsxCfMWHrF8f5P5a"
APP_ID = "SmartAPPEU"
OPERATOR_CODE = "SMART"
HMAC_SECRET_B64 = "NzRlNzQ2OWFmZjUwNDJiYmJlZDdiYmIxYjM2YzE1ZTk="
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 9; ANE-LX1 Build/HUAWEIANE-L21; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/118.0.0.0 "
    "Mobile Safari/537.36"
)
COOKIE = (
    "gmid=gmid.ver4.AcbHPqUK5Q.xOaWPhRTb7gy-6-GUW6cxQVf_t7LhbmeabBNXqqqsT6d"
    "pLJLOWCGWZM07EkmfM4j.u2AMsCQ9ZsKc6ugOIoVwCgryB2KJNCnbBrlY6pq0W2Ww7sxSkUa"
    "9_WTPBIwAufhCQYkb7gA2eUbb6EIZjrl5mQ.sc3; ucid=hPzasmkDyTeHN0DinLRGvw; "
    "hasGmid=ver4; gig_bootstrap_" + API_KEY + "=auth_ver4"
)


# --- Signing (from evcc helper.go) ---


def _random_string(length: int) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


def create_signature(
    method: str, path: str, params: dict, body: bytes | None = None
) -> tuple[str, str, str]:
    """Create HMAC-SHA1 signature for API request.

    Returns (nonce, timestamp, signature).
    """
    nonce = _random_string(16)
    ts = str(int(time.time() * 1000))

    if body:
        md5_hash = base64.b64encode(hashlib.md5(body).digest()).decode()
    else:
        md5_hash = "1B2M2Y8AsgTpgAmY7PhCfg=="

    params_str = urlencode(params, doseq=True)

    payload = (
        f"application/json;responseformat=3\n"
        f"x-api-signature-nonce:{nonce}\n"
        f"x-api-signature-version:1.0\n"
        f"\n"
        f"{params_str}\n"
        f"{md5_hash}\n"
        f"{ts}\n"
        f"{method}\n"
        f"{path}"
    )

    secret = base64.b64decode(HMAC_SECRET_B64)
    mac = hmac.new(secret, payload.encode(), hashlib.sha1)
    signature = base64.b64encode(mac.digest()).decode()

    return nonce, ts, signature


# --- Client ---


class HelloSmartClient:
    """Client for the Hello Smart API (Smart #1, #3, #5)."""

    def __init__(self, user: str, password: str, quiet: bool = True):
        self.user = user
        self.password = password
        self.quiet = quiet
        self.device_id = _random_string(16)
        self.access_token: str | None = None
        self.user_id: str | None = None
        self.session = requests.Session()
        self.vehicle_api: dict[str, str] = {}  # VIN → API base URL

    def _log(self, msg: str):
        if not self.quiet:
            logger.info(msg)

    def authenticate(self):
        """Run the full 4-step authentication flow."""
        self._log("Authenticating with Hello Smart...")

        # Steps 1-3: Login → OAuth token
        oauth_token = self._login()

        # Step 4: Exchange for app token
        self._app_token(oauth_token)

        self._log(f"Authenticated as user {self.user_id}")

    def _login(self) -> str:
        """Steps 1-3: Gigya login → OIDC token exchange. Returns OAuth access_token."""

        # Step 1: Get authorization context
        self._log("Step 1: Getting auth context...")
        resp = self.session.get(
            "https://awsapi.future.smart.com/login-app/api/v1/authorize",
            params={"uiLocales": "de-DE"},
            headers={
                "user-agent": USER_AGENT,
                "x-requested-with": "com.smart.hellosmart",
            },
            allow_redirects=True,
        )
        resp.raise_for_status()

        context = parse_qs(urlparse(resp.url).query).get("context", [None])[0]
        if not context:
            raise RuntimeError(f"Missing context in redirect URL: {resp.url}")

        # Step 2: Gigya accounts.login
        self._log("Step 2: Logging in via Gigya...")
        resp = self.session.post(
            "https://auth.smart.com/accounts.login",
            data={
                "loginID": self.user,
                "password": self.password,
                "sessionExpiration": "2592000",
                "targetEnv": "jssdk",
                "include": "profile,data,emails,subscriptions,preferences",
                "includeUserInfo": "true",
                "loginMode": "standard",
                "lang": "de",
                "APIKey": API_KEY,
                "source": "showScreenSet",
                "sdk": "js_latest",
                "authMode": "cookie",
                "pageURL": "https://app.id.smart.com/login?gig_ui_locales=de-DE",
                "sdkBuild": "15482",
                "format": "json",
            },
            headers={
                "user-agent": USER_AGENT,
                "x-requested-with": "com.smart.hellosmart",
                "cookie": COOKIE,
            },
        )
        resp.raise_for_status()
        login_data = resp.json()

        if login_data.get("errorCode", -1) != 0:
            raise RuntimeError(
                f"Login failed: {login_data.get('errorMessage', 'unknown')} "
                f"- {login_data.get('errorDetails', '')}"
            )

        login_token = login_data["sessionInfo"]["login_token"]

        # Step 3: OIDC token exchange
        self._log("Step 3: Exchanging OIDC token...")
        cookie_with_token = COOKIE + f";glt_{API_KEY}={login_token}"

        resp = self.session.get(
            f"https://auth.smart.com/oidc/op/v1.0/{API_KEY}/authorize/continue",
            params={"context": context, "login_token": login_token},
            headers={
                "user-agent": USER_AGENT,
                "x-requested-with": "com.smart.hellosmart",
                "cookie": cookie_with_token,
            },
            allow_redirects=False,
        )

        # Follow redirect chain to find token or authorization code
        location = resp.headers.get("Location", "")
        while resp.is_redirect and location:
            parsed = urlparse(location)
            params = parse_qs(parsed.fragment) or parse_qs(parsed.query)

            if "access_token" in params:
                return params["access_token"][0]

            if "code" in params:
                resp = self.session.get(
                    location,
                    headers={
                        "user-agent": USER_AGENT,
                        "x-requested-with": "com.smart.hellosmart",
                    },
                    allow_redirects=False,
                )
                location = resp.headers.get("Location", "")
                continue

            resp = self.session.get(
                location,
                headers={
                    "user-agent": USER_AGENT,
                    "x-requested-with": "com.smart.hellosmart",
                },
                allow_redirects=False,
            )
            location = resp.headers.get("Location", "")

        # Check final location
        if location:
            parsed = urlparse(location)
            params = parse_qs(parsed.fragment) or parse_qs(parsed.query)
            if "access_token" in params:
                return params["access_token"][0]
            if "code" in params:
                return self._exchange_code(params["code"][0], location)

        raise RuntimeError(
            f"No access_token or code in redirect chain: {location[:200]}"
        )

    def _exchange_code(self, code: str, redirect_url: str) -> str:
        """Exchange authorization code for access token at OIDC token endpoint."""
        parsed = urlparse(redirect_url)
        redirect_uri = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

        token_url = f"https://auth.smart.com/oidc/op/v1.0/{API_KEY}/token"

        resp = self.session.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": API_KEY,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "user-agent": USER_AGENT,
                "x-requested-with": "com.smart.hellosmart",
                "cookie": COOKIE,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        access_token = data.get("access_token")
        if not access_token:
            raise RuntimeError(f"Token exchange failed: {data}")

        return access_token

    def _app_token(self, oauth_token: str):
        """Step 4: Exchange OAuth token for app token."""
        self._log("Step 4: Exchanging for app token...")

        path = "/auth/account/session/secure"
        params = {"identity_type": "smart"}
        body = json.dumps({"accessToken": oauth_token}).encode()

        nonce, ts, sign = create_signature("POST", path, params, body)

        resp = self.session.post(
            f"{API_URI}{path}",
            params=params,
            data=body,
            headers={
                "Accept": "application/json;responseformat=3",
                "Content-Type": "application/json; charset=utf-8",
                "X-Api-Signature-Version": "1.0",
                "X-Api-Signature-Nonce": nonce,
                "X-App-Id": APP_ID,
                "X-Device-Identifier": self.device_id,
                "X-Operator-Code": OPERATOR_CODE,
                "X-Signature": sign,
                "X-Timestamp": ts,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        if str(data.get("code")) != "1000":
            raise RuntimeError(f"App token failed: {data.get('message', data)}")

        self.access_token = data["data"]["accessToken"]
        self.user_id = data["data"]["userId"]

    def _signed_request(
        self, method: str, path: str, params: dict,
        body: bytes | None = None, base_url: str = API_URI,
    ) -> dict:
        """Make a signed API request."""
        nonce, ts, sign = create_signature(method, path, params, body)

        headers = {
            "Accept": "application/json;responseformat=3",
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": self.access_token,
            "X-Operator-Code": OPERATOR_CODE,
            "X-Api-Signature-Version": "1.0",
            "X-App-Id": APP_ID,
            "X-Device-Identifier": self.device_id,
            "X-Api-Signature-Nonce": nonce,
            "X-Signature": sign,
            "X-Timestamp": ts,
        }

        resp = self.session.request(
            method,
            f"{base_url}{path}",
            params=params,
            data=body,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()

    def list_vehicles(self) -> list[str]:
        """List vehicles from the account. Detects API version per model."""
        data = self._signed_request(
            "GET",
            "/device-platform/user/vehicle/secure",
            {"needSharedCar": "1", "userId": self.user_id},
        )
        if str(data.get("code")) != "1000":
            raise RuntimeError(f"List vehicles failed: {data.get('message', data)}")
        vehicles = data["data"]["list"]
        vins = []
        for v in vehicles:
            vin = v.get("vin", "?")
            series = v.get("seriesCodeVs", v.get("modelName", ""))
            # Smart #5 (HY) uses V2 API, #1 (HX) and #3 (HC) use V1
            if series.startswith("HY"):
                self.vehicle_api[vin] = API_URI_V2
            else:
                self.vehicle_api[vin] = API_URI
            logger.debug(f"Found vehicle: {vin} — {series}")
            vins.append(vin)
        return vins

    def _api_for(self, vin: str) -> str:
        """Return the correct API base URL for this VIN."""
        return self.vehicle_api.get(vin, API_URI)

    def update_session(self, vin: str):
        """Update session for VIN (required before status query)."""
        body = json.dumps({
            "vin": vin,
            "sessionToken": self.access_token,
            "language": "",
        }).encode()

        data = self._signed_request(
            "POST",
            "/device-platform/user/session/update",
            {"identity_type": "smart"},
            body,
            base_url=self._api_for(vin),
        )
        if str(data.get("code")) != "1000":
            raise RuntimeError(f"Update session failed: {data.get('message', data)}")

    def get_status(self, vin: str) -> dict:
        """Get full vehicle status (tries update_session first).

        Returns the full data envelope (contains vehicleStatus).
        """
        try:
            self.update_session(vin)
        except RuntimeError as e:
            logger.warning(f"Session update failed: {e} — trying status anyway...")

        data = self._signed_request(
            "GET",
            f"/remote-control/vehicle/status/{vin}",
            {"latest": "true", "target": "", "userId": self.user_id},
            base_url=self._api_for(vin),
        )
        if str(data.get("code")) != "1000":
            raise RuntimeError(f"Get status failed: {data.get('message', data)}")

        return data["data"]


def get_soc(client: HelloSmartClient, vin: str) -> int:
    """Get current battery SOC from vehicle.

    Args:
        client: Authenticated HelloSmartClient instance
        vin: Vehicle identification number

    Returns:
        SOC as integer percentage.

    Raises:
        RuntimeError: If status query fails
        KeyError: If SOC field is missing from response
    """
    data = client.get_status(vin)
    logger.debug(f"Smart car API response keys: {sorted(data.keys())}")
    if "vehicleStatus" in data:
        vs = data["vehicleStatus"]
        logger.debug(f"vehicleStatus keys: {sorted(vs.keys())}")
        avs = vs.get("additionalVehicleStatus", {})
        evs = avs.get("electricVehicleStatus", {})
        logger.debug(f"electricVehicleStatus: {evs}")
    vehicle_status = data.get("vehicleStatus", data)
    ev = vehicle_status.get("additionalVehicleStatus", {}).get("electricVehicleStatus", {})
    soc = ev.get("chargeLevel")
    if soc is None:
        raise KeyError("chargeLevel not found in vehicle status")

    return int(soc)
