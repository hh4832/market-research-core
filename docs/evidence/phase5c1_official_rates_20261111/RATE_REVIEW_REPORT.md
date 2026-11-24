# Phase 5-C1 official historical rate review

**DATA_PENDING — no loadable official Schedule and no DATA_READY claim.**

Review date: 2026-11-11. Starting Core HEAD: `cf4651f6fbe9d857ecc4006ab79a38972f9f53b9`.

Official historical advisory text was reviewed through the web tool. Direct SEC and FINRA downloads were rejected by the managed Cloud egress proxy (CONNECT HTTP 403). No original source bytes were acquired. All original-document SHA fields and Schedule SHA are explicitly null. A hash of rendered search text must not be substituted for an original-document SHA. Existing evidence and readiness report are preserved.

## Candidate history (inclusive dates)

The following table is a transcription for review, not a verified/loadable cost schedule. SEC dates are charge dates; TAF dates are trade dates. Coverage is clipped to the requested window; predecessor establishment dates are retained in rate_review.json.

| Component | From | Through | Announcement / filing | Original rate | Converted rate | Source |
|---|---|---|---|---|---|---|
| SEC | 2017-01-01 | 2017-07-03 | 2016-01-07 | 21.80 USD_PER_MILLION_NOTIONAL | 0.0000218 USD_PER_NOTIONAL | [SEC-2016-2](https://www.sec.gov/news/pressrelease/2016-2.html) |
| SEC | 2017-07-04 | 2018-05-21 | 2017-05-31 | 23.10 USD_PER_MILLION_NOTIONAL | 0.0000231 USD_PER_NOTIONAL | [SEC-2017-111](https://www.sec.gov/newsroom/press-releases/2017-111) |
| SEC | 2018-05-22 | 2019-04-15 | 2018-04-17 | 13.00 USD_PER_MILLION_NOTIONAL | 0.000013 USD_PER_NOTIONAL | [SEC-2018-67](https://www.sec.gov/newsroom/press-releases/2018-67) |
| SEC | 2019-04-16 | 2020-02-17 | 2019-03-12 | 20.70 USD_PER_MILLION_NOTIONAL | 0.0000207 USD_PER_NOTIONAL | [SEC-2019-30](https://www.sec.gov/newsroom/press-releases/2019-30) |
| SEC | 2020-02-18 | 2021-02-24 | 2020-01-09 | 22.10 USD_PER_MILLION_NOTIONAL | 0.0000221 USD_PER_NOTIONAL | [SEC-2020-7](https://www.sec.gov/newsroom/press-releases/2020-7) |
| SEC | 2021-02-25 | 2022-05-13 | 2021-01-15 | 5.10 USD_PER_MILLION_NOTIONAL | 0.0000051 USD_PER_NOTIONAL | [SEC-2021-8](https://www.sec.gov/newsroom/press-releases/2021-8) |
| SEC | 2022-05-14 | 2023-02-26 | 2022-04-08 | 22.90 USD_PER_MILLION_NOTIONAL | 0.0000229 USD_PER_NOTIONAL | [SEC-2022-60](https://www.sec.gov/newsroom/press-releases/2022-60) |
| SEC | 2023-02-27 | 2024-05-21 | 2023-01-23 | 8.00 USD_PER_MILLION_NOTIONAL | 0.000008 USD_PER_NOTIONAL | [SEC-2023-15](https://www.sec.gov/newsroom/press-releases/2023-15) |
| SEC | 2024-05-22 | 2024-12-31 | 2024-04-17 | 27.80 USD_PER_MILLION_NOTIONAL | 0.0000278 USD_PER_NOTIONAL | [SEC-2024-2](https://www.sec.gov/rules-regulations/fee-rate-advisories/2024-2) |
| TAF | 2017-01-01 | 2021-12-31 | 2012-06-25 | 0.000119 USD_PER_SHARE | 0.000119 USD_PER_SHARE | [TAF-12-31](https://www.finra.org/rules-guidance/notices/12-31) |
| TAF | 2022-01-01 | 2022-12-31 | 2020-10-02 | 0.000130 USD_PER_SHARE | 0.000130 USD_PER_SHARE | [TAF-2020-032](https://www.finra.org/sites/default/files/2020-10/SR-FINRA-2020-032.pdf) |
| TAF | 2023-01-01 | 2023-12-31 | 2020-10-02 | 0.000145 USD_PER_SHARE | 0.000145 USD_PER_SHARE | [TAF-2020-032](https://www.finra.org/sites/default/files/2020-10/SR-FINRA-2020-032.pdf) |
| TAF | 2024-01-01 | 2024-12-31 | 2020-10-02 | 0.000166 USD_PER_SHARE | 0.000166 USD_PER_SHARE | [TAF-2020-032](https://www.finra.org/sites/default/files/2020-10/SR-FINRA-2020-032.pdf) |

## Review findings

- SEC has **9** distinct rate intervals in the requested window. The 21.80 predecessor became effective 2016-02-16; the 2016-09-30 continuing-resolution advisory and the 2017 successor confirm carry-forward. Successor advisories explicitly state the previous rate through the preceding day.
- SEC conversion uses exact decimal division by 1,000,000. No percentage or basis-point conversion is substituted.
- SEC 2024 advisory body announces 2024-04-17, effective 2024-05-22. The migrated page also displays 2024-07-23 and a later update stamp; these are not effective dates.
- FINRA 12-31 announces 2012-06-25, effective for sales from 2012-07-01. The 2020 filing shows 0.000119 as current/2021 and phased rates for 2022, 2023, 2024. Exhibit 5 explicitly sets January 1 implementation dates. Filing/immediate legal effectiveness on 2020-10-02 is not rate implementation. SEC notice 34-90176 is dated 2020-10-14, published 2020-10-20; it is an immediate-effectiveness notice, not an approval order.
- Official TAF sale-only caps are respectively USD5.95, 6.49, 7.27, 8.30. They are recorded for source review only. Existing BOTH-side/no-cap conservative TAF and assumed CAT remain unchanged.
- Candidate SEC and TAF intervals separately cover every calendar date with no gap or overlap. This structural PASS does not establish verified source coverage.
- Rule 31(a)(3) distinguishes charge date from trade date: clearing-agency-based exchange sales generally use settlement date; other sales may use trade date. Do not relabel SEC charge-date boundaries as universal official trade-date boundaries. The existing dated_costs.py New York fill-date convention is preserved as a research convention; venue/broker-specific charge-date mapping remains pending.
- Current FINRA rule text was used only to locate amendment history, never to backcast rates. Raw review of intervening amendments (including SR-FINRA-2016-027 and 2023-009), complete source retention, and independent source-to-record review remain necessary before PASS.

## Acceptance

| Requirement | Result |
|---|---|
| SEC verified loadable 2017–2024 coverage | DATA_PENDING |
| FINRA TAF verified loadable 2017–2024 coverage | DATA_PENDING |
| Candidate numerical conversion / interval continuity | PASS |
| Every original source / effective-date verification | DATA_PENDING |
| Original Source SHA / Schedule SHA | DATA_PENDING / not created |
| Synthetic and regression tests | See validation.txt |
| DATA_READY | NO |

## Resume requirements

Allow official SEC/FINRA hosts in Cloud network settings; download official documents with TLS verification, retain exact response-body bytes at new content-addressed paths without overwriting prior evidence, record retrieval metadata and SHA256, review historical amendments and date semantics, then construct and validate the unchanged tsm-dated-trading-costs-v1 contract. Seal each rate using rate_record_sha256 and the Schedule using schedule_sha256; load with an independently reviewed expected pin and the exact original-source map. Exercise all four scenarios and every boundary. Do not publish a verified Schedule until these steps pass.

No ExecutionEngine, cost formulas, Agent, market data, Holdout, Batch, authorization or formal gates were changed. This report authorizes no research run.
