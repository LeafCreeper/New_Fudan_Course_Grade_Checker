"""WebVPN URL encoding + authenticated session for Fudan University.

Fudan's WebVPN (webvpn.fudan.edu.cn) rewrites every upstream URL into a
proxy URL whose host segment is the AES-128-CFB ciphertext of the real
hostname, hex-encoded and prefixed with the IV.

    https://fdjwgl.fudan.edu.cn/student/for-std/grade/sheet/
        ->
    https://webvpn.fudan.edu.cn/https/<iv><ciphertext>/student/for-std/grade/sheet/

The URL-encoding helpers come first; ``WebVPNSession`` below them owns
the full IDP (id.fudan.edu.cn) login that establishes the VPN session.
"""

import html as html_mod
import re
import base64
from binascii import hexlify, unhexlify
from urllib.parse import urlparse, quote

import requests
from Crypto.Cipher import AES
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5

from src import config


def encrypt_host(hostname: str) -> str:
    """Encrypt a hostname with AES-128-CFB; return hex ciphertext.

    The IV is fixed (config.WEBVPN_AES_IV) and prepended to the proxy URL
    in the clear, so we only return the ciphertext half here.
    """
    cipher = AES.new(
        config.WEBVPN_AES_KEY, AES.MODE_CFB, config.WEBVPN_AES_IV, segment_size=128
    )
    return hexlify(cipher.encrypt(hostname.encode("utf-8"))).decode("ascii")


def decrypt_host(ciphertext_hex: str) -> str:
    """Inverse of :func:`encrypt_host`."""
    cipher = AES.new(
        config.WEBVPN_AES_KEY, AES.MODE_CFB, config.WEBVPN_AES_IV, segment_size=128
    )
    return cipher.decrypt(unhexlify(ciphertext_hex)).decode("utf-8")


def get_vpn_url(url: str) -> str:
    """Convert a real URL into its WebVPN proxy URL.

    Example::

        https://fdjwgl.fudan.edu.cn/student/for-std/grade/sheet/info/123
        ->
        https://webvpn.fudan.edu.cn/https/<iv><host>/student/for-std/grade/sheet/info/123

    Non-standard ports are encoded as ``<scheme>-<port>``.
    """
    parsed = urlparse(url)
    protocol = parsed.scheme
    hostname = parsed.hostname
    port = parsed.port

    # Re-attach path + query + fragment onto the proxy URL's tail.
    path = parsed.path
    if parsed.query:
        path += "?" + parsed.query
    if parsed.fragment:
        path += "#" + parsed.fragment
    path = path.lstrip("/")

    iv_hex = hexlify(config.WEBVPN_AES_IV).decode("ascii")
    encrypted = encrypt_host(hostname)

    # Encode non-default ports so the proxy can dial them.
    port_suffix = ""
    if port and not (
        (protocol == "http" and port == 80)
        or (protocol == "https" and port == 443)
    ):
        port_suffix = f"-{port}"

    vpn_url = f"{config.WEBVPN_BASE}/{protocol}{port_suffix}/{iv_hex}{encrypted}"
    if path:
        vpn_url += f"/{path}"
    return vpn_url


def get_ordinary_url(vpn_url: str) -> str:
    """Convert a WebVPN proxy URL back to the real URL it points at."""
    parsed = urlparse(vpn_url)
    parts = parsed.path.strip("/").split("/", 2)
    if len(parts) < 2:
        raise ValueError(f"Invalid WebVPN URL: {vpn_url}")

    protocol_part, encoded_host = parts[0], parts[1]
    rest = parts[2] if len(parts) > 2 else ""

    # protocol_part looks like "https" or "https-8080"
    if "-" in protocol_part:
        protocol, port_str = protocol_part.rsplit("-", 1)
        port = f":{port_str}"
    else:
        protocol, port = protocol_part, ""

    # Strip the 32-char IV hex prefix, then decrypt the host.
    hostname = decrypt_host(encoded_host[32:])

    original = f"{protocol}://{hostname}{port}"
    if rest:
        original += f"/{rest}"
    if parsed.query:
        original += f"?{parsed.query}"
    return original


class WebVPNSession:
    """An authenticated WebVPN session.

    ``login()`` runs the 7-step IDP flow that proves who we are to the VPN
    gateway itself.  Once that succeeds, the session cookie lets us reach
    *any* fudan.edu.cn host through ``get_vpn_url`` — but each protected
    host (e.g. fdjwgl) still needs its own SSO handshake, handled by
    :meth:`authenticate_grade`.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": config.USER_AGENT})
        self.logged_in = False

    # ── public API ───────────────────────────────────────────────────────

    def login(self, student_id: str = None, password: str = None) -> bool:
        """Run the full 7-step IDP authentication for the WebVPN gateway.

        Returns True on success; raises on any step that fails.
        """
        student_id = student_id or config.STUDENT_ID
        password = password or config.PASSWORD
        if not student_id or not password:
            raise ValueError(
                "Student ID and password are required. "
                "Set StuId and UISPsw environment variables."
            )

        print("[1/7] Getting authentication context...")
        lck, entity_id = self._get_auth_context()

        print("[2/7] Querying authentication methods...")
        auth_chain_code, request_type = self._query_auth_methods(lck, entity_id)

        print("[3/7] Getting RSA public key...")
        pub_key_pem = self._get_public_key()

        print("[4/7] Encrypting password...")
        encrypted_password = self._encrypt_password(password, pub_key_pem)

        print("[5/7] Executing authentication...")
        login_token = self._auth_execute(
            student_id, encrypted_password, lck, entity_id,
            auth_chain_code, request_type,
        )

        print("[6/7] Getting CAS ticket...")
        ticket_url = self._get_cas_ticket(login_token)

        print("[7/7] Establishing WebVPN session...")
        self._establish_session(ticket_url)

        self.logged_in = True
        print("[*] WebVPN login successful!")
        return True

    def get(self, url: str, **kwargs) -> requests.Response:
        """GET a *real* URL through WebVPN (auto-converted)."""
        kwargs.setdefault("timeout", 30)
        return self.session.get(get_vpn_url(url), **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        """POST a *real* URL through WebVPN (auto-converted)."""
        kwargs.setdefault("timeout", 30)
        return self.session.post(get_vpn_url(url), **kwargs)

    def get_raw(self, url: str, **kwargs) -> requests.Response:
        """GET without URL conversion (for already-converted VPN URLs)."""
        kwargs.setdefault("timeout", 30)
        return self.session.get(url, **kwargs)

    def post_raw(self, url: str, **kwargs) -> requests.Response:
        """POST without URL conversion."""
        kwargs.setdefault("timeout", 30)
        return self.session.post(url, **kwargs)

    # ── IDP login steps (direct to id.fudan.edu.cn, no VPN) ──────────────

    def _get_auth_context(self) -> tuple[str, str]:
        """Step 1: hit the authenticate endpoint, extract ``lck`` from the
        redirect chain.  ``entityId`` for the WebVPN gateway is the gateway
        itself."""
        service_url = f"{config.WEBVPN_BASE}/login?cas_login=true"
        url = (
            f"{config.IDP_BASE}/idp/authCenter/authenticate"
            f"?service={quote(service_url, safe='')}"
        )
        resp = self.session.get(url, allow_redirects=False, timeout=60)

        # Follow redirects manually until ``lck=`` appears in the Location.
        location = resp.headers.get("Location", "")
        while resp.status_code in (301, 302) and "lck=" not in location:
            resp = self.session.get(location, allow_redirects=False, timeout=60)
            location = resp.headers.get("Location", "")
        if resp.status_code in (301, 302):
            location = resp.headers.get("Location", "")

        match = re.search(r"[?&]lck=([^&]+)", location)
        if not match:
            raise RuntimeError(
                f"Failed to extract lck from redirect (status={resp.status_code})"
            )
        print("    lck: OK")
        return match.group(1), config.WEBVPN_BASE

    def _query_auth_methods(self, lck: str, entity_id: str) -> tuple[str, str]:
        """Step 2: ask IDP which auth methods exist; grab the password one."""
        url = f"{config.IDP_BASE}/idp/authn/queryAuthMethods"
        resp = self.session.post(
            url,
            json={"lck": lck, "entityId": entity_id},
            headers={
                "Content-Type": "application/json",
                "Referer": f"{config.IDP_BASE}/ac/",
                "Origin": config.IDP_BASE,
            },
            timeout=60,
        )
        data = resp.json()

        # ``data["data"]`` is a list of auth methods; pick userAndPwd.
        request_type = data.get("requestType", "chain_type")
        auth_chain_code = ""
        for method in data.get("data", []):
            if method.get("moduleCode") == "userAndPwd":
                auth_chain_code = method.get("authChainCode", "")
                break
        if not auth_chain_code:
            raise RuntimeError("Failed to get authChainCode")
        print("    authChainCode: OK")
        return auth_chain_code, request_type

    def _get_public_key(self) -> str:
        """Step 3: fetch the RSA public key used to encrypt the password."""
        url = f"{config.IDP_BASE}/idp/authn/getJsPublicKey"
        resp = self.session.get(
            url,
            headers={"Referer": f"{config.IDP_BASE}/ac/"},
            timeout=60,
        )
        pub_key_b64 = resp.json().get("data", "")
        if not pub_key_b64:
            raise RuntimeError("Failed to get public key")
        print("    Got RSA public key")
        return pub_key_b64

    def _encrypt_password(self, password: str, pub_key_b64: str) -> str:
        """Step 4: RSA-encrypt the password (PKCS1_v1_5) and base64 it."""
        pem = (
            "-----BEGIN PUBLIC KEY-----\n"
            + pub_key_b64
            + "\n-----END PUBLIC KEY-----"
        )
        rsa_key = RSA.import_key(pem)
        cipher = PKCS1_v1_5.new(rsa_key)
        encrypted = cipher.encrypt(password.encode("utf-8"))
        return base64.b64encode(encrypted).decode("ascii")

    def _auth_execute(
        self, student_id: str, encrypted_password: str,
        lck: str, entity_id: str,
        auth_chain_code: str, request_type: str,
    ) -> str:
        """Step 5: submit credentials, receive a loginToken."""
        url = f"{config.IDP_BASE}/idp/authn/authExecute"
        payload = {
            "authModuleCode": "userAndPwd",
            "authChainCode": auth_chain_code,
            "entityId": entity_id,
            "requestType": request_type,
            "lck": lck,
            "authPara": {
                "loginName": student_id,
                "password": encrypted_password,
                "verifyCode": "",
            },
        }
        resp = self.session.post(
            url, json=payload,
            headers={
                "Content-Type": "application/json",
                "Referer": f"{config.IDP_BASE}/ac/",
                "Origin": config.IDP_BASE,
            },
            timeout=60,
        )
        data = resp.json()
        if str(data.get("code")) != "200":
            raise RuntimeError(f"Authentication failed (code={data.get('code')})")
        login_token = data.get("loginToken", "")
        if not login_token:
            raise RuntimeError("No loginToken in response")
        print("    loginToken: OK")
        return login_token

    def _get_cas_ticket(self, login_token: str) -> str:
        """Step 6: exchange the loginToken for a CAS ticket URL.

        The response is an HTML page with a JS redirect carrying the ticket.
        """
        url = f"{config.IDP_BASE}/idp/authCenter/authnEngine"
        resp = self.session.post(
            url, data={"loginToken": login_token},
            headers={
                "Referer": f"{config.IDP_BASE}/ac/",
                "Origin": config.IDP_BASE,
            },
            timeout=60,
        )
        html = resp.text
        match = re.search(r'locationValue\s*=\s*"([^"]*ticket=[^"]*)"', html)
        if not match:
            match = re.search(r'(https?://[^\s"\'<>]*ticket=[^\s"\'<>]*)', html)
        if not match:
            raise RuntimeError(
                f"Failed to extract ticket URL (response length: {len(html)})"
            )
        ticket_url = html_mod.unescape(match.group(1))
        print("    Ticket extracted.")
        return ticket_url

    def _establish_session(self, ticket_url: str):
        """Step 7: follow the ticket URL to plant the WebVPN session cookie.

        CAS tickets are single-use, so this GET must complete in one shot.
        The WebVPN gateway only marks the session active *after* it finishes
        validating the ticket server-side — a client-side timeout mid-body
        leaves the cookie planted but the session unvalidated, so the next
        request bounces back to ``/login``.  We use a generous timeout and
        then probe the portal to confirm the session actually took.
        """
        resp = self.session.get(ticket_url, allow_redirects=True, timeout=90)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Failed to establish WebVPN session (status={resp.status_code})"
            )
        # Confirm the session is live: a cold session 302s to /login.
        probe = self.session.get(
            config.WEBVPN_BASE + "/", allow_redirects=False, timeout=15
        )
        location = probe.headers.get("Location", "")
        if probe.status_code == 302 and "/login" in location:
            raise RuntimeError(
                "WebVPN session not active after ticket follow "
                "(portal still redirects to /login)"
            )
        print("    Session established.")
