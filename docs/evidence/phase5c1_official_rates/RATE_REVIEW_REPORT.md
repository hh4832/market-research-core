# Official Historical SEC / FINRA TAF Rate Review

Historical rate data: **DATA_READY**. Broker-specific SEC trade-to-charge-date mapping: **DATA_PENDING**. No formal research or execution authorization is created.

This review supersedes the source-download blockers in the preserved `../phase5c1_official_rates_20261111/` review. Starting local HEAD was cf4651f6fbe9d857ecc4006ab79a38972f9f53b9; remote review commit 7021bcd5cbef7146db33f8dca427f3d7f3275e82 was fast-forwarded before adding these artifacts. Existing evidence was not overwritten.

## Rates and dates

Intervals are inclusive and clipped to 2017-01-01–2024-12-31. SEC dates below are **official charge dates**, not a claim of universal trade-date applicability. FINRA TAF dates are trade-date rate implementation dates. Announcement/filing dates are separate from implementation dates. SEC 2024 page metadata shows a later migration/review date; the announcement body is dated April 17, 2024.

| Component | From | Through | Announcement / filing / approval | Original effective from | Original rate | Schedule rate / unit | Primary source |
|---|---|---|---|---|---|---|---|
| SEC | 2017-01-01 | 2017-07-03 | 2016-01-07 | 2016-02-16 | 21.80 USD_PER_MILLION_NOTIONAL | 0.0000218 USD_PER_NOTIONAL | [sec-2016-2](https://www.sec.gov/newsroom/press-releases/2016-2) |
| SEC | 2017-07-04 | 2018-05-21 | 2017-05-31 | 2017-07-04 | 23.10 USD_PER_MILLION_NOTIONAL | 0.0000231 USD_PER_NOTIONAL | [sec-2017-111](https://www.sec.gov/newsroom/press-releases/2017-111) |
| SEC | 2018-05-22 | 2019-04-15 | 2018-04-17 | 2018-05-22 | 13.00 USD_PER_MILLION_NOTIONAL | 0.000013 USD_PER_NOTIONAL | [sec-2018-67](https://www.sec.gov/newsroom/press-releases/2018-67) |
| SEC | 2019-04-16 | 2020-02-17 | 2019-03-12 | 2019-04-16 | 20.70 USD_PER_MILLION_NOTIONAL | 0.0000207 USD_PER_NOTIONAL | [sec-2019-30](https://www.sec.gov/newsroom/press-releases/2019-30) |
| SEC | 2020-02-18 | 2021-02-24 | 2020-01-09 | 2020-02-18 | 22.10 USD_PER_MILLION_NOTIONAL | 0.0000221 USD_PER_NOTIONAL | [sec-2020-7](https://www.sec.gov/newsroom/press-releases/2020-7) |
| SEC | 2021-02-25 | 2022-05-13 | 2021-01-15 | 2021-02-25 | 5.10 USD_PER_MILLION_NOTIONAL | 0.0000051 USD_PER_NOTIONAL | [sec-2021-8](https://www.sec.gov/newsroom/press-releases/2021-8) |
| SEC | 2022-05-14 | 2023-02-26 | 2022-04-08 | 2022-05-14 | 22.90 USD_PER_MILLION_NOTIONAL | 0.0000229 USD_PER_NOTIONAL | [sec-2022-60](https://www.sec.gov/newsroom/press-releases/2022-60) |
| SEC | 2023-02-27 | 2024-05-21 | 2023-01-23 | 2023-02-27 | 8.00 USD_PER_MILLION_NOTIONAL | 0.000008 USD_PER_NOTIONAL | [sec-2023-15](https://www.sec.gov/newsroom/press-releases/2023-15) |
| SEC | 2024-05-22 | 2024-12-31 | 2024-04-17 | 2024-05-22 | 27.80 USD_PER_MILLION_NOTIONAL | 0.0000278 USD_PER_NOTIONAL | [sec-2024-2](https://www.sec.gov/rules-regulations/fee-rate-advisories/2024-2) |
| TAF | 2017-01-01 | 2021-12-31 | 2012-06-22 | 2012-07-01 | 0.000119 USD_PER_SHARE | 0.000119 USD_PER_SHARE | [finra-524551](https://www.finra.org/rev/524551) |
| TAF | 2022-01-01 | 2022-12-31 | 2020-10-02 | 2022-01-01 | 0.000130 USD_PER_SHARE | 0.000130 USD_PER_SHARE | [finra-2020-032](https://www.finra.org/sites/default/files/2020-10/SR-FINRA-2020-032.pdf) |
| TAF | 2023-01-01 | 2023-12-31 | 2020-10-02 | 2023-01-01 | 0.000145 USD_PER_SHARE | 0.000145 USD_PER_SHARE | [finra-2020-032](https://www.finra.org/sites/default/files/2020-10/SR-FINRA-2020-032.pdf) |
| TAF | 2024-01-01 | 2024-12-31 | 2020-10-02 | 2024-01-01 | 0.000166 USD_PER_SHARE | 0.000166 USD_PER_SHARE | [finra-2020-032](https://www.finra.org/sites/default/files/2020-10/SR-FINRA-2020-032.pdf) |

## Source-to-record review

SEC: each rate-change advisory states both the predecessor rate through the previous day and the new rate on/after its effective charge date. Successor advisories cross-check every boundary. The 2016-2 advisory establishes the predecessor on 2016-02-16; 2016-35 and 2016-206 confirm its continuation. 2017-183 confirms 23.10 continuation; 2020-49, 2021-36 and 2023-41 explicitly confirm no midyear change. 2025-2 confirms 27.80 remains through 2025-05-13, validating the requested terminal boundary. The complete advisory index was retained and checked for intervening transaction-fee advisories; filing fees are excluded.

FINRA: historical revisions 524551 and 1207221 independently show 0.000119 and a 5.95 USD cap, including the July 2017 amendment. SR-FINRA-2020-032 p20 states equity TAF has not increased since 2012, shows current/2021 and 2022/2023/2024 rates and caps. Exhibit 5A p130, 5B p138 and 5C p142 explicitly identify January 1 implementation dates. The predecessor row uses the SEC approval publication date (2012-06-22), not an unretained FINRA notice date. SEC 34-67242 approves the predecessor 0.000119 change and identifies July 1, 2012 implementation. SEC 34-90176 is an immediate-effectiveness **notice**, not an approval order: filing on 2020-10-02 and notice on 2020-10-14 are not 2022–2024 implementation dates. Historical rule revisions 1207251, 1803996, 1803986 and 1880951 confirm the phased rates and the unchanged equity rate across the November 2023 amendment. The current rule page is retained only for revision labels/chronology, never used to infer past numeric rates.

Official equity TAF is on sales and capped at 5.95, 6.49, 7.27 and 8.30 USD respectively (and subject to rule exemptions/low-price conditions). Those sourced terms are retained in rate_review.json. The existing research model intentionally applies TAF on BOTH sides with NO cap plus assumed CAT; it does not claim broker-actual TAF. BASE/LOW/STRESS/CAT_STRESS, Fixed commission and rounding formulas are unchanged.

## Date limitation

SEC Rule 31(a)(3), retained in 34-49928 PDF (Section II.A / Charge Date), uses settlement date for covered sales reported on clearing-agency data, with other definitions for exercises, maturities and other sales. A charge date must not be relabelled as a universal trade date. The unchanged dated_costs.py contract selects by America/New_York execution/fill date. This Schedule therefore applies the verified official charge-date table using the existing **research convention**. Venue/broker-specific trade-to-charge-date conversion is DATA_PENDING and is not fabricated. Historical-rate DATA_READY is not broker-actual cost readiness or Phase 5-C1 execution readiness.

## Integrity and validation

- SEC official charge-date coverage 2017–2024: PASS; nine intervals.
- FINRA equity TAF trade-date coverage 2017–2024: PASS; four intervals.
- Original source / actual rate implementation-date review: PASS.
- All 2,922 calendar dates have exactly one rate per component: PASS; no gaps or overlaps.
- SEC USD per million divided by 1,000,000 using Decimal; TAF USD per share unchanged: PASS.
- Every retained original response-body SHA, record SHA and Schedule SHA: PASS.
- Official source bytes required by the existing loader; four scenarios load and all boundaries resolve: PASS.
- Synthetic and full regression tests: PASS — 173 passed, 85% total coverage (Python 3.12); see validation.txt.

Canonical Schedule SHA256 (`dated_costs.schedule_sha256`, field nulled before canonical JSON hash):

`43b6045758760a40ad88d741b1a97a73cfb0b6cf6b2a4a7f1f3b31d5085a4216`

The byte hash of the formatted JSON is separately listed in ARTIFACT_SHA256SUMS. Source Inventory lists every URL, exact response-body digest and content-addressed local copy; HTML/JSON/PDF are original responses, not rendered search results. FINRA revision JSON is the official AJAX response used by its historical version selector. Derived review metadata is separate from original bytes. Rebuild offline with `PYTHONPATH=src python scripts/build_official_cost_schedule.py`.

No ExecutionEngine or Agent changes, official strategy runs, Batch consumption, research authorization, Holdout reads, merge or deployment occurred. Old readiness reports remain historical evidence; this report claims readiness only for official historical rate data, leaving the formal market gate unchanged.
