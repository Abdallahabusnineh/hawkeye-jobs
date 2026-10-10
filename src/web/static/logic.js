/*
 * logic.js — Pure helpers for the Hawkeye Jobs window (no DOM, no network).
 * Unit-tested with `node --test tests/js/*.test.mjs` (a directory argument fails on Node 24). The constants mirror the backend
 * (tests/test_frontend.py checks SOURCE_ORDER, BROWSER_SOURCES, AUTO_HOURS).
 */

export const STATUSES = ["new", "saved", "applied", "dismissed"];
export const FILTERS = ["new", "saved", "applied", "all"];
export const SOURCE_ORDER = ["linkedin", "indeed", "bayt", "naukrigulf", "google_jobs", "linkedin_posts"];
export const BROWSER_SOURCES = ["bayt", "naukrigulf", "google_jobs", "linkedin_posts"];
export const AUTO_HOURS = [2, 4, 6, 12, 24];
export const MAX_KEYWORDS = 20;
export const MAX_KEYWORD_LENGTH = 100;

const has = (object, key) => !!object && Object.prototype.hasOwnProperty.call(object, key);

// ── text ─────────────────────────────────────────────────────────────

export function format(template, params = {}) {
  return String(template).replace(/\{(\w+)\}/g, (match, name) => (has(params, name) ? String(params[name]) : match));
}

export function translate(dict, fallback, key, params = {}) {
  const text = has(dict, key) ? dict[key] : has(fallback, key) ? fallback[key] : key;
  return format(text, params);
}

export function pluralKey(dict, base, n, lang) {
  let rule = "other";
  try {
    rule = new Intl.PluralRules(lang).select(n);
  } catch (error) {
    rule = n === 1 ? "one" : "other";
  }
  const key = `${base}.${rule}`;
  return has(dict, key) ? key : `${base}.other`;
}

export function ageParts(iso, nowMs) {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return null;
  const minutes = Math.max(0, Math.floor((nowMs - then) / 60000));
  if (minutes < 1) return { base: "age.now", n: 0 };
  if (minutes < 60) return { base: "age.minutes", n: minutes };
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return { base: "age.hours", n: hours };
  return { base: "age.days", n: Math.floor(hours / 24) };
}

// ── run progress ─────────────────────────────────────────────────────

export const STARTING_TTL_MS = 30000;

export function startingRun(now = Date.now()) {
  return { run_id: null, trigger: "manual", total: 0, done: 0, found: 0, new: 0, current: null, stopping: false, since: now };
}

export function applyEvent(run, event) {
  switch (event.type) {
    case "run_started":
      return { run_id: event.run_id, trigger: event.trigger || "manual", total: event.total || 0,
               done: 0, found: 0, new: 0, current: null, stopping: false };
    case "query_started":
      return run ? { ...run, current: { source: event.source, keyword: event.keyword, location_id: event.location_id } } : run;
    case "query_done":
    case "query_failed":
    case "query_skipped":
      if (!run) return run;
      return { ...run, done: run.done + 1, found: run.found + (Number(event.found) || 0),
               new: run.new + (Number(event.new) || 0) };
    case "run_finished":
    case "run_error":
      return null;
    default:
      return run;
  }
}

// GET /api/state -> run snapshot {run_id, trigger, total, done, found, new} (no current query).
export function fromSnapshot(snapshot, previous, now = Date.now(), finished = []) {
  if (snapshot && finished.includes(snapshot.run_id)) snapshot = null;
  if (!snapshot) {
    return previous && previous.run_id === null && now - previous.since < STARTING_TTL_MS ? previous : null;
  }
  const same = !!previous && previous.run_id === snapshot.run_id;
  return {
    run_id: snapshot.run_id, trigger: snapshot.trigger, total: snapshot.total, done: snapshot.done,
    found: snapshot.found, new: snapshot.new,
    current: same ? previous.current : null, stopping: same ? previous.stopping : false,
  };
}

export function progressPercent(run) {
  if (!run || !run.total) return 0;
  return Math.min(100, Math.round((run.done * 100) / run.total));
}

// ── search form ──────────────────────────────────────────────────────

export function addKeyword(list, raw) {
  const text = String(raw || "").replace(/\s+/g, " ").trim();
  if (!text) return { list, error: null };
  if (text.length > MAX_KEYWORD_LENGTH) return { list, error: "too_long" };
  if (list.some((keyword) => keyword.toLowerCase() === text.toLowerCase())) return { list, error: null };
  if (list.length >= MAX_KEYWORDS) return { list, error: "too_many" };
  return { list: [...list, text], error: null };
}

export function filterCountries(countries, query, selected, limit = 50) {
  const needle = String(query || "").trim().toLocaleLowerCase();
  const chosen = new Set(selected);
  return countries
    .filter((c) => !chosen.has(c.id) && (!needle || c.label.toLocaleLowerCase().includes(needle) || c.id.toLowerCase() === needle))
    .slice(0, limit);
}

export function defaultSources(chromeFound) {
  return SOURCE_ORDER.filter((id) => id !== "linkedin_posts" && (chromeFound || !BROWSER_SOURCES.includes(id)));
}

export function orderSources(ids) {
  return SOURCE_ORDER.filter((id) => ids.includes(id));
}

// ── jobs ─────────────────────────────────────────────────────────────

export function jobsQuery({ filter, text, offset, limit }) {
  const params = new URLSearchParams();
  params.set("status", filter); // "all" includes "not interested" (the API handles it)
  if (text && text.trim()) params.set("text", text.trim());
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  return `/api/jobs?${params.toString()}`;
}

const JOB_SOURCE_IDS = {
  linkedin: "linkedin", indeed: "indeed", bayt: "bayt", "bayt.com": "bayt", naukrigulf: "naukrigulf",
  "google jobs": "google_jobs", "linkedin post": "linkedin_posts",
};

export function jobSourceId(source) {
  return JOB_SOURCE_IDS[String(source || "").trim().toLowerCase()] || null;
}

export function nextStatus(current, pressed) {
  return current === pressed ? "new" : pressed;
}

export function jobVisible(status, filter) {
  return filter === "all" || status === filter;
}

// ── codes -> i18n keys ───────────────────────────────────────────────

const KNOWN_ERRORS = ["run_in_progress", "no_settings", "not_running", "chrome_not_found", "outside_data_dir",
                      "files_in_use", "not_found", "forbidden"];

export function apiErrorKey(status, body) {
  let code = body && body.error;
  if (!code && body && body.detail && typeof body.detail === "object") code = Object.values(body.detail)[0];
  if (KNOWN_ERRORS.includes(code)) return `error.${code}`;
  if (status === 0) return "error.offline_app";
  return "error.unexpected";
}

export function queryOutcome(query) {
  if (query.status === "done") return { key: "history.q.done", params: { found: query.found, new: query.new } };
  if (query.status === "failed") return { key: "history.q.failed", params: {} };
  const reason = ["login_required", "browser_unavailable"].includes(query.detail) ? query.detail : "other";
  return { key: `history.q.skipped.${reason}`, params: {} };
}

export function estimateMinutes(seconds) {
  return Math.max(1, Math.round(seconds / 60));
}

export function runFinishedKey(state) {
  if (state === "done") return "notice.run_done";
  if (state === "cancelled") return "notice.run_cancelled";
  return "notice.run_failed";
}

export function browserNoticeKey(reason) {
  return ["chrome_not_found", "start_failed", "crashed"].includes(reason) ? `notice.browser.${reason}` : "notice.browser.start_failed";
}
