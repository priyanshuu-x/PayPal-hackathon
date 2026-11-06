import { useEffect, useMemo, useState } from 'react';
import { AgStudio, AgStudioProvider } from 'ag-studio-react';
import { errorMessage } from '@/store/format';
import { hasData, loadInsights, type InsightsLoad } from './insights-data';
import { mismatchWidget } from './mismatch-widget';
import { buildData, buildReport } from './report';
import { proofCheckTheme } from './studio-theme';

const licenseKey = import.meta.env.VITE_AG_STUDIO_LICENSE_KEY as string | undefined;

export function InsightsPage() {
  const [load, setLoad] = useState<InsightsLoad | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadInsights().then(setLoad, (e: unknown) => setError(errorMessage(e)));
  }, []);

  return (
    <section className="flex h-[calc(100dvh-10rem)] min-h-[32rem] flex-col">
      <h1 className="text-[28px] font-semibold text-pp-navy">Insights</h1>
      {error && <p role="alert" className="mt-3 text-sm text-pp-danger">{error}</p>}
      {!load && !error && <p className="mt-3 text-sm text-pp-muted">Loading insights…</p>}
      {load?.truncated && <p className="mt-1 text-xs text-pp-muted">Mismatch types cover the newest 100 cases.</p>}
      {load && !hasData(load.summary) && <p className="mt-3 text-sm text-pp-muted">No analysed cases yet.</p>}
      {load && hasData(load.summary) && <Dashboard load={load} />}
    </section>
  );
}

function Dashboard({ load }: { load: InsightsLoad }) {
  const { data, initialState, widgets } = useMemo(() => {
    const custom = mismatchWidget(load.summary.mismatch_types_by_day);
    return {
      data: buildData(load.summary),
      initialState: buildReport(load.summary.currency),
      widgets: (config: { widgets: readonly unknown[] }) => ({ ...config, widgets: [...config.widgets, custom] }),
    };
  }, [load]);

  return (
    <div className="mt-3 min-h-0 flex-1">
      <AgStudioProvider {...(licenseKey ? { licenseKey } : {})}>
        <AgStudio
          style={{ height: '100%', width: '100%' }}
          theme={proofCheckTheme}
          layout={{ widgetBorderEnabled: true }}
          data={data}
          initialState={initialState}
          // AG Studio's registry types don't cover a widget added at runtime.
          widgets={widgets as never}
          mode="view"
        />
      </AgStudioProvider>
    </div>
  );
}
