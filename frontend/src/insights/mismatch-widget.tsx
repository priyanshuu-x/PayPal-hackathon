// The custom AG Studio widget: "Mismatch types over time", one row per check and one column per
// day, shaded by how many cases differed from PayPal's record that day.

import type { AgWidgetDefinition } from 'ag-studio-react';
import type { InsightsSummary } from '@/api/types';
import { fieldLabel } from '@/dashboard/labels';

export const MISMATCH_WIDGET_ID = 'mismatch-types-over-time';

type Row = InsightsSummary['mismatch_types_by_day'][number];

export interface HeatmapModel {
  days: string[];
  fields: string[];
  cell: (field: string, day: string) => number;
  max: number;
}

/** Busiest check first, days in order. */
export function heatmapModel(rows: Row[]): HeatmapModel {
  const counts = new Map(rows.map((r) => [`${r.field}|${r.date}`, r.count]));
  const totals = new Map<string, number>();
  for (const r of rows) totals.set(r.field, (totals.get(r.field) ?? 0) + r.count);
  return {
    days: [...new Set(rows.map((r) => r.date))].sort(),
    fields: [...totals.keys()].sort((a, b) => (totals.get(b) ?? 0) - (totals.get(a) ?? 0) || a.localeCompare(b)),
    cell: (field, day) => counts.get(`${field}|${day}`) ?? 0,
    max: Math.max(0, ...rows.map((r) => r.count)),
  };
}

export function MismatchHeatmap({ rows }: { rows: Row[] }) {
  const { days, fields, cell, max } = heatmapModel(rows);
  if (fields.length === 0) {
    return <p className="p-3 text-sm text-pp-muted">No mismatches recorded yet.</p>;
  }
  return (
    <div className="h-full overflow-auto p-2">
      <p className="mb-2 text-sm font-semibold text-pp-navy">Mismatch types over time</p>
      <table className="border-separate border-spacing-1 text-xs">
        <thead>
          <tr>
            <th scope="col" className="sr-only">Check</th>
            {days.map((d) => <th key={d} scope="col" className="px-1 font-normal text-pp-muted">{d.slice(5)}</th>)}
          </tr>
        </thead>
        <tbody>
          {fields.map((field) => (
            <tr key={field}>
              <th scope="row" className="pr-2 text-left font-semibold whitespace-nowrap">{fieldLabel(field)}</th>
              {days.map((day) => {
                const count = cell(field, day);
                return (
                  <td
                    key={day}
                    title={`${fieldLabel(field)}, ${day}: ${count}`}
                    className="h-6 min-w-8 rounded text-center"
                    style={count ? { background: `color-mix(in srgb, var(--pp-danger) ${Math.round(20 + (count / max) * 80)}%, white)`, color: count / max > 0.5 ? 'white' : 'var(--pp-ink)' } : { background: 'var(--pp-gridline)' }}
                  >
                    {count || ''}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Registered with Studio by `InsightsPage`; the rows come from the page's own data. */
export function mismatchWidget(rows: Row[]): AgWidgetDefinition {
  return {
    id: MISMATCH_WIDGET_ID,
    label: 'Mismatch types over time',
    comp: () => <MismatchHeatmap rows={rows} />,
    form: ({ createDefaults }) => createDefaults({}),
    defaultSize: { width: 12, height: 20 },
    minSize: { width: 6, height: 10 },
  };
}
