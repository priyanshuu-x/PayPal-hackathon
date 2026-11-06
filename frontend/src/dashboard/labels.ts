// Plain-language wording for the merchant UI. Verdict names stay neutral: never an accusation
// (CLAUDE.md rule 11).

import type { CaseSource, CaseStatus, CheckResult, Severity, Verdict } from '@/api/types';

export const VERDICT_LABELS: Record<Verdict, string> = {
  MATCHES_RECORDS: 'Matches records',
  NEEDS_REVIEW: 'Needs review',
  DOES_NOT_MATCH_RECORDS: 'Does not match records',
};

export const VERDICT_STYLES: Record<Verdict, string> = {
  MATCHES_RECORDS: 'bg-green-50 text-pp-success border-pp-success',
  NEEDS_REVIEW: 'bg-amber-50 text-pp-warning border-pp-warning',
  DOES_NOT_MATCH_RECORDS: 'bg-red-50 text-pp-danger border-pp-danger',
};

export const STATUS_LABELS: Record<CaseStatus, string> = {
  new: 'New',
  processing: 'Analysing',
  ready: 'Ready for review',
  approved: 'Approved',
  declined: 'Declined',
  error: 'Analysis failed',
};

export const SOURCE_LABELS: Record<CaseSource, string> = {
  refund: 'Refund request',
  dispute: 'PayPal dispute',
};

const FIELD_LABELS: Record<string, string> = {
  order: 'Order',
  amount: 'Amount',
  date: 'Date',
  items: 'Items',
  merchant: 'Merchant',
  payer: 'Payer',
  shipping: 'Shipping address',
  delivery: 'Delivery',
  embedded_instructions: 'Hidden instructions',
  reused_file: 'Reused file',
  extraction: 'Document',
  evidence: 'Document',
};

export const fieldLabel = (field: string) => FIELD_LABELS[field] ?? field.replaceAll('_', ' ');

const RESULT_ORDER: Record<CheckResult['result'], number> = { mismatch: 0, unknown: 1, match: 2 };
const SEVERITY_ORDER: Record<Severity, number> = { critical: 0, major: 1, minor: 2, info: 3 };

/** Mismatches first, most severe first (plan.md §7.7). The input is not changed. */
export function sortChecks(checks: CheckResult[]): CheckResult[] {
  return [...checks].sort(
    (a, b) => RESULT_ORDER[a.result] - RESULT_ORDER[b.result] || SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity],
  );
}

/** Statuses that change on their own, so the case page polls. */
export const isAnalysing = (status: CaseStatus) => status === 'new' || status === 'processing';

export const REFUND_NOTE_LIMIT = 240;
export const DISPUTE_NOTE_LIMIT = 1500;
