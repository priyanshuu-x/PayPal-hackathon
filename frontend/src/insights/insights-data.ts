// Data for the Insights page. `GET /api/insights/summary` is in the contract but not on the
// backend yet, so on a 404 the same summary is worked out from the merchant's real cases. It is
// never sample data: an empty account shows an empty dashboard.

import { ApiError } from '@/api/client';
import { getCase, insightsSummary, listCases } from '@/api/endpoints';
import type { CaseDetail, CaseSummary, InsightsSummary } from '@/api/types';

const MAX_DETAILED_CASES = 100; // one request each, so stay well under the 240 per minute limit
const PAGE_SIZE = 100;

export interface InsightsLoad {
  summary: InsightsSummary;
  derived: boolean; // worked out in the browser from cases
  truncated: boolean; // mismatch types cover only the newest cases
}

/** Pure, so it can be tested. `details` may cover only some of `cases`. */
export function summarise(cases: CaseSummary[], details: CaseDetail[]): InsightsSummary {
  const summary: InsightsSummary = {
    by_verdict: { MATCHES_RECORDS: 0, NEEDS_REVIEW: 0, DOES_NOT_MATCH_RECORDS: 0 },
    amount_at_risk_caught: '0.00',
    currency: cases.find((c) => c.currency)?.currency ?? 'USD',
    merchant_overrides: 0,
    mismatch_types_by_day: [],
  };
  let caughtCents = 0;
  for (const c of cases) {
    if (c.verdict) summary.by_verdict[c.verdict] += 1;
    // Money the checks flagged and the merchant did not pay out.
    if (c.verdict === 'DOES_NOT_MATCH_RECORDS' && c.status !== 'approved' && c.amount && c.currency === summary.currency) {
      caughtCents += Math.round(Number(c.amount) * 100);
    }
    const overruled =
      (c.verdict === 'DOES_NOT_MATCH_RECORDS' && c.status === 'approved') ||
      (c.verdict === 'MATCHES_RECORDS' && c.status === 'declined');
    if (overruled) summary.merchant_overrides += 1;
  }
  summary.amount_at_risk_caught = (caughtCents / 100).toFixed(2);

  const counts = new Map<string, number>();
  for (const d of details) {
    const date = d.created_at.slice(0, 10);
    for (const check of d.checks) {
      if (check.result !== 'mismatch') continue;
      const key = `${date}|${check.field}`;
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
  }
  summary.mismatch_types_by_day = [...counts.entries()]
    .map(([key, count]) => {
      const [date = '', field = ''] = key.split('|');
      return { date, field, count };
    })
    .sort((a, b) => a.date.localeCompare(b.date) || a.field.localeCompare(b.field));
  return summary;
}

async function allCases(): Promise<CaseSummary[]> {
  const cases: CaseSummary[] = [];
  let cursor: string | null = null;
  do {
    const page = await listCases({ limit: PAGE_SIZE, ...(cursor ? { cursor } : {}) });
    cases.push(...page.items);
    cursor = page.next_cursor;
  } while (cursor);
  return cases;
}

export async function loadInsights(): Promise<InsightsLoad> {
  try {
    return { summary: await insightsSummary(), derived: false, truncated: false };
  } catch (e) {
    if (!(e instanceof ApiError && e.status === 404)) throw e;
  }
  const cases = await allCases();
  const newest = cases.slice(0, MAX_DETAILED_CASES);
  const details = [];
  for (const c of newest) details.push(await getCase(c.id)); // one at a time, for the rate limit
  return { summary: summarise(cases, details), derived: true, truncated: cases.length > newest.length };
}

export const hasData = ({ by_verdict: v }: InsightsSummary) =>
  v.MATCHES_RECORDS + v.NEEDS_REVIEW + v.DOES_NOT_MATCH_RECORDS > 0;
