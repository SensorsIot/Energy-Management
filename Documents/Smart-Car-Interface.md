# Smart # Car Interface — Hello Smart API

**Purpose:** Read EV state (SOC, charging status, range) from Smart # cars (Smart #1, #3, #5) via the Hello Smart app backend API.

**Reference implementations:**
- [evcc-io/evcc](https://github.com/evcc-io/evcc) `vehicle/smart/hello/` — Go, implicit OIDC flow only, V1 API only (does **not** support Smart #5)
- [DasBasti/pySmartHashtag](https://github.com/DasBasti/pySmartHashtag) — Python, V1+V2 API routing by model
- Original reverse-engineering: [TA2k/ioBroker.smart-eq](https://github.com/TA2k/ioBroker.smart-eq)

**Verified working:** 2026-02-12, Smart #5 Brabus AWD (VIN HESYA4C44SG200806), SOC 76%.

---

## 1. Authentication Flow (4 steps)

### Step 1: Get Authorization Context

```
GET https://awsapi.future.smart.com/login-app/api/v1/authorize?uiLocales=de-DE
Headers:
  user-agent: Mozilla/5.0 (Linux; Android 9; ANE-LX1 ...)
  x-requested-with: com.smart.hellosmart
```

Returns a redirect. Extract the `context` parameter from the redirect URL's query string.

### Step 2: Gigya Login

```
POST https://auth.smart.com/accounts.login
Content-Type: application/x-www-form-urlencoded
Headers:
  user-agent: Mozilla/5.0 (Linux; Android 9; ANE-LX1 ...)
  x-requested-with: com.smart.hellosmart

Form data:
  loginID=<email>
  password=<password>
  sessionExpiration=2592000
  targetEnv=jssdk
  include=profile,data,emails,subscriptions,preferences
  includeUserInfo=true
  loginMode=standard
  lang=de
  APIKey=3_L94eyQ-wvJhWm7Afp1oBhfTGXZArUfSHHW9p9Pncg513hZELXsxCfMWHrF8f5P5a
  source=showScreenSet
  sdk=js_latest
  authMode=cookie
  pageURL=https://app.id.smart.com/login?gig_ui_locales=de-DE
  sdkBuild=15482
  format=json
```

Response contains `sessionInfo.login_token`.

### Step 3: OIDC Token Exchange (Authorization Code Flow)

```
GET https://auth.smart.com/oidc/op/v1.0/<ApiKey>/authorize/continue?context=<CONTEXT>&login_token=<LOGIN_TOKEN>
Headers:
  user-agent: Mozilla/5.0 (Linux; Android 9; ANE-LX1 ...)
  x-requested-with: com.smart.hellosmart
  cookie: ...; glt_<ApiKey>=<LOGIN_TOKEN>
Allow-Redirects: false
```

Returns a **302 redirect chain** (two hops):

1. **Hop 1:** `→ https://awsapi.future.smart.com/login-app/api/v1/FinalPage?code=<AUTH_CODE>`
2. **Hop 2:** `→ /login-app/api/v1/MobileData?access_token=<ACCESS_TOKEN>`

Follow each redirect manually (disable auto-redirects). Extract `access_token` from the second hop's Location header query parameters.

> **Note:** evcc and older documentation describe an implicit flow where `access_token` appears directly in the first redirect. As of 2026, Smart's OIDC server uses authorization code flow instead. The `FinalPage` endpoint on Smart's server performs the code-for-token exchange server-side and redirects to `MobileData` with the token. No client-side token endpoint call is needed.

### Step 4: App Token Exchange

```
POST https://api.ecloudeu.com/auth/account/session/secure?identity_type=smart
Headers:
  Accept: application/json;responseformat=3
  Content-Type: application/json; charset=utf-8
  X-App-Id: SmartAPPEU
  X-Operator-Code: SMART
  (+ signature headers, see Section 3)

Body:
{
  "accessToken": "<OAuth access_token from step 3>"
}
```

Response (`code: "1000"` = success):
```json
{
  "data": {
    "accessToken": "<APP_ACCESS_TOKEN>",
    "refreshToken": "<APP_REFRESH_TOKEN>",
    "expiresIn": 86400,
    "userId": "<USER_ID>"
  }
}
```

The `accessToken` and `userId` are used for all subsequent API calls.

---

## 2. Reading Vehicle Data

### 2.1 API Version Routing (V1 vs V2)

The Smart API has two base URLs. The correct one depends on the car model:

| Model | Series Prefix | API Base URL |
|-------|---------------|-------------|
| Smart #1 | `HX` | `https://api.ecloudeu.com` (V1) |
| Smart #3 | `HC` | `https://api.ecloudeu.com` (V1) |
| Smart #5 | `HY` | `https://apiv2.ecloudeu.com` (V2) |

**Detection:** The vehicle list response (Section 2.2) includes a `seriesCodeVs` field. Check its prefix to select the API base URL.

**Which calls use which URL:**
- **Always V1:** Authentication (Step 4), vehicle list — these are account-level, model-agnostic
- **V1 or V2 per model:** Session update, vehicle status — these are vehicle-specific

> **Critical:** Using V1 for a Smart #5 returns error 8063 "No vehicle information for this VIN". This is the root cause of evcc's broken Smart #5 support ([evcc #22978](https://github.com/evcc-io/evcc/issues/22978)) — evcc hardcodes V1 for all models.

### 2.2 List Vehicles

```
GET https://api.ecloudeu.com/device-platform/user/vehicle/secure?needSharedCar=1&userId=<USER_ID>
```

Returns `data.list[]` with fields including `vin` and `seriesCodeVs` (used for API version routing).

### 2.3 Update Session (required before every status call)

```
POST <API_BASE>/device-platform/user/session/update?identity_type=smart

Body:
{
  "vin": "<VIN>",
  "sessionToken": "<accessToken>",
  "language": ""
}
```

Where `<API_BASE>` is `api.ecloudeu.com` or `apiv2.ecloudeu.com` depending on model (see 2.1).

### 2.4 Get Vehicle Status

```
GET <API_BASE>/remote-control/vehicle/status/<VIN>?latest=true&target=&userId=<USER_ID>
```

### 2.5 Available Fields

**Electric Vehicle Status** (`additionalVehicleStatus.electricVehicleStatus`):

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| `chargeLevel` | int | % | **SOC — the key field** |
| `distanceToEmptyOnBatteryOnly` | int | km | Remaining range |
| `statusOfChargerConnection` | int | - | 0=disconnected, 1/3=connected, 2=charging |
| `chargeSts` | int | - | AC charge status |
| `dcChargeSts` | int | - | DC charge status |
| `chargeIAct` | float | A | AC charging current |
| `chargeUAct` | float | V | AC charging voltage |
| `dcChargeIAct` | float | A | DC charging current |
| `timeToFullyCharged` | int | min | Time to full charge |
| `chargeLidAcStatus` | int | - | AC charge lid status |
| `chargeLidDcAcStatus` | int | - | DC charge lid status |
| `distanceToEmptyOnBattery100Soc` | int | km | Range at 100% SOC |
| `distanceToEmptyOnBattery20Soc` | int | km | Range at 20% SOC |
| `averPowerConsumption` | float | - | Average power consumption |

**Maintenance** (`additionalVehicleStatus.maintenanceStatus`):

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| `odometer` | float | km | Odometer reading |

**12V Battery** (`additionalVehicleStatus.maintenanceStatus.mainBatteryStatus`):

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| `chargeLevel` | float | % | 12V battery charge |
| `voltage` | float | V | 12V battery voltage |
| `stateOfHealth` | int | - | 12V battery health |

**Position** (`basicVehicleStatus.position`):

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| `latitude` | int | ÷3,600,000 → degrees | GPS latitude |
| `longitude` | int | ÷3,600,000 → degrees | GPS longitude |

**Climate** (`additionalVehicleStatus.climateStatus`):

| Field | Type | Description |
|-------|------|-------------|
| `preClimateActive` | bool | Pre-conditioning active |
| `defrost` | bool | Defrost active |

---

## 3. Request Signing (HMAC-SHA1)

All requests to `api.ecloudeu.com` and `apiv2.ecloudeu.com` require signed headers (same scheme for both):

| Header | Value |
|--------|-------|
| `Authorization` | `<APP_ACCESS_TOKEN>` |
| `X-App-Id` | `SmartAPPEU` |
| `X-Operator-Code` | `SMART` |
| `X-Api-Signature-Version` | `1.0` |
| `X-Api-Signature-Nonce` | random 16-char alphanumeric |
| `X-Timestamp` | Unix milliseconds |
| `X-Device-Identifier` | random 16-char alphanumeric (persisted per session) |
| `X-Signature` | HMAC-SHA1 signature (see below) |

### Signing Algorithm

1. Generate random 16-char alphanumeric **nonce**
2. Get current time as **Unix milliseconds**
3. Compute **MD5** of request body, Base64-encoded (empty body: `1B2M2Y8AsgTpgAmY7PhCfg==`)
4. Build signing payload:
   ```
   application/json;responseformat=3
   x-api-signature-nonce:<nonce>
   x-api-signature-version:1.0

   <query_params>
   <md5_hash>
   <timestamp>
   <HTTP_METHOD>
   <path>
   ```
5. Sign with **HMAC-SHA1** using the secret key (Base64-decode first): `NzRlNzQ2OWFmZjUwNDJiYmJlZDdiYmIxYjM2YzE1ZTk=`
   - Decoded: `74e7469aff5042bbbe7bbb1b36c15e9`
6. Base64-encode the HMAC result → `X-Signature`

---

## 4. Constants

| Constant | Value |
|----------|-------|
| API base URL (V1) | `https://api.ecloudeu.com` (Smart #1, #3) |
| API base URL (V2) | `https://apiv2.ecloudeu.com` (Smart #5) |
| Gigya API key | `3_L94eyQ-wvJhWm7Afp1oBhfTGXZArUfSHHW9p9Pncg513hZELXsxCfMWHrF8f5P5a` |
| App ID | `SmartAPPEU` |
| Operator code | `SMART` |
| HMAC secret (Base64) | `NzRlNzQ2OWFmZjUwNDJiYmJlZDdiYmIxYjM2YzE1ZTk=` |
| Auth authorize URL | `https://awsapi.future.smart.com/login-app/api/v1/authorize` |
| Auth login URL | `https://auth.smart.com/accounts.login` |
| Success response code | `1000` |

---

## 5. Source References

### evcc (Go) — V1 only, broken for Smart #5

All in [evcc-io/evcc](https://github.com/evcc-io/evcc) repository:

| File | Purpose |
|------|---------|
| `vehicle/smart/hello/identity.go` | Authentication flow (implicit OIDC only — broken as of 2026) |
| `vehicle/smart/hello/api.go` | API client (V1 only — no Smart #5 support) |
| `vehicle/smart/hello/helper.go` | HMAC-SHA1 request signing |
| `vehicle/smart/hello/provider.go` | Data extraction (SOC, range, charging status) |
| `vehicle/smart/hello/types.go` | Response data structures |
| `vehicle/smart/hello/const.go` | API URLs and keys (V1 only) |

**Known issues:** [#22978](https://github.com/evcc-io/evcc/issues/22978) (Smart #5 not supported), implicit OIDC flow broken (returns `code` not `access_token`).

### pySmartHashtag (Python) — V1+V2, model routing

[DasBasti/pySmartHashtag](https://github.com/DasBasti/pySmartHashtag) — has V1/V2 routing via `seriesCodeVs` prefix detection. Used by the SmartHashtag Home Assistant integration.

### Our implementation

`scripts/smart_car_status.py` — standalone Python script combining:
- Authorization code flow (Step 3 two-hop redirect, not implicit)
- V1/V2 API routing by model series
- HMAC-SHA1 signing from evcc's `helper.go`

---

## 6. Rate Limit Discovery

The Gigya/SAP CDC API enforces rate limits (error code `403048`) but does not publish the cooldown duration. The `scripts/smart_rate_limit_probe.py` tool discovers this empirically.

### Algorithm

1. **Confirm credentials** — attempt login; if successful, credentials are valid. If already rate-limited, credentials are confirmed on first later success.
2. **Trigger rate limit** — fire rapid login attempts until `403048` is returned.
3. **Find upper bound** — wait `interval` (starting at 1 hour), attempt login. If still rate-limited, double the interval (2h, 4h, 8h, ...) and repeat until the first successful login. This establishes the upper bound.
4. **Binary search** — narrow the cooldown window between last-known-failure and first-known-success. Each iteration re-triggers the rate limit, waits the midpoint duration, and tests. Converges to ±30s precision.

### Output

Logs each probe with timestamps and reports the cooldown window:

```
[14:32:10] RESULT: Rate limit cooldown is 240s – 270s
           (4.0 min – 4.5 min)
           Uncertainty: ±15s
```

### Usage

```bash
export SMART_USER="your@email.com"
export SMART_PASSWORD="yourpassword"
python3 scripts/smart_rate_limit_probe.py
```

### Known Constraints

- Gigya rate limits are per-API-key and may vary by contract tier
- SAP does not publish exact thresholds (see SAP KBA 2702625)
- evcc handles rate limits only via generic exponential backoff on cache refresh failures (`5^n` seconds), with no Gigya-specific detection
