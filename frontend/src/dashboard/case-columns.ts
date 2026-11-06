// AG Grid column definitions for the case list (kept in their own file, per CLAUDE.md).

import type { ColDef } from 'ag-grid-community';
import type { CaseSummary, Verdict } from '@/api/types';
import { SOURCE_LABELS, STATUS_LABELS, VERDICT_LABELS } from './labels';

const ageOf = (iso: string, now: number): string => {
  const minutes = Math.max(0, Math.floor((now - new Date(iso).getTime()) / 60_000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return hours < 48 ? `${hours} h` : `${Math.floor(hours / 24)} days`;
};

// Worst first when sorted ascending; cases still being analysed come last.
const VERDICT_RANK: Record<Verdict, number> = {
  DOES_NOT_MATCH_RECORDS: 0,
  NEEDS_REVIEW: 1,
  MATCHES_RECORDS: 2,
};
const verdictRank = (v: Verdict | null | undefined) => (v ? VERDICT_RANK[v] : 3);

export const caseColumns: ColDef<CaseSummary>[] = [
  {
    headerName: 'Verdict',
    field: 'verdict',
    valueFormatter: ({ value }) => (value ? VERDICT_LABELS[value as Verdict] : 'Pending'),
    filterValueGetter: ({ data }) => (data?.verdict ? VERDICT_LABELS[data.verdict] : 'Pending'),
    comparator: (a: Verdict | null, b: Verdict | null) => verdictRank(a) - verdictRank(b),
    cellClass: ({ value }) =>
      value === 'DOES_NOT_MATCH_RECORDS' ? 'text-pp-danger font-semibold'
      : value === 'NEEDS_REVIEW' ? 'text-pp-warning font-semibold'
      : value === 'MATCHES_RECORDS' ? 'text-pp-success font-semibold'
      : 'text-pp-muted',
  },
  {
    headerName: 'Status',
    field: 'status',
    valueFormatter: ({ value }) => (value ? STATUS_LABELS[value as CaseSummary['status']] : ''),
    filterValueGetter: ({ data }) => (data ? STATUS_LABELS[data.status] : ''),
  },
  { headerName: 'Amount', field: 'amount', type: 'money' },
  {
    headerName: 'Age',
    field: 'created_at',
    valueFormatter: ({ value }) => (value ? ageOf(value as string, Date.now()) : ''),
    filter: false,
    sort: 'desc',
  },
  {
    headerName: 'Source',
    field: 'source',
    valueFormatter: ({ value }) => (value ? SOURCE_LABELS[value as CaseSummary['source']] : ''),
    filterValueGetter: ({ data }) => (data ? SOURCE_LABELS[data.source] : ''),
  },
  { headerName: 'PayPal order', field: 'paypal_order_id' },
  { headerName: 'Risk', field: 'risk_score', filter: 'agNumberColumnFilter' },
];
