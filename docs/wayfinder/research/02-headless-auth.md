# Research 02: allauth headless + Ninja auth for an SSR TanStack Start frontend

Facts only; no design decisions. Researched 2026-11-09 against `main`/`latest` docs and source. Items marked **[unverified]** could not be confirmed from a primary source in this pass.

## 1. allauth headless: setup and client types

- Headless is provided by the `allauth.headless` app; `allauth.account` must still be installed, and its views can be disabled with `HEADLESS_ONLY = True`. Install via `pip install "django-allauth[headless]"`. INSTALLED_APPS needs `allauth`, `allauth.account`, `allauth.headless`. Mount with `path("_allauth/", include("allauth.headless.urls"))`; `path("accounts/", include("allauth.urls"))` stays (needed for provider callbacks). [Installation](https://docs.allauth.org/en/latest/headless/installation.html), [Introduction](https://docs.allauth.org/en/latest/headless/introduction.html)
- Settings ([Configuration](https://docs.allauth.org/en/latest/headless/configuration.html)):
  - `HEADLESS_CLIENTS` default `("app", "browser")`; `("app",)` removes browser endpoints.
  - `HEADLESS_ONLY` default `False`.
  - `HEADLESS_FRONTEND_URLS` default `{}`; maps view names (`account_confirm_email`, `account_reset_password_from_key`, `account_signup`) to frontend URLs, `{key}` placeholder filled in; emails then link to the SPA.
  - `HEADLESS_TOKEN_STRATEGY` default `allauth.headless.tokens.strategies.sessions.SessionTokenStrategy`.
  - `HEADLESS_SERVE_SPECIFICATION` default `False`; serves `/_allauth/openapi.{yaml,json,html}` (needs `django-allauth[headless-spec]`).
- URL layout: `/_allauth/browser/v1/...` and `/_allauth/app/v1/...` are mounted independently per enabled client; same feature modules in both; `tokens/` exists only under `app/`. [urls.py](https://github.com/pennersr/django-allauth/blob/main/allauth/headless/urls.py)
- Browser vs app, from source ([decorators.py](https://github.com/pennersr/django-allauth/blob/main/allauth/headless/internal/decorators.py), [authkit.py](https://github.com/pennersr/django-allauth/blob/main/allauth/headless/internal/authkit.py)):
  - `browser_view` tags request `Client.BROWSER`, calls `get_token(request)` so the CSRF cookie is set; NOT csrf-exempt. Uses Django's normal session cookie.
  - `app_view` tags request `Client.APP`, is wrapped in `csrf_exempt`, and runs inside `authkit.authentication_context(request)`, which swaps in the session identified by the session token; unknown token raises HTTP 410. The access/session token is only exposed in responses for the app client.
- OpenAPI param descriptions: Client = "The type of client accessing the API."; SessionToken = "Session token. Only needed when `client` is equal to `app`." [openapi.yaml](https://github.com/pennersr/django-allauth/blob/main/allauth/headless/spec/doc/openapi.yaml)

## 2. Session vs token auth

- Default `SessionTokenStrategy`: the token IS the Django session key (`create_session_token` saves the session if needed and returns `session_key`; `lookup_session` checks the session store). [sessions.py](https://github.com/pennersr/django-allauth/blob/main/allauth/headless/tokens/strategies/sessions.py)
- Token is read from request header `x-session-token` (case-insensitive). [strategies/base.py](https://github.com/pennersr/django-allauth/blob/main/allauth/headless/tokens/strategies/base.py)
- Base strategy's `create_access_token` returns `None`; `create_access_token_payload` wraps a token as `{"access_token": ...}` (used by alternate strategies such as JWT; docs list a "JWT Tokens" strategy page, content not read).
- Browser client = cookie session + CSRF. App client = `X-Session-Token` header, no CSRF. Both are available simultaneously unless `HEADLESS_CLIENTS` is narrowed.
- How django-ninja authenticates (source, [ninja/security/session.py](https://github.com/vitalik/django-ninja/blob/master/ninja/security/session.py), [apikey.py](https://github.com/vitalik/django-ninja/blob/master/ninja/security/apikey.py)):
  - `SessionAuth(APIKeyCookie)` uses `param_name = settings.SESSION_COOKIE_NAME`; `authenticate` returns `request.user` if authenticated else `None`. Also `SessionAuthSuperUser` and `SessionAuthIsStaff` (staff accepted if authenticated and `is_staff`; as read, superuser OR staff passes).
  - `APIKeyCookie.__init__(csrf: bool = True)`: when `csrf` is true and the operation is not marked `_ninja_csrf_exempt`, `check_csrf(request)` runs inside `_get_key`; failure raises `HttpError(403, "CSRF check Failed")`.
  - `APIKeyHeader` does no CSRF check; reads `request.headers.get(param_name)`.
  - So a Ninja endpoint can authenticate either via the allauth browser session cookie (SessionAuth, CSRF enforced) or via a custom `APIKeyHeader` subclass that reads `X-Session-Token` and resolves the user from the session store **[no first-party example found for this; it follows from the two code facts above]**.
- Ninja docs page (`django-ninja.dev/guides/authentication/`) returned 404 in this pass; facts above are from source only.

## 3. CSRF / CORS / cookies across origins or subdomains

allauth CORS page ([CORS](https://docs.allauth.org/en/latest/headless/cors.html)): add `corsheaders` app + `corsheaders.middleware.CorsMiddleware`; `CORS_ALLOWED_ORIGINS` lists frontend origins; `CORS_ALLOW_HEADERS` = defaults plus `x-session-token`, `x-email-verification-key`, `x-password-reset-key`; `CORS_ALLOW_CREDENTIALS = True`.

Django CSRF facts ([ref/csrf](https://docs.djangoproject.com/en/5.2/ref/csrf/), [howto/csrf](https://docs.djangoproject.com/en/5.2/howto/csrf/), [settings](https://docs.djangoproject.com/en/5.2/ref/settings/)):
- Unsafe methods need a CSRF cookie plus token (header `X-CSRFToken`, name from `CSRF_HEADER_NAME`). Default JS flow reads the `csrftoken` cookie (requires `CSRF_COOKIE_HTTPONLY=False`, `CSRF_USE_SESSIONS=False`).
- Middleware checks `Origin` against current host and `CSRF_TRUSTED_ORIGINS` (entries with scheme, e.g. `https://app.example.com`, wildcards `https://*.example.com` supported). For HTTPS without `Origin`, strict Referer check.
- `CSRF_COOKIE_DOMAIN=".example.com"` allows cross-subdomain POSTs (Referer compared against it); if unset, referer must match `Host`. Docs warn subdomains can set cookies for the whole domain and thereby circumvent CSRF protection.
- CSRF tokens rotate on login.
- `CSRF_COOKIE_SAMESITE` and `SESSION_COOKIE_SAMESITE` default `Lax` (the session default is from Django knowledge; settings page was truncated before the Sessions section **[unverified on page]**). `SESSION_COOKIE_DOMAIN` default `None`; set `".example.com"` to share across subdomains (same caveat).
- Django's howto notes legitimate cross-origin POSTs need CORS + `CSRF_TRUSTED_ORIGINS`, and that the token must be obtained from a page/cookie the client can read.
- `ensure_csrf_cookie` forces the CSRF cookie to be sent from a view.
- allauth browser client calls `get_token()` so responses set the CSRF cookie. allauth's own end-to-end browser-CSRF guidance (FAQ / "session/CSRF" sections) was not retrievable **[unverified]**; the FAQ page fetched only covered signup extra fields (`ACCOUNT_SIGNUP_FORM_CLASS`, since headed `ACCOUNT_FORMS` don't apply to headless).
- Not directly established from a primary source: how an SSR server (Node) should forward browser cookies/CSRF header to Django on behalf of the user. What the sources establish is only the mechanics: the session cookie and `csrftoken` cookie are plain request cookies, `X-CSRFToken` is a plain header, and `X-Session-Token` is a plain header.

## 4. Anonymous Oscar basket / guest across requests

Source: [oscar/apps/basket/middleware.py](https://github.com/django-oscar/django-oscar/blob/master/src/oscar/apps/basket/middleware.py), [defaults.py](https://github.com/django-oscar/django-oscar/blob/master/src/oscar/defaults.py).
- Anonymous basket identity is NOT the Django session. It is a cookie named by `OSCAR_BASKET_COOKIE_OPEN` (default `"oscar_open_basket"`) holding a signed basket id (`django.core.signing.Signer().sign(basket_id)`). Lookup: `Basket` with `pk=basket_id, owner=None, status=OPEN`; bad signature/missing basket queues cookie deletion.
- Cookie is set in `process_response` only for anonymous users with a saved basket (has id) and no valid cookie: `max_age=OSCAR_BASKET_COOKIE_LIFETIME` (default 7 days), `secure=OSCAR_BASKET_COOKIE_SECURE` (default `False`), `httponly=True`. No `samesite` argument is passed in the code as summarized **[unverified: confirm in file]**.
- An empty anonymous basket is an unsaved `Basket()`, persisted on first add; so no cookie exists until the first item is added.
- Authenticated user: basket via `get_or_create(owner=request.user)`; multiple baskets are merged; a cookie basket is merged in (`master.merge(slave, add_quantities=False)`, so duplicate-line quantities are not summed) and cookie deleted.
- `request.basket` is a lazy object populated by `BasketMiddleware` (a Django middleware), meaning it is computed from Django request cookies on any request that goes through Oscar's middleware stack. `get_cookie_key()` is an overridable hook.
- Guest checkout settings: `OSCAR_ALLOW_ANON_CHECKOUT = False` (default), `OSCAR_ALLOW_GUEST_CHECKOUT_WITH_ACCOUNT = False`. [defaults.py](https://github.com/django-oscar/django-oscar/blob/master/src/oscar/defaults.py)
- Implication of the facts above: in a Ninja API, the basket is recognised only when the request carries the `oscar_open_basket` cookie and passes through Oscar's `BasketMiddleware`. An SSR server and a browser each need that cookie to reach the same basket; the cookie is `HttpOnly`, so browser JS cannot read it but the browser sends it automatically to its own origin/matching domain. If the API lives on another origin, the cookie is set for the API's host and is sent on cross-origin requests only with `credentials: "include"` + `CORS_ALLOW_CREDENTIALS` (CORS and SameSite rules; not verified against a first-party Oscar+headless example).
- Oscar basket merge on login happens inside the middleware when the next request is made as the authenticated user (not in a login signal) per the code summary.

## 5. Email/password signup, login, email verification (headless)

Endpoints under `/_allauth/{client}/v1/` ([openapi.yaml](https://github.com/pennersr/django-allauth/blob/main/allauth/headless/spec/doc/openapi.yaml); the full spec has 119k chars and I read ~100k + a tail slice):
- `POST auth/signup`, `POST auth/login`, `GET/DELETE auth/session`, `GET/DELETE auth/sessions`, `GET config`, `POST auth/password/request`, `GET/POST auth/password/reset`, `POST auth/reauthenticate`, `GET/POST/PUT/PATCH/DELETE account/email`.
- Email verification: `GET auth/email/verify` (key supplied via `X-Email-Verification-Key` header, param "The email verification key") returns info; `POST auth/email/verify` with `key` completes it; `POST auth/email/verify/resend` requires `ACCOUNT_EMAIL_VERIFICATION_SUPPORTS_RESEND = True`.
- Flow responses: HTTP 401 with a `flows` array where `is_pending: true` marks the next step (`verify_email`, `mfa_authenticate`, `login_by_code`, `provider_signup`). 401 on POST `auth/email/verify` can mean "verified but not logged in" (e.g. `ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION=False`). 409 = conflict (already logged in, or flow not pending). 410 (from source) = unknown session token.
- Account settings ([account configuration](https://docs.allauth.org/en/latest/account/configuration.html)): `ACCOUNT_LOGIN_METHODS` default `{"username"}`; `ACCOUNT_SIGNUP_FIELDS` default `['username*','email','password1*','password2*']` (asterisk = required); `ACCOUNT_EMAIL_VERIFICATION` default `"optional"` (`"mandatory"` blocks login until verified, requires `email*` in signup fields; `"none"`); `ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED` default `False` (link vs code); `ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION` default `False` (link-based only); `ACCOUNT_UNIQUE_EMAIL` default `True`; `ACCOUNT_CHANGE_EMAIL` default `False`; `ACCOUNT_USER_MODEL_USERNAME_FIELD` default `"username"`.
- Extra signup fields in headless go through `ACCOUNT_SIGNUP_FORM_CLASS` (headed `ACCOUNT_FORMS` overrides don't apply). [FAQ](https://docs.allauth.org/en/latest/headless/faq.html)

## 6. Gaps to resolve if needed
- allauth docs on session cookie vs CSRF specifics for the browser client, and the `meta.session_token` response shape (not retrieved).
- Whether Oscar's basket cookie sets `samesite` (confirm in `middleware.py`).
- Ninja docs page for authentication (404'd).
- Whether the Django session key rotates on login and how that interacts with app-client `X-Session-Token` after login (the response carries a new token for app clients per `expose_access_token`; exact behaviour not read).
