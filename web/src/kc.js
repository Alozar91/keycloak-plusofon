// Keycloak OIDC (Authorization Code + PKCE) для страницы профиля.
// Токены живут только в JS-памяти (sessionStorage для возврата после redirect).

const KC_BASE = "https://merch-vibe.ru:8443";
const REALM = "plusofon-test";
const CLIENT_ID = "web-profile";
const REDIRECT_URI = location.origin + "/profile";

const b64url = (buf) =>
  btoa(String.fromCharCode(...new Uint8Array(buf)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

async function pkce() {
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return { verifier, challenge: b64url(digest) };
}

export const kc = {
  get token() {
    return sessionStorage.getItem("kc-access-token");
  },

  async login() {
    const { verifier, challenge } = await pkce();
    sessionStorage.setItem("pkce-verifier", verifier);
    const url = new URL(`${KC_BASE}/realms/${REALM}/protocol/openid-connect/auth`);
    url.searchParams.set("client_id", CLIENT_ID);
    url.searchParams.set("response_type", "code");
    url.searchParams.set("scope", "openid profile");
    url.searchParams.set("redirect_uri", REDIRECT_URI);
    url.searchParams.set("code_challenge", challenge);
    url.searchParams.set("code_challenge_method", "S256");
    location.assign(url);
  },

  // обработка ?code=... после redirect; true если вошли
  async handleRedirect() {
    const params = new URLSearchParams(location.search);
    const code = params.get("code");
    if (!code) return false;
    history.replaceState(null, "", "/profile");
    const verifier = sessionStorage.getItem("pkce-verifier");
    const resp = await fetch(
      `${KC_BASE}/realms/${REALM}/protocol/openid-connect/token`,
      {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "authorization_code",
          client_id: CLIENT_ID,
          code,
          redirect_uri: REDIRECT_URI,
          code_verifier: verifier,
        }),
      }
    );
    if (!resp.ok) throw new Error("token exchange failed: " + resp.status);
    const data = await resp.json();
    sessionStorage.setItem("kc-access-token", data.access_token);
    return true;
  },

  logout() {
    sessionStorage.removeItem("kc-access-token");
    location.assign(
      `${KC_BASE}/realms/${REALM}/protocol/openid-connect/logout?post_logout_redirect_uri=${encodeURIComponent(location.origin)}&client_id=${CLIENT_ID}`
    );
  },
};

const json = async (res) => {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401) { sessionStorage.removeItem("kc-access-token"); }
    throw new Error(body.detail ? String(body.detail) : res.statusText);
  }
  return body;
};

const authHeaders = () => ({ Authorization: "Bearer " + kc.token });

// API профиля (backend проверяет Bearer через JWKS)
export const profile = {
  get: () => json(fetch("/api/profile", { headers: authHeaders() })).then((r) => r),
  async get() { return json(await fetch("/api/profile", { headers: authHeaders() })); },
  async setUsername(username) {
    return json(await fetch("/api/profile/username", {
      method: "PUT", headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ username }),
    }));
  },
  async setEmail(email) {
    return json(await fetch("/api/profile/email", {
      method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ email }),
    }));
  },
  async setPassword(password) {
    return json(await fetch("/api/profile/password", {
      method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ password }),
    }));
  },
  async bindPhone(phone, verificationId) {
    return json(await fetch("/api/profile/phone", {
      method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ phone, verification_id: verificationId }),
    }));
  },
  async unbindPhone() {
    return json(await fetch("/api/profile/phone", { method: "DELETE", headers: authHeaders() }));
  },
  async outbox(to) {
    const API_KEY = import.meta.env.VITE_API_KEY || "";
    return json(await fetch("/api/dev/outbox" + (to ? `?to=${encodeURIComponent(to)}` : ""), {
      headers: API_KEY ? { "X-Api-Key": API_KEY } : {},
    }));
  },
};
