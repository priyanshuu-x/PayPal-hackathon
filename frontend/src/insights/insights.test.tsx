import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/api/client';
import * as endpoints from '@/api/endpoints';
import type { CaseDetail, CaseSummary } from '@/api/types';
import { EvalPage, Report, type EvalReport } from './eval-page';
import { hasData, loadInsights, summarise } from './insights-data';
import { heatmapModel, MismatchHeatmap } from './mismatch-widget';
import { buildData, buildReport } from './report';

afterEach(() => vi.restoreAllMocks());

const summaryCase = (over: Partial<CaseSummary>): CaseSummary => ({
  id: 'c', source: 'refund', status: 'ready', verdict: 'MATCHES_RECORDS', risk_score: 0, amount: '10.00', currency: 'USD',
  paypal_order_id: 'O', created_at: '2026-11-05T10:00:00Z', version: 1, ...over,
});

const detail = (over: Partial<CaseDetail>): CaseDetail => ({
  ...summaryCase({}), capture_id: null, refund_id: null, evidence_count: 0, dispute: null, dispute_id: null, checks: [],
  forensic_notes: [], paypal_record: null, draft: null, audit: [], audit_chain_valid: true, ...over,
});

const mismatch = (field: string) => ({ field, claimed: null, actual: null, result: 'mismatch' as const, severity: 'major' as const, note: '' });

describe('summarise', () => {
  it('counts verdicts, money caught and overrides', () => {
    const s = summarise(
      [
        summaryCase({ verdict: 'DOES_NOT_MATCH_RECORDS', amount: '19.99' }),
        summaryCase({ verdict: 'DOES_NOT_MATCH_RECORDS', amount: '10.01', status: 'declined' }),
        summaryCase({ verdict: 'DOES_NOT_MATCH_RECORDS', amount: '50.00', status: 'approved' }), // paid out anyway
        summaryCase({ verdict: 'MATCHES_RECORDS', status: 'declined' }),
        summaryCase({ verdict: 'NEEDS_REVIEW' }),
        summaryCase({ verdict: null, status: 'processing' }),
      ],
      [],
    );
    expect(s.by_verdict).toEqual({ MATCHES_RECORDS: 1, NEEDS_REVIEW: 1, DOES_NOT_MATCH_RECORDS: 3 });
    expect(s.amount_at_risk_caught).toBe('30.00');
    expect(s.merchant_overrides).toBe(2);
  });

  it('counts mismatch types per day, ignoring matches', () => {
    const s = summarise([], [
      detail({ created_at: '2026-11-05T01:00:00Z', checks: [mismatch('amount'), mismatch('order'), { ...mismatch('date'), result: 'match' }] }),
      detail({ created_at: '2026-11-05T09:00:00Z', checks: [mismatch('amount')] }),
      detail({ created_at: '2026-11-06T09:00:00Z', checks: [mismatch('items')] }),
    ]);
    expect(s.mismatch_types_by_day).toEqual([
      { date: '2026-11-05', field: 'amount', count: 2 },
      { date: '2026-11-05', field: 'order', count: 1 },
      { date: '2026-11-06', field: 'items', count: 1 },
    ]);
  });
});

describe('loadInsights', () => {
  it('uses the summary endpoint when it exists', async () => {
    const summary = summarise([summaryCase({})], []);
    vi.spyOn(endpoints, 'insightsSummary').mockResolvedValue(summary);
    const list = vi.spyOn(endpoints, 'listCases');
    await expect(loadInsights()).resolves.toEqual({ summary, derived: false, truncated: false });
    expect(list).not.toHaveBeenCalled();
  });

  it('works it out from real cases on a 404, following pages', async () => {
    vi.spyOn(endpoints, 'insightsSummary').mockRejectedValue(new ApiError(404, 'not_found', 'Not found'));
    vi.spyOn(endpoints, 'listCases')
      .mockResolvedValueOnce({ items: [summaryCase({ id: 'a' })], next_cursor: '100' })
      .mockResolvedValueOnce({ items: [summaryCase({ id: 'b', verdict: 'NEEDS_REVIEW' })], next_cursor: null });
    vi.spyOn(endpoints, 'getCase').mockResolvedValue(detail({}));
    const result = await loadInsights();
    expect(result.derived).toBe(true);
    expect(result.summary.by_verdict).toMatchObject({ MATCHES_RECORDS: 1, NEEDS_REVIEW: 1 });
  });

  it('passes other failures on', async () => {
    vi.spyOn(endpoints, 'insightsSummary').mockRejectedValue(new ApiError(401, 'unauthenticated', 'Sign in'));
    await expect(loadInsights()).rejects.toMatchObject({ status: 401 });
  });
});

describe('report', () => {
  const summary = summarise([summaryCase({ verdict: 'NEEDS_REVIEW' })], []);

  it('every widget has a position and uses a real field', () => {
    const report = buildReport('USD') as unknown as { pages: { widgets: Record<string, { dataMapping: Record<string, { id: string }[]> }>; widgetLayout: Record<string, unknown> }[] };
    const page = report.pages[0];
    if (!page) throw new Error('no page');
    expect(Object.keys(page.widgetLayout).sort()).toEqual(Object.keys(page.widgets).sort());
    const data = buildData(summary) as unknown as { sources: { id: string; fields: { id: string }[] }[] };
    const known = new Set(data.sources.flatMap((s) => s.fields.map((f) => `${s.id}.${f.id}`)));
    for (const widget of Object.values(page.widgets)) {
      for (const field of Object.values(widget.dataMapping).flat()) expect(known).toContain(field.id);
    }
  });

  it('uses neutral verdict labels', () => {
    expect(JSON.stringify(buildData(summary))).not.toMatch(/fraud/i);
    expect(hasData(summary)).toBe(true);
    expect(hasData(summarise([], []))).toBe(false);
  });
});

describe('mismatch widget', () => {
  const rows = [
    { date: '2026-11-06', field: 'items', count: 1 },
    { date: '2026-11-05', field: 'amount', count: 4 },
    { date: '2026-11-06', field: 'amount', count: 2 },
  ];

  it('orders days and puts the busiest check first', () => {
    const m = heatmapModel(rows);
    expect(m.days).toEqual(['2026-11-05', '2026-11-06']);
    expect(m.fields).toEqual(['amount', 'items']);
    expect(m.cell('items', '2026-11-05')).toBe(0);
    expect(m.max).toBe(4);
  });

  it('renders labels and an empty state', () => {
    render(<MismatchHeatmap rows={rows} />);
    expect(screen.getByText('Amount')).toBeInTheDocument();
    render(<MismatchHeatmap rows={[]} />);
    expect(screen.getByText('No mismatches recorded yet.')).toBeInTheDocument();
  });
});

const report = (over: Partial<EvalReport['meta']> = {}): EvalReport => ({
  meta: { created_at: '2026-11-06T10:00:00Z', split: 'all', llm: 'groq', data_source: 'sandbox', vision_model: 'v', text_model: 't', ...over },
  summary: { cases: 140, verdict_accuracy: 0.9, verdict_accuracy_by_bucket: { genuine: 0.975 }, confusion_matrix: { MATCHES_RECORDS: { MATCHES_RECORDS: 39, NEEDS_REVIEW: 1 } } },
  targets: {
    false_alarm_rate_genuine: { value: 0.05, target: '<= 0.02', met: false },
    latency_p95_s: { value: 9.5, target: '<= 15', met: true },
  },
});

describe('EvalPage', () => {
  it('shows targets, including a missed one', () => {
    render(<Report report={report()} />);
    expect(screen.getByText('False-alarm rate on genuine documents')).toBeInTheDocument();
    expect(screen.getByText('5.0%')).toBeInTheDocument();
    expect(screen.getByText('9.5')).toBeInTheDocument();
    expect(screen.getByText('No')).toBeInTheDocument();
    expect(screen.queryByRole('note')).toBeNull();
  });

  it('labels oracle runs so they are not read as model results', () => {
    render(<Report report={report({ llm: 'oracle' })} />);
    expect(screen.getByRole('note')).toHaveTextContent('not the model');
  });

  it('says when nothing is published', async () => {
    vi.spyOn(endpoints, 'latestEval').mockRejectedValue(new ApiError(404, 'not_found', 'Not found'));
    render(<EvalPage />);
    expect(await screen.findByText('No eval report has been published yet.')).toBeInTheDocument();
  });
});
