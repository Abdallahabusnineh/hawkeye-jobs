import { test } from "node:test";
import assert from "node:assert/strict";
import { ApiError, createApi, takeToken } from "../../src/web/static/api.js";

function fakeWindow(href, stored = null, { blocked = false } = {}) {
  const storage = new Map(stored ? [["hawkeye-token", stored]] : []);
  const win = {
    location: { href },
    sessionStorage: {
      getItem: (key) => { if (blocked) throw new Error("blocked"); return storage.has(key) ? storage.get(key) : null; },
      setItem: (key, value) => { if (blocked) throw new Error("blocked"); storage.set(key, value); },
    },
    history: { replaceState: (_state, _title, url) => { win.replaced = url; } },
    replaced: null,
  };
  return { win, storage };
}

test("takeToken moves the token from the URL into session storage", () => {
  const { win, storage } = fakeWindow("http://127.0.0.1:5555/?token=abc#jobs");
  assert.equal(takeToken(win), "abc");
  assert.equal(storage.get("hawkeye-token"), "abc");
  assert.equal(win.replaced, "/#jobs");
});

test("takeToken reads session storage after a reload", () => {
  const { win } = fakeWindow("http://127.0.0.1:5555/", "abc");
  assert.equal(takeToken(win), "abc");
  assert.equal(win.replaced, null);
});

test("takeToken survives blocked storage", () => {
  assert.equal(takeToken(fakeWindow("http://127.0.0.1:5555/?token=abc", null, { blocked: true }).win), "abc");
  assert.equal(takeToken(fakeWindow("http://127.0.0.1:5555/", null, { blocked: true }).win), "");
});

test("createApi sends the token and JSON, and turns failures into ApiError", async () => {
  const calls = [];
  const fetchImpl = async (path, options) => {
    calls.push({ path, options });
    if (path === "/api/fail") return { ok: false, status: 409, json: async () => ({ error: "run_in_progress" }) };
    if (path === "/api/empty") return { ok: true, status: 204, json: async () => { throw new Error("no body"); } };
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  const api = createApi("tok", fetchImpl);
  assert.deepEqual(await api.patch("/api/jobs/1", { status: "saved" }), { ok: true });
  assert.equal(calls[0].options.method, "PATCH");
  assert.equal(calls[0].options.headers["X-Hawkeye-Token"], "tok");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.equal(calls[0].options.body, JSON.stringify({ status: "saved" }));
  assert.equal(await api.post("/api/empty"), null);
  assert.equal(calls[1].options.body, "{}");
  const get = await api.get("/api/state");
  assert.deepEqual(get, { ok: true });
  assert.equal(calls[2].options.body, undefined);
  assert.equal(calls[2].options.headers["Content-Type"], undefined);
  await assert.rejects(api.get("/api/fail"),
    (error) => error instanceof ApiError && error.status === 409 && error.body.error === "run_in_progress");
  await assert.rejects(createApi("tok", async () => { throw new TypeError("network"); }).get("/api/state"),
    (error) => error instanceof ApiError && error.status === 0);
});
