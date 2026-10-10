/*
 * api.js — Talks to the app's local server (127.0.0.1) with the per-launch token.
 * The token arrives once in the window URL; it is moved to sessionStorage and
 * removed from the address bar so it never shows up in a Referer or a screenshot.
 */

const TOKEN_KEY = "hawkeye-token";

export function takeToken(win = window) {
  const url = new URL(win.location.href);
  const fromUrl = url.searchParams.get("token");
  if (fromUrl) {
    try {
      win.sessionStorage.setItem(TOKEN_KEY, fromUrl);
    } catch (error) {
      // Storage blocked: the token still works until the window is reloaded.
    }
    url.searchParams.delete("token");
    win.history.replaceState(null, "", url.pathname + url.search + url.hash);
    return fromUrl;
  }
  try {
    return win.sessionStorage.getItem(TOKEN_KEY) || "";
  } catch (error) {
    return "";
  }
}

export class ApiError extends Error {
  constructor(status, body) {
    super(`HTTP ${status}`);
    this.status = status; // 0 = the local server could not be reached
    this.body = body || {};
  }
}

export function createApi(token, fetchImpl = (...args) => window.fetch(...args)) {
  async function request(method, path, body) {
    const headers = { "X-Hawkeye-Token": token };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let response;
    try {
      response = await fetchImpl(path, {
        method, headers, cache: "no-store", body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (error) {
      throw new ApiError(0, {});
    }
    if (response.status === 204) return null;
    let data = null;
    try {
      data = await response.json();
    } catch (error) {
      data = null;
    }
    if (!response.ok) throw new ApiError(response.status, data);
    return data;
  }

  return {
    get: (path) => request("GET", path),
    post: (path, body = {}) => request("POST", path, body),
    put: (path, body) => request("PUT", path, body),
    patch: (path, body) => request("PATCH", path, body),
    // EventSource cannot send headers; /api/events is the only endpoint that accepts ?token=.
    events: () => new EventSource(`/api/events?token=${encodeURIComponent(token)}`),
  };
}
