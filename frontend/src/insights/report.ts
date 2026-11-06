// AG Studio data sources and the Insights report state, built from an `InsightsSummary`.
// Field IDs ('verdicts.count') refer to the sources below.

import type { AgDataSourcesDefinition, AgReportState, AgWidgetLayoutState } from 'ag-studio';
import type { InsightsSummary, Verdict } from '@/api/types';
import { VERDICT_LABELS } from '@/dashboard/labels';
import { MISMATCH_WIDGET_ID } from './mismatch-widget';

// Position on Studio's canvas, in tracks: 24 columns wide.
const at = (xTrack: number, yTrack: number, xSpan: number, ySpan: number): AgWidgetLayoutState => ({ xTrack, yTrack, xSpan, ySpan });
const title = (text: string) => ({ title: { enabled: true, text } });

export function buildData(s: InsightsSummary): AgDataSourcesDefinition {
  return {
    description: 'ProofCheck cases: verdicts for refund and dispute evidence checked against PayPal records.',
    sources: [
      {
        id: 'verdicts',
        description: 'Cases by verdict.',
        data: (Object.keys(s.by_verdict) as Verdict[]).map((v) => ({ verdict: VERDICT_LABELS[v], count: s.by_verdict[v] })),
        fields: [
          { id: 'verdict', name: 'Verdict', format: 'textFormat' },
          { id: 'count', name: 'Cases', format: 'integerFormat' },
        ],
      },
      {
        id: 'totals',
        description: 'One row of totals.',
        data: [{ amountAtRisk: Number(s.amount_at_risk_caught), overrides: s.merchant_overrides }],
        fields: [
          { id: 'amountAtRisk', name: `Amount at risk caught (${s.currency})`, format: 'currencyFormat' },
          { id: 'overrides', name: 'Merchant overrides', format: 'integerFormat' },
        ],
      },
    ],
  };
}

export function buildReport(currency: string): AgReportState {
  const page = {
    id: 'overview',
    widgets: {
      'kpi-cases': {
        type: 'value' as const,
        dataMapping: { value: [{ id: 'verdicts.count', aggregation: 'sum' as const }] },
        format: title('Cases checked'),
      },
      'kpi-at-risk': {
        type: 'value' as const,
        dataMapping: { value: [{ id: 'totals.amountAtRisk', aggregation: 'sum' as const }] },
        format: title(`Amount at risk caught (${currency})`),
      },
      'kpi-overrides': {
        type: 'value' as const,
        dataMapping: { value: [{ id: 'totals.overrides', aggregation: 'sum' as const }] },
        format: title('Merchant overrides'),
      },
      'verdict-mix': {
        type: 'donut-chart' as const,
        dataMapping: { categoryKey: [{ id: 'verdicts.verdict' }], valueKey: [{ id: 'verdicts.count', aggregation: 'sum' as const }] },
        format: title('Verdict mix'),
      },
      [MISMATCH_WIDGET_ID]: { type: MISMATCH_WIDGET_ID, dataMapping: {} },
    },
    widgetLayout: {
      'kpi-cases': at(0, 0, 8, 9),
      'kpi-at-risk': at(8, 0, 8, 9),
      'kpi-overrides': at(16, 0, 8, 9),
      'verdict-mix': at(0, 9, 10, 24),
      [MISMATCH_WIDGET_ID]: at(10, 9, 14, 24),
    },
  };
  // There are no page filters here, so the panel stays closed.
  const panels = { filters: { collapsed: true } };
  return { selectedPageId: page.id, pages: [page], panels } as unknown as AgReportState;
}
