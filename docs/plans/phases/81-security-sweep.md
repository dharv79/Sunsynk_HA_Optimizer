# Phase 81 — Vulnerability and security sweep

**Status:** Done (see the plan index for the merge). **Size:** M, ~50-80k est. **Category:** user request (06/11/2026, out of the idea sweep).

## Goal

A full security review of the integration, its bundled frontend cards, the dashboard generator and the GitHub workflows. Fix every real finding and add tests or CI checks so the fixes stay fixed.

## Scope (checklist)

1. **Credentials and secrets.**
   - The Sunsynk username, password and access token must never reach logs, notifications, the debug stream (`CONF_DATA_REPORT_TARGET`), JSONL records, sensor attributes or the diagnostics download.
   - Check `planning.redact_config` covers every secret-like key (password, token, refresh token, API keys) at every nesting level, including `OptimizerState`.
   - Check the options form never echoes the stored password back.
2. **Sunsynk cloud API (`api.py`).**
   - TLS verification is on everywhere, with no `ssl=False`.
   - Every request has a timeout.
   - Error paths don't log raw response bodies that may hold tokens.
   - RSA login: check key handling and that token refresh failures fail safely.
3. **Octopus public API (`octopus_public.py`).** Same checks: HTTPS only, timeout, no URLs built from unchecked user input.
4. **Inputs.**
   - Service action fields (`services.yaml` plus the handlers): ranges and types, and that writes are refused in monitor mode or while slots are held (`_service_guard`).
   - The options flow: entity-id fields and `tariff_rate_overrides` JSON parsing, which must be safe on bad input and must never `eval`.
   - The datetime and number entities.
   - Make sure no user-controlled string becomes a file path (data dir, dashboard YAML name).
5. **Dashboard generator (`dashboard_installer.py`).** Config strings such as entity ids and the serial end up in YAML and Jinja templates. Check that a hostile or odd value can't break out of the YAML or template and run unexpected template code.
6. **Bundled cards (`www/*.js`).**
   - The cards use `innerHTML` in 7 places. Confirm that only static markup or numbers go through it, and that entity names, states and attributes use `textContent`.
   - Check card config values for injection: colours in `style` attributes and entity ids in markup.
   - `card_resource` must serve only the listed files: no path traversal, and the right content type.
7. **File I/O.**
   - The JSONL data dir and the dashboard YAML are written inside `{config}`.
   - File permissions.
   - The 13-month pruning only deletes the integration's own files.
8. **Dependencies.**
   - `manifest.json` has `requirements: []` and relies on HA's bundled `cryptography`. Confirm that's still true and supported.
   - Pin test-only tools in CI.
9. **GitHub workflows.**
   - Pin `actions/checkout` and `actions/setup-python` to commit SHAs.
   - Keep `permissions` minimal (`release.yml` has `contents: write`; `tests.yml` should be read-only).
   - Check that branch and tag names can't inject into shell lines such as `TAG="${GITHUB_REF_NAME#release/}"` and `gh` calls (quoting).
   - Consider enabling Dependabot for actions, and secret scanning.
10. **Automated scans.**
    - Run `bandit` (Python), `pip-audit` (test deps) and secret scanning over the repo and its history.
    - Add a cheap static check to CI where it pays off.

## Method

Read each area, list the findings with severity (high, medium, low, info), and check each one against the code before fixing it, so none are false positives. Fix high and medium findings in this phase. Fix low ones if they're cheap; otherwise list them in this file. Do-not-break rules 5, 16 and 17 must still hold.

## Acceptance

- A findings table is added to this file: area, finding, severity, fix or reason not fixed.
- Tests cover each fix: redaction of every secret key, rejected bad service inputs, safe override parsing, dashboard escaping, card served-file allow-list.
- The CI changes are in: pinned actions and minimal permissions, plus any static check added.
- py_compile, pyflakes and pytest all pass. The README and docs are updated if behaviour changes.

## Findings (06/11/2026)

| # | Area | Finding | Severity | Fix / reason not fixed |
|---|---|---|---|---|
| 1 | API | `plant_id` and `inverter_sn` went into URL paths unquoted, so a value with `/`, `?` or `..` could send the bearer token to a different route on the Sunsynk host | Medium | `api._seg()` quotes every id; `planning.url_segment` for the probe routes |
| 2 | Secrets | `redact_config` did not look inside lists, so a secret in a list of dicts would reach the diagnostics download | Low | Recurses into lists and tuples |
| 3 | API | Bandit B324: MD5 for the nonce signature | Info (false positive) | The Sunsynk API requires MD5 signing; marked `usedforsecurity=False` |
| 4 | Workflows | Actions pinned to moving tags (`@v4`, `@v5`) | Medium | Pinned to commit SHAs; Dependabot keeps them current |
| 5 | Workflows | `tests.yml` had the default token permissions | Low | `permissions: contents: read` |
| 6 | Workflows | Release tag came from the branch name without a format check (it was already quoted, so no injection) | Low | Refuses anything that isn't `N.N.N` or `N.N.NbN` |
| 7 | CI | Test tools unpinned; no static security scan | Low | Pinned pytest, pyflakes, bandit; bandit (medium+) runs in CI |
| 8 | Secrets | Password, token and username never reach logs, notifications or attributes; the options flow doesn't echo the password; `SunsynkApiError` text holds only error bodies | None | No change |
| 9 | API / Octopus | TLS verification is on everywhere and every request has a timeout | None | No change; a test guards against `ssl=False` |
| 10 | Inputs | Service schemas check types and the 20-100% range; `_service_guard` refuses writes in monitor mode or while slots are held; overrides JSON uses `json.loads` (no `eval`) and junk becomes `{}` | None | Tests added for junk overrides |
| 11 | Files | The dashboard filename uses the HA entry id; the Agile cache path uses a region checked as A-P; pruning deletes only `YYYY-MM.jsonl` | None | No change |
| 12 | Dashboard | The serial, plant id and entity ids are reduced to safe characters before they reach Jinja; the file is written with `json.dumps` | None | No change |
| 13 | Cards | `innerHTML` holds only static markup, constant colours and numbers; names and states use `textContent`; `www/` holds only the two registered cards | None | Tests added |
| 14 | Dependencies | `requirements: []` (HA bundles `cryptography`); `pip-audit` on the test tools is clean; no secrets in git history | None | No change |
| 15 | Config | Bandit B105: the `CONF_PASSWORD = "password"` constant | Info (false positive) | Not a secret |
