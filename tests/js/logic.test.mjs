import { test } from "node:test";
import assert from "node:assert/strict";
import * as L from "../../src/web/static/logic.js";

test("format fills named placeholders and leaves unknown ones", () => {
  assert.equal(L.format("{done} of {total}", { done: 3, total: 9 }), "3 of 9");
  assert.equal(L.format("{x}", {}), "{x}");
});

test("translate falls back to English, then to the key", () => {
  assert.equal(L.translate({ a: "ar" }, { a: "en", b: "en-b" }, "a"), "ar");
  assert.equal(L.translate({}, { b: "en-b {n}" }, "b", { n: 2 }), "en-b 2");
  assert.equal(L.translate({}, {}, "missing.key"), "missing.key");
});

test("pluralKey picks the Arabic form and falls back to other", () => {
  const dict = { "x.one": "", "x.two": "", "x.few": "", "x.many": "", "x.other": "" };
  assert.equal(L.pluralKey(dict, "x", 1, "ar"), "x.one");
  assert.equal(L.pluralKey(dict, "x", 2, "ar"), "x.two");
  assert.equal(L.pluralKey(dict, "x", 5, "ar"), "x.few");
  assert.equal(L.pluralKey(dict, "x", 11, "ar"), "x.many");
  assert.equal(L.pluralKey(dict, "x", 100, "ar"), "x.other");
  assert.equal(L.pluralKey(dict, "x", 1, "en"), "x.one");
  assert.equal(L.pluralKey({ "x.other": "" }, "x", 1, "en"), "x.other");
});

test("ageParts buckets minutes, hours and days", () => {
  const now = Date.parse("2026-10-10T12:00:00Z");
  assert.deepEqual(L.ageParts("2026-10-10T11:59:30Z", now), { base: "age.now", n: 0 });
  assert.deepEqual(L.ageParts("2026-10-10T11:55:00Z", now), { base: "age.minutes", n: 5 });
  assert.deepEqual(L.ageParts("2026-10-10T09:00:00Z", now), { base: "age.hours", n: 3 });
  assert.deepEqual(L.ageParts("2026-10-07T12:00:00Z", now), { base: "age.days", n: 3 });
  assert.equal(L.ageParts("not a date", now), null);
});

test("applyEvent tracks a run from start to finish", () => {
  let run = L.applyEvent(null, { type: "run_started", run_id: 7, total: 3, trigger: "auto" });
  assert.deepEqual(run, { run_id: 7, trigger: "auto", total: 3, done: 0, found: 0, new: 0, current: null, stopping: false });
  run = L.applyEvent(run, { type: "query_started", source: "bayt", keyword: "Java", location_id: "jo", index: 0 });
  assert.deepEqual(run.current, { source: "bayt", keyword: "Java", location_id: "jo" });
  run = L.applyEvent(run, { type: "query_done", found: 5, new: 2 });
  run = L.applyEvent(run, { type: "query_failed", error: "HTTPError" });
  run = L.applyEvent(run, { type: "query_skipped", reason: "login_required" });
  assert.equal(run.done, 3);
  assert.equal(run.found, 5);
  assert.equal(run.new, 2);
  assert.equal(L.applyEvent(run, { type: "jobs_reported", count: 2 }), run);
  assert.equal(L.applyEvent(run, { type: "run_finished", state: "done" }), null);
});

test("applyEvent keeps stopping, ignores query events without a run, clears on run_error", () => {
  const run = { ...L.startingRun(), run_id: 1, total: 2, stopping: true };
  assert.equal(L.applyEvent(run, { type: "query_done", found: 1, new: 0 }).stopping, true);
  assert.equal(L.applyEvent(null, { type: "query_done", found: 1, new: 1 }), null);
  assert.equal(L.applyEvent(run, { type: "run_error" }), null);
});

test("fromSnapshot keeps the current query of the same run and the starting placeholder", () => {
  const prev = { run_id: 4, trigger: "manual", total: 9, done: 1, found: 0, new: 0,
                 current: { source: "linkedin", keyword: "Java", location_id: "jo" }, stopping: false };
  const snap = { run_id: 4, trigger: "manual", total: 9, done: 2, found: 3, new: 1 };
  assert.deepEqual(L.fromSnapshot(snap, prev), { ...snap, current: prev.current, stopping: false });
  assert.equal(L.fromSnapshot(snap, null).current, null);
  assert.equal(L.fromSnapshot({ ...snap, run_id: 5 }, prev).current, null);
  const starting = L.startingRun();
  assert.equal(L.fromSnapshot(null, starting), starting);
  assert.equal(L.fromSnapshot(null, prev), null);
});

test("the starting placeholder expires after 30 seconds", () => {
  const starting = L.startingRun(1000);
  assert.equal(starting.since, 1000);
  assert.equal(L.fromSnapshot(null, starting, 1000 + 29000), starting);
  assert.equal(L.fromSnapshot(null, starting, 1000 + 31000), null);
  assert.equal(L.applyEvent(starting, { type: "run_started", run_id: 2, total: 1 }).since, undefined);
});

test("fromSnapshot ignores a snapshot of an already finished run", () => {
  const snap = { run_id: 7, trigger: "manual", total: 2, done: 2, found: 1, new: 1 };
  assert.equal(L.fromSnapshot(snap, null, 0, [3, 7]), null);
  assert.equal(L.fromSnapshot(snap, null, 0, [3]).run_id, 7);
  const starting = L.startingRun(1000);
  assert.equal(L.fromSnapshot(null, starting, 2000, [7]), starting);
  assert.equal(L.fromSnapshot(null, starting, 40000, [7]), null);
});

test("progressPercent is bounded", () => {
  assert.equal(L.progressPercent(null), 0);
  assert.equal(L.progressPercent({ done: 3, total: 4 }), 75);
  assert.equal(L.progressPercent({ done: 5, total: 4 }), 100);
  assert.equal(L.progressPercent({ done: 0, total: 0 }), 0);
});

test("addKeyword trims, dedupes case-insensitively and enforces limits", () => {
  assert.deepEqual(L.addKeyword([], "  Java   Developer "), { list: ["Java Developer"], error: null });
  assert.deepEqual(L.addKeyword(["Java"], "java"), { list: ["Java"], error: null });
  assert.deepEqual(L.addKeyword([], "x".repeat(101)), { list: [], error: "too_long" });
  const twenty = Array.from({ length: 20 }, (_, i) => `k${i}`);
  assert.equal(L.addKeyword(twenty, "new").error, "too_many");
  assert.deepEqual(L.addKeyword(["a"], "   "), { list: ["a"], error: null });
});

test("filterCountries matches labels and ids, skips chosen ones, caps the list", () => {
  const countries = [{ id: "jo", label: "الأردن" }, { id: "sa", label: "السعودية" }, { id: "remote", label: "عن بُعد" }];
  assert.deepEqual(L.filterCountries(countries, "الأ", []).map((c) => c.id), ["jo"]);
  assert.deepEqual(L.filterCountries(countries, "SA", []).map((c) => c.id), ["sa"]);
  assert.deepEqual(L.filterCountries(countries, "", ["jo"]).map((c) => c.id), ["sa", "remote"]);
  assert.equal(L.filterCountries(countries, "", [], 1).length, 1);
  assert.ok(L.filterCountries(countries, "الأ", []).map((c) => c.id).includes("jo"));
});

test("filterCountries matches word starts with Arabic-aware normalisation", () => {
  const ids = (list, q) => L.filterCountries(list, q, []).map((c) => c.id);
  const ar = [{ id: "jo", label: "الأردن" }, { id: "sa", label: "السعودية" }, { id: "ma", label: "المغرب" },
              { id: "mx", label: "المكسيك" }, { id: "ae", label: "الإمارات العربية المتحدة" }];
  assert.deepEqual(ids(ar, "سعود"), ["sa"]);
  assert.deepEqual(ids(ar, "السعودية"), ["sa"]);
  assert.deepEqual(ids(ar, "اردن"), ["jo"]);
  assert.deepEqual(ids(ar, "الم"), ["ma", "mx", "ae"]);
  assert.ok(!ids(ar, "الم").includes("jo"));
  const en = [{ id: "GB", label: "United Kingdom" }, { id: "US", label: "United States" },
              { id: "TN", label: "Tunisia" }, { id: "RE", label: "Réunion" }];
  assert.deepEqual(ids(en, "Uni"), ["GB", "US"]);
  assert.deepEqual(ids(en, "reu"), ["RE"]);
  assert.deepEqual(ids(en, "united k"), ["GB"]);
  assert.deepEqual(ids(en, "tn"), ["TN"]);
});

test("jobsQuery builds the API path", () => {
  assert.equal(L.jobsQuery({ filter: "new", text: " java ", offset: 0, limit: 50 }),
               "/api/jobs?status=new&text=java&limit=50&offset=0");
  assert.equal(L.jobsQuery({ filter: "all", text: "", offset: 50, limit: 50 }),
               "/api/jobs?status=all&limit=50&offset=50");
});

test("jobSourceId maps stored source names", () => {
  assert.equal(L.jobSourceId("Linkedin"), "linkedin");
  assert.equal(L.jobSourceId("Indeed"), "indeed");
  assert.equal(L.jobSourceId("Bayt"), "bayt");
  assert.equal(L.jobSourceId("NaukriGulf"), "naukrigulf");
  assert.equal(L.jobSourceId("Google Jobs"), "google_jobs");
  assert.equal(L.jobSourceId("LinkedIn Post"), "linkedin_posts");
  assert.equal(L.jobSourceId("Something else"), null);
});

test("statuses toggle back to new and decide visibility", () => {
  assert.equal(L.nextStatus("new", "saved"), "saved");
  assert.equal(L.nextStatus("saved", "saved"), "new");
  assert.equal(L.nextStatus("dismissed", "new"), "new");
  assert.equal(L.jobVisible("saved", "new"), false);
  assert.equal(L.jobVisible("saved", "saved"), true);
  assert.equal(L.jobVisible("dismissed", "all"), true);
});

test("default sources depend on Chrome and orderSources follows the catalog order", () => {
  assert.deepEqual(L.defaultSources(true), ["linkedin", "indeed", "bayt", "naukrigulf", "google_jobs"]);
  assert.deepEqual(L.defaultSources(false), ["linkedin", "indeed"]);
  assert.deepEqual(L.orderSources(["google_jobs", "linkedin", "nope", "linkedin"]), ["linkedin", "google_jobs"]);
});

test("apiErrorKey maps known codes and hides unknown ones", () => {
  assert.equal(L.apiErrorKey(409, { error: "run_in_progress" }), "error.run_in_progress");
  assert.equal(L.apiErrorKey(404, { detail: { job: "not_found" } }), "error.not_found");
  assert.equal(L.apiErrorKey(0, {}), "error.offline_app");
  assert.equal(L.apiErrorKey(500, { error: "http_500" }), "error.unexpected");
  assert.equal(L.apiErrorKey(409, { error: "something_new" }), "error.unexpected");
  assert.equal(L.apiErrorKey(500, null), "error.unexpected");
});

test("queryOutcome turns stored rows into keys", () => {
  assert.deepEqual(L.queryOutcome({ status: "done", found: 3, new: 1 }), { key: "history.q.done", params: { found: 3, new: 1 } });
  assert.equal(L.queryOutcome({ status: "failed", detail: "HTTPError" }).key, "history.q.failed");
  assert.equal(L.queryOutcome({ status: "skipped", detail: "login_required" }).key, "history.q.skipped.login_required");
  assert.equal(L.queryOutcome({ status: "skipped", detail: "weird" }).key, "history.q.skipped.other");
});

test("small key helpers", () => {
  assert.equal(L.estimateMinutes(20), 1);
  assert.equal(L.estimateMinutes(90), 2);
  assert.equal(L.estimateMinutes(600), 10);
  assert.equal(L.runFinishedKey("done"), "notice.run_done");
  assert.equal(L.runFinishedKey("cancelled"), "notice.run_cancelled");
  assert.equal(L.runFinishedKey("failed"), "notice.run_failed");
  assert.equal(L.browserNoticeKey("crashed"), "notice.browser.crashed");
  assert.equal(L.browserNoticeKey("odd"), "notice.browser.start_failed");
});

test("shouldReplaceToast keeps an actionable toast over a plain one", () => {
  assert.equal(L.shouldReplaceToast(null, { action: null }), true);
  assert.equal(L.shouldReplaceToast({ action: { label: "x" } }, { action: null }), false);
  assert.equal(L.shouldReplaceToast({ action: { label: "x" } }, { action: { label: "y" } }), true);
  assert.equal(L.shouldReplaceToast({ action: null }, { action: null }), true);
});
