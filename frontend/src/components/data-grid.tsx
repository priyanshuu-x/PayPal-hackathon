// The shared AG Grid, from the AG Grid + PayPal boilerplate. Column definitions live in their own
// files next to each page (e.g. src/dashboard/case-columns.ts). Money columns use `type: 'money'`,
// which formats the value in the row's `currency`; date columns use the grid's built-in
// `cellDataType: 'dateString'` ('YYYY-MM-DD') or `'dateTimeString'`.

import { useState } from 'react';
import {
  ClientSideRowModelModule,
  ColumnAutoSizeModule,
  DateFilterModule,
  enableDevValidations,
  NumberFilterModule,
  QuickFilterModule,
  TextFilterModule,
  themeQuartz,
  type ColDef,
  type ColTypeDefs,
  type RowClickedEvent,
  type ValueFormatterParams,
} from 'ag-grid-community';
import { AgGridProvider, AgGridReact } from 'ag-grid-react';

if (import.meta.env.DEV) {
  enableDevValidations();
}

// Only the grid features we use: in-memory rows, filters for each column type, the search box
// (quick filter) and sizing columns to their content.
const modules = [
  ClientSideRowModelModule,
  TextFilterModule,
  NumberFilterModule,
  DateFilterModule,
  QuickFilterModule,
  ColumnAutoSizeModule,
];

// Same tokens as the rest of the app (src/styles/globals.css).
const theme = themeQuartz.withParams({
  fontFamily: 'var(--pp-font)',
  accentColor: 'var(--pp-blue)',
  foregroundColor: 'var(--pp-ink)',
  borderColor: 'var(--pp-border)',
  headerFontWeight: 600,
  headerTextColor: 'var(--pp-muted)',
  rowHoverColor: 'var(--pp-highlight)',
});

export type Row = Record<string, unknown>;

// Money arrives from the API as a decimal string; format it in the row's `currency`.
function formatMoney({ value, data }: ValueFormatterParams<Row>): string {
  const amount = typeof value === 'string' ? Number(value) : value;
  if (typeof amount !== 'number' || Number.isNaN(amount) || typeof data?.currency !== 'string') return '';
  return amount.toLocaleString('en-US', { style: 'currency', currency: data.currency });
}

const columnTypes: ColTypeDefs<Row> = {
  money: { valueFormatter: formatMoney, filter: 'agNumberColumnFilter' },
};

const defaultColDef: ColDef<Row> = { filter: true };
const autoSizeStrategy = { type: 'fitCellContents' } as const;

export function DataGrid<T extends Row>({
  rows,
  columns,
  onRowClicked,
}: {
  rows: T[];
  columns: ColDef<T>[];
  onRowClicked?: (row: T) => void;
}) {
  const [search, setSearch] = useState('');

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <input
        type="search"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Search all columns…"
        aria-label="Search all columns"
        className="h-10 w-72 rounded border border-pp-border px-3 text-sm outline-none focus:border-pp-blue"
      />
      <div className="min-h-0 flex-1">
        <AgGridProvider modules={modules}>
          <AgGridReact<T>
            theme={theme}
            rowData={rows}
            columnDefs={columns}
            columnTypes={columnTypes as ColTypeDefs<T>}
            defaultColDef={defaultColDef as ColDef<T>}
            quickFilterText={search}
            autoSizeStrategy={autoSizeStrategy}
            enableCellTextSelection
            onRowClicked={(e: RowClickedEvent<T>) => {
              if (e.data && onRowClicked) onRowClicked(e.data);
            }}
          />
        </AgGridProvider>
      </div>
    </div>
  );
}
