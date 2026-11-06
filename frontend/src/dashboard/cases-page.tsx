import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { listCases } from '@/api/endpoints';
import type { CaseSummary } from '@/api/types';
import { DataGrid } from '@/components/data-grid';
import { errorMessage } from '@/store/format';
import { caseColumns } from './case-columns';

const PAGE_SIZE = 100;

export function CasesPage() {
  const navigate = useNavigate();
  const [cases, setCases] = useState<CaseSummary[] | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (cursor?: string) => {
    setLoading(true);
    setError(null);
    try {
      const page = await listCases({ limit: PAGE_SIZE, ...(cursor ? { cursor } : {}) });
      setCases((current) => (cursor && current ? [...current, ...page.items] : page.items));
      setNextCursor(page.next_cursor);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className="flex h-[calc(100dvh-10rem)] min-h-96 flex-col">
      <div className="flex items-baseline justify-between gap-4">
        <h1 className="text-[28px] font-semibold text-pp-navy">Cases</h1>
        <button type="button" onClick={() => void load()} disabled={loading} className="text-sm font-semibold text-pp-blue hover:underline disabled:text-pp-muted">
          Refresh
        </button>
      </div>
      {error && <p role="alert" className="mt-3 text-sm text-pp-danger">{error}</p>}
      {!cases && !error && <p className="mt-3 text-sm text-pp-muted">Loading cases…</p>}
      {cases?.length === 0 && <p className="mt-3 text-sm text-pp-muted">No cases yet. They appear here when a buyer asks for a refund or opens a dispute.</p>}
      {cases && cases.length > 0 && (
        <div className="mt-3 flex min-h-0 flex-1 flex-col">
          <DataGrid
            rows={cases}
            columns={caseColumns}
            onRowClicked={(row) => void navigate(`/merchant/cases/${encodeURIComponent(row.id)}`)}
          />
          {nextCursor && (
            <button type="button" onClick={() => void load(nextCursor)} disabled={loading} className="mt-3 self-start text-sm font-semibold text-pp-blue hover:underline">
              Load more
            </button>
          )}
        </div>
      )}
    </section>
  );
}
