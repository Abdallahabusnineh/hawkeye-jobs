/*
 * app.js — The Hawkeye Jobs window: one Alpine.js component, "hawkeye".
 * Pure helpers live in logic.js and server calls in api.js. Job fields are
 * untrusted scraped text: they are only ever rendered with x-text.
 */
import * as L from "./logic.js";
import { ApiError, createApi, takeToken } from "./api.js";

const PAGE_SIZE = 50;
const TOAST_MS = 8000;
const CHROME_URL = "https://www.google.com/chrome/";
const RELEASES_URL = "https://github.com/Abdallahabusnineh/hawkeye-jobs/releases/latest";

const api = createApi(takeToken());

async function loadDictionaries() {
  const load = async (lang) => {
    const response = await fetch(`i18n/${lang}.json`, { cache: "no-store" });
    if (!response.ok) throw new Error(`i18n ${lang}: HTTP ${response.status}`);
    return response.json();
  };
  const [ar, en] = await Promise.all([load("ar"), load("en")]);
  return { ar, en };
}

function hawkeye() {
  return {
    ready: false,
    fatal: false,
    dicts: { ar: {}, en: {} },
    lang: "ar",
    theme: "auto",
    page: "jobs",
    pages: ["jobs", "search", "history", "settings"],
    filters: L.FILTERS,
    sourceOrder: L.SOURCE_ORDER,
    autoHours: L.AUTO_HOURS,
    jobAges: L.JOB_AGE_DAYS,
    themes: ["auto", "light", "dark"],
    chromeUrl: CHROME_URL,
    releasesUrl: RELEASES_URL,
    state: null,
    appSettings: {},
    run: null,
    now: Date.now(),
    toast: null,
    toastQueue: [],
    closing: false,
    countries: [],
    countryLabels: {},
    jobs: { filter: "new", text: "", items: [], total: 0, loading: false },
    search: {
      keywords: [], locations: [], sources: [], keywordInput: "", countryQuery: "", countryOpen: false,
      auto: { enabled: true, hours: 6 }, jobAge: 14, errors: {}, estimate: null, saving: false,
    },
    history: { runs: [], selectedId: null, details: null, loading: false, loaded: false },
    settingsPage: { update: null, checking: false, confirmOpen: false, confirmText: "", busy: false },
    wizard: { step: 1 },
    timers: { refresh: null, text: null, estimate: null, toast: null },
    jobsRequest: 0,
    countriesRequest: 0,
    finishedRunIds: [],
    terminalEvents: 0,
    starting: false,

    async init() {
      // Sent by the window when it is closed during a search: show «Stopping…» until the search ends.
      window.addEventListener("hawkeye-closing", () => { this.closing = true; });
      try {
        this.dicts = await loadDictionaries();
        await this.refreshState();
        this.lang = this.state.language;
        this.theme = this.state.theme;
        this.applyLanguage();
        this.applyTheme();
        await Promise.all([this.loadSearchSettings(), this.loadJobs()]);
        this.ready = true;
        this.connectEvents();
        setInterval(() => { this.now = Date.now(); }, 60000);
      } catch (error) {
        console.error("Hawkeye Jobs could not start", error);
        this.fatal = true;
        return;
      }
      this.loadCountries().catch((error) => this.showError(error));
    },

    // ── text ─────────────────────────────────────────────────────────
    t(key, params) { return L.translate(this.dicts[this.lang], this.dicts.en, key, params); },
    tn(base, n, params = {}) { return this.t(L.pluralKey(this.dicts[this.lang], base, n, this.lang), { n, ...params }); },
    has(key) { return Object.prototype.hasOwnProperty.call(this.dicts[this.lang] || {}, key); },
    applyLanguage() {
      document.documentElement.lang = this.lang;
      document.documentElement.dir = this.lang === "ar" ? "rtl" : "ltr";
    },
    applyTheme() {
      if (this.theme === "auto") delete document.documentElement.dataset.theme;
      else document.documentElement.dataset.theme = this.theme;
    },
    formatDate(iso) {
      const date = new Date(iso);
      if (Number.isNaN(date.getTime())) return "";
      const locale = this.lang === "ar" ? "ar-u-nu-latn" : "en";
      return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(date);
    },
    ageText(iso) {
      const parts = L.ageParts(iso, this.now);
      if (!parts) return "";
      const age = parts.base === "age.now" ? this.t("age.now") : this.tn(parts.base, parts.n);
      return this.t("jobs.found_age", { age });
    },
    sourceName(id) { return this.t(`source.${id}`); },
    sourceShort(id) { return this.t(`source.${id}.short`); },
    jobSourceLabel(job) {
      const id = L.jobSourceId(job.source);
      return id ? this.sourceShort(id) : job.source;
    },
    locationLabel(id) {
      if (!id) return "";
      if (this.countryLabels[id]) return this.countryLabels[id];
      return id === "remote" ? this.t("search.remote_label") : String(id).toUpperCase();
    },
    jobLocation(job) { return job.location_id ? this.locationLabel(job.location_id) : (job.location || ""); },

    // ── state helpers (safe before /api/state has loaded) ────────────
    inWizard() { return !!this.state && !this.state.onboarding_done; },
    wizardStep(n) { return this.ready && this.inWizard() && this.wizard.step === n; },
    showPage(name) { return this.ready && !this.inWizard() && this.page === name; },
    showSearchForm() { return this.showPage("search") || this.wizardStep(3); },
    chromeFound() { return !!(this.state && this.state.chrome && this.state.chrome.found); },
    version() { return this.state ? this.state.version : ""; },
    updateAvailable() { return !!(this.state && this.state.update && this.state.update.available); },
    storeApp() { return !!(this.state && this.state.update && this.state.update.store); },  // Microsoft Store: it updates the app
    count(status) { return (this.state && this.state.job_counts && this.state.job_counts[status]) || 0; },
    go(page) {
      this.page = page;
      if (page === "history") this.loadHistory();
      if (page === "jobs") this.reloadJobs();
    },

    // ── loading ──────────────────────────────────────────────────────
    async refreshState() {
      this.state = await api.get("/api/state");
      this.run = L.fromSnapshot(this.state.run, this.run, Date.now(), this.finishedRunIds);
    },
    async loadCountries() {
      // A language switch can overtake an earlier request; only the latest one may land.
      const request = ++this.countriesRequest;
      const countries = await api.get(`/api/countries?lang=${this.lang}`);
      if (request !== this.countriesRequest) return;
      this.countries = countries;
      this.countryLabels = Object.fromEntries(this.countries.map((c) => [c.id, c.label]));
    },
    async loadSearchSettings() {
      const [saved, app] = await Promise.all([api.get("/api/settings"), api.get("/api/app-settings")]);
      this.appSettings = app;
      this.search.keywords = [...saved.keywords];
      this.search.locations = [...saved.locations];
      this.search.sources = saved.sources.length ? L.orderSources(saved.sources) : L.defaultSources(this.chromeFound());
      this.search.auto = { enabled: !!app.auto_search.enabled, hours: Number(app.auto_search.hours) };
      this.search.jobAge = Number(app.job_age_days) || 14;
      this.requestEstimate();
    },

    // ── live events ──────────────────────────────────────────────────
    connectEvents() {
      const source = api.events();
      // (Re)connected: the server may have dropped us while we were slow or asleep. Resync.
      source.onopen = () => { this.refreshState().catch(() => {}); };
      source.onmessage = (message) => {
        let event;
        try {
          event = JSON.parse(message.data);
        } catch (error) {
          return;
        }
        this.handleEvent(event);
      };
    },
    markFinished(runId) {
      this.terminalEvents += 1;
      if (typeof runId === "number") this.finishedRunIds = [...this.finishedRunIds, runId].slice(-20);
    },
    handleEvent(event) {
      if (event.type === "run_finished") this.markFinished(event.run_id);
      else if (event.type === "run_error") this.markFinished(this.run ? this.run.run_id : null);
      this.run = L.applyEvent(this.run, event);
      switch (event.type) {
        case "query_done":
          if (event.new > 0) this.scheduleRefresh();
          break;
        case "browser_failed":
          this.notify("warn", L.browserNoticeKey(event.reason), {}, event.reason === "chrome_not_found"
            ? { label: "action.download_chrome", run: () => this.openUrl(CHROME_URL) }
            : { label: "action.retry", run: () => this.startSearch() });
          break;
        case "run_finished":
          this.scheduleRefresh(0);
          if (this.history.loaded) this.loadHistory();
          this.notify(event.state === "done" ? "success" : "info", L.runFinishedKey(event.state),
                      { found: event.found, new: event.new });
          break;
        case "run_error":
          this.scheduleRefresh(0);
          this.notify("error", "error.unexpected", {}, { label: "action.report_problem", run: () => this.reportProblem() });
          break;
        case "jobs_reported":
          this.scheduleRefresh(0);
          break;
        default:
          break;
      }
    },
    scheduleRefresh(delay = 1500) {
      if (this.timers.refresh) return;
      this.timers.refresh = setTimeout(async () => {
        this.timers.refresh = null;
        try {
          await this.refreshState();
        } catch (error) {
          return; // the next event retries
        }
        if (this.showPage("jobs")) this.reloadJobs();
      }, delay);
    },

    // ── messages ─────────────────────────────────────────────────────
    notify(kind, key, params = {}, action = null) {
      const { show, queue } = L.queueToast(this.toast, this.toastQueue, { kind, key, params, action });
      this.toastQueue = queue;
      if (show !== this.toast) this.showToast(show);
    },
    showToast(toast) {
      clearTimeout(this.timers.toast);
      this.toast = toast;
      this.timers.toast = setTimeout(() => this.closeToast(), toast.action ? TOAST_MS * 2 : TOAST_MS);
    },
    closeToast() {
      clearTimeout(this.timers.toast);
      this.toast = null;
      const [next, ...rest] = this.toastQueue;
      this.toastQueue = rest;
      if (next) this.showToast(next);
    },
    runToastAction() {
      const action = this.toast && this.toast.action;
      this.closeToast();
      if (action) action.run();
    },
    showError(error) {
      const key = error instanceof ApiError ? L.apiErrorKey(error.status, error.body) : "error.unexpected";
      let action = null;
      if (key === "error.chrome_not_found") action = { label: "action.download_chrome", run: () => this.openUrl(CHROME_URL) };
      else if (key === "error.unexpected") action = { label: "action.report_problem", run: () => this.reportProblem() };
      this.notify("error", key, {}, action);
    },

    // ── jobs ─────────────────────────────────────────────────────────
    async loadJobs(append = false) {
      const offset = append ? this.jobs.items.length : 0;
      const limit = append ? PAGE_SIZE : Math.min(200, Math.max(PAGE_SIZE, this.jobs.items.length));
      const request = ++this.jobsRequest;
      this.jobs.loading = true;
      try {
        const data = await api.get(L.jobsQuery({ filter: this.jobs.filter, text: this.jobs.text, offset, limit }));
        if (request !== this.jobsRequest) return;
        this.jobs.items = append ? [...this.jobs.items, ...data.jobs] : data.jobs;
        this.jobs.total = data.total;
      } catch (error) {
        if (request !== this.jobsRequest) return; // superseded: a newer request owns the list
        throw error;
      } finally {
        if (request === this.jobsRequest) this.jobs.loading = false;
      }
    },
    reloadJobs() { this.loadJobs().catch((error) => this.showError(error)); },
    loadMore() { this.loadJobs(true).catch((error) => this.showError(error)); },
    setFilter(filter) {
      this.jobs.filter = filter;
      this.jobs.items = [];
      this.reloadJobs();
    },
    onJobsText() {
      clearTimeout(this.timers.text);
      this.timers.text = setTimeout(() => {
        this.jobs.items = [];
        this.reloadJobs();
      }, 300);
    },
    emptyText() {
      return this.jobs.text.trim() ? this.t("jobs.empty.text") : this.t(`jobs.empty.${this.jobs.filter}`);
    },
    async setJobStatus(job, pressed) {
      const previous = job.status;
      const status = L.nextStatus(previous, pressed);
      job.status = status;
      try {
        await api.patch(`/api/jobs/${encodeURIComponent(job.id)}`, { status });
      } catch (error) {
        job.status = previous;
        this.showError(error);
        return;
      }
      this.refreshState().catch(() => {});
      if (status === "dismissed") {
        this.notify("info", "jobs.dismissed_toast", {}, {
          label: "action.undo",
          run: async () => {
            await this.setJobStatus(job, previous);
            this.reloadJobs();
          },
        });
      }
      if (!L.jobVisible(status, this.jobs.filter)) {
        setTimeout(() => {
          const before = this.jobs.items.length;
          this.jobs.items = this.jobs.items.filter((item) => item.id !== job.id);
          if (this.jobs.items.length < before) this.jobs.total = Math.max(0, this.jobs.total - 1);
        }, 300);
      }
    },

    // ── running a search ─────────────────────────────────────────────
    async startSearch() {
      if (this.run || this.starting) return;
      this.starting = true;
      const terminalBefore = this.terminalEvents;
      let data = null;
      try {
        data = await api.post("/api/runs");
      } catch (error) {
        if (error instanceof ApiError && error.body.error === "no_settings") {
          this.page = "search";
          this.notify("warn", "error.no_settings");
          return;
        }
        this.showError(error);
        return;
      } finally {
        this.starting = false;
      }
      if (!this.run && this.terminalEvents === terminalBefore) {
        const placeholder = L.startingRun();
        this.run = data && typeof data.run_id === "number" ? { ...placeholder, run_id: data.run_id } : placeholder;
        setTimeout(() => this.scheduleRefresh(0), L.STARTING_TTL_MS + 1000);
      }
      this.scheduleRefresh(0); // the real snapshot (total/done) arrives even if run_started was missed
      this.go("jobs");
    },
    async stopSearch() {
      if (!this.run) return;
      this.run = { ...this.run, stopping: true };
      try {
        await api.post("/api/runs/current/cancel");
      } catch (error) {
        if (error instanceof ApiError && error.body.error === "not_running") {
          this.run = null;
          return;
        }
        if (this.run) this.run = { ...this.run, stopping: false };
        this.showError(error);
      }
    },
    bannerTitle() {
      if (!this.run) return "";
      if (this.run.stopping) return this.t("run.stopping");
      if (this.run.run_id === null || !this.run.total) return this.t("banner.starting");
      return this.t("banner.progress", { done: this.run.done, total: this.run.total });
    },
    bannerNow() {
      const current = this.run && this.run.current;
      if (!current) return "";
      const params = { source: this.sourceShort(current.source), keyword: current.keyword,
                       location: this.locationLabel(current.location_id) };
      return this.t(current.location_id ? "banner.now" : "banner.now_nolocation", params);
    },
    progress() { return L.progressPercent(this.run); },

    // ── search form ──────────────────────────────────────────────────
    commitKeyword() {
      const result = L.addKeyword(this.search.keywords, this.search.keywordInput);
      this.search.keywords = result.list;
      this.setFieldError("keywords", result.error);
      if (!result.error) this.search.keywordInput = "";
      this.requestEstimate();
    },
    keywordKeydown(event) {
      if (event.key === "," || event.key === "،") {
        event.preventDefault();
        this.commitKeyword();
      } else if (event.key === "Backspace" && !this.search.keywordInput && this.search.keywords.length) {
        this.removeKeyword(this.search.keywords.length - 1);
      }
    },
    removeKeyword(index) {
      this.search.keywords = this.search.keywords.filter((_, i) => i !== index);
      this.setFieldError("keywords", null);
      this.requestEstimate();
    },
    countryMatches() { return !this.countries.length ? [] : L.filterCountries(this.countries, this.search.countryQuery, this.search.locations); },
    addLocation(id) {
      if (!this.search.locations.includes(id)) this.search.locations = [...this.search.locations, id];
      this.search.countryQuery = "";
      this.search.countryOpen = false;
      this.setFieldError("locations", null);
      this.requestEstimate();
    },
    pickFirstCountry() {
      const first = this.countryMatches()[0];
      if (first) this.addLocation(first.id);
    },
    removeLocation(id) {
      this.search.locations = this.search.locations.filter((item) => item !== id);
      this.requestEstimate();
    },
    sourceDisabled(id) { return !this.chromeFound() && L.BROWSER_SOURCES.includes(id); },
    toggleSource(id) {
      const has = this.search.sources.includes(id);
      this.search.sources = has ? this.search.sources.filter((item) => item !== id) : L.orderSources([...this.search.sources, id]);
      this.setFieldError("sources", null);
      this.requestEstimate();
    },
    setFieldError(field, code) { this.search.errors = { ...this.search.errors, [field]: code || null }; },
    fieldError(field) {
      const code = this.search.errors[field];
      if (!code) return "";
      const key = `errors.${field}.${code}`;
      return this.has(key) ? this.t(key) : this.t("errors.field.invalid");
    },
    searchPayload() {
      return { keywords: this.search.keywords, locations: this.search.locations, sources: this.search.sources };
    },
    requestEstimate() {
      clearTimeout(this.timers.estimate);
      this.timers.estimate = setTimeout(async () => {
        try {
          const data = await api.post("/api/estimate", this.searchPayload());
          this.search.estimate = data.queries ? data.seconds : null;
        } catch (error) {
          this.search.estimate = null;
        }
      }, 400);
    },
    estimateText() {
      if (this.search.estimate === null) return "";
      return this.tn("estimate.minutes", L.estimateMinutes(this.search.estimate));
    },
    async saveSearch(thenRun = false) {
      if (this.search.keywordInput.trim()) this.commitKeyword();
      this.search.saving = true;
      try {
        const cleaned = await api.put("/api/settings", this.searchPayload());
        this.search.keywords = cleaned.keywords;
        this.search.locations = cleaned.locations;
        this.search.sources = L.orderSources(cleaned.sources);
        this.search.errors = {};
        this.appSettings = await api.patch("/api/app-settings", {
          auto_search: { enabled: !!this.search.auto.enabled, hours: Number(this.search.auto.hours) },
          job_age_days: Number(this.search.jobAge),
        });
        await this.refreshState();
      } catch (error) {
        if (error instanceof ApiError && error.status === 422 && error.body.errors) this.search.errors = error.body.errors;
        else this.showError(error);
        return false;
      } finally {
        this.search.saving = false;
      }
      if (thenRun) await this.startSearch();
      else if (!this.inWizard()) this.notify("success", "search.saved");
      return true;
    },

    // ── history ──────────────────────────────────────────────────────
    async loadHistory() {
      this.history.loading = true;
      try {
        const data = await api.get("/api/runs?limit=50");
        this.history.runs = data.runs;
        this.history.loaded = true;
      } catch (error) {
        this.showError(error);
      } finally {
        this.history.loading = false;
      }
    },
    async selectRun(id) {
      this.history.selectedId = id;
      this.history.details = null;
      try {
        this.history.details = await api.get(`/api/runs/${id}`);
      } catch (error) {
        this.showError(error);
      }
    },
    queryLabel(q) {
      const params = { source: this.sourceName(q.source), keyword: q.keyword, location: this.locationLabel(q.location_id) };
      return this.t(q.location_id ? "history.query_location" : "history.query", params);
    },
    queryOutcomeText(q) {
      const outcome = L.queryOutcome(q);
      return this.t(outcome.key, outcome.params);
    },

    // ── settings ─────────────────────────────────────────────────────
    async patchApp(patch) {
      try {
        this.appSettings = await api.patch("/api/app-settings", patch);
        return true;
      } catch (error) {
        this.showError(error);
        return false;
      }
    },
    async setLanguage(lang) {
      if (!(await this.patchApp({ language: lang }))) return;
      this.lang = lang;
      this.applyLanguage();
      try {
        await this.loadCountries();
      } catch (error) {
        this.showError(error);
      }
    },
    async setTheme(theme) {
      if (await this.patchApp({ theme })) {
        this.theme = theme;
        this.applyTheme();
      }
    },
    backgroundTray() { return !!(this.state && this.state.background && this.state.background.tray); },
    backgroundAutostart() { return !!(this.state && this.state.background && this.state.background.autostart); },
    async setStartAtLogin(on, input) {
      if (!(await this.patchApp({ start_at_login: on }))) input.checked = !on;  // the OS refused: show the real state
    },
    async setUpdateCheck(on) {
      if (await this.patchApp({ update_check: on })) this.settingsPage.update = null;
    },
    async checkUpdate() {
      this.settingsPage.checking = true;
      try {
        this.settingsPage.update = await api.get("/api/update?force=1");
        await this.refreshState();
      } catch (error) {
        this.showError(error);
      } finally {
        this.settingsPage.checking = false;
      }
    },
    updateText() {
      const info = this.settingsPage.update;
      if (!info) return this.updateAvailable() ? this.t("settings.update_available", { version: this.state.update.latest }) : "";
      if (info.disabled) return this.t("settings.update_disabled");
      if (info.available) return this.t("settings.update_available", { version: info.latest });
      return info.latest ? this.t("settings.up_to_date") : this.t("settings.update_unknown");
    },
    async openUrl(url) {
      try {
        await api.post("/api/open-url", { url });
      } catch (error) {
        this.showError(error);
      }
    },
    async openDataFolder() {
      try {
        await api.post("/api/open-data-folder");
      } catch (error) {
        this.showError(error);
      }
    },
    async reportProblem() {
      try {
        await api.post("/api/report-problem"); // the backend opens the issue page itself
        this.notify("info", "settings.report_opened");
      } catch (error) {
        this.notify("error", "error.report_failed");
      }
    },
    openDeleteDialog() {
      this.settingsPage.confirmText = "";
      this.settingsPage.confirmOpen = true;
    },
    closeDeleteDialog() {
      if (!this.settingsPage.confirmOpen) return;
      this.settingsPage.confirmOpen = false;
      this.$nextTick(() => { if (this.$refs.deleteOpener) this.$refs.deleteOpener.focus(); });
    },
    canDelete() { return this.settingsPage.confirmText.trim() === this.t("settings.delete_word"); },
    async resetData() {
      if (!this.canDelete()) return;
      this.settingsPage.busy = true;
      try {
        await api.post("/api/reset", { confirm: "DELETE" });
      } catch (error) {
        this.showError(error);
        return;
      } finally {
        this.settingsPage.busy = false;
      }
      this.settingsPage.confirmOpen = false;
      this.history = { runs: [], selectedId: null, details: null, loading: false, loaded: false };
      this.jobs.items = [];
      await this.refreshState().catch(() => {});
      this.reloadJobs();
      this.notify("success", "settings.deleted");
    },

    // ── first-run wizard ─────────────────────────────────────────────
    async recheckChrome() {
      try {
        await this.refreshState();
        if (!this.search.sources.some((id) => L.BROWSER_SOURCES.includes(id)) && this.chromeFound()) {
          this.search.sources = L.defaultSources(true);
          this.requestEstimate();
        }
      } catch (error) {
        this.showError(error);
      }
    },
    async wizardNextFromSearch() {
      if (await this.saveSearch(false)) this.wizard.step = 4;
    },
    async finishWizard() {
      if (!(await this.patchApp({ onboarding_done: true }))) return;
      await this.refreshState().catch(() => {});
      if (this.state) this.state.onboarding_done = true;
      this.page = "jobs";
      await this.startSearch();
    },
  };
}

document.addEventListener("alpine:init", () => {
  window.Alpine.data("hawkeye", hawkeye);
});
