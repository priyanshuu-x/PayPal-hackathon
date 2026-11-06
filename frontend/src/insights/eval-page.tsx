// The published eval report (`eval/reports/latest.json`, written by eval/report.py).

import { useEffect, useState } from 'react';
import { ApiError } from '@/api/client';
import { latestEval } from '@/api/endpoints';
import { errorMessage } from '@/store/format';

export interface EvalTarget {
  value: number | null;
  target: string;
  met: boolean | null;
}

export interface EvalReport {
  meta: { created_at: string; split: string; llm: string; data_source: string; vision_model: string; text_model: string };
  summary: {
    cases: number;
    verdict_accuracy: number | null;
    verdict_accuracy_by_bucket: Record<string, number | null>;
    confusion_matrix: Record<string, Record<string, number>>;
    copilot?: { answer_accuracy: number | null; write_attempts: number; write_attempts_executed: number };
  };
  targets: Record<string, EvalTarget>;
}

const TARGET_LABELS: Record<string, string> = {
  key_field_accuracy_clear: 'Key-field accuracy on clear images',
  critical_mismatch_recall: 'Mismatch recall (critical fields)',
  false_alarm_rate_genuine: 'False-alarm rate on genuine documents',
  injection_not_followed: 'Prompt injection not followed',
  injection_reported: 'Prompt injection reported',
  draft_grounded_first_try: 'Grounded drafts, first try',
  latency_p95_s: 'Latency p95 (seconds)',
};

const pct = (v: number | null | undefined) => (v === null || v === undefined ? 'n/a' : `${(v * 100).toFixed(1)}%`);
const label = (key: string) => key.replaceAll('_', ' ');

export function EvalPage() {
  const [report, setReport] = useState<EvalReport | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    latestEval().then(
      (r) => {
        setReport(r as EvalReport);
        setLoading(false);
      },
      (e: unknown) => {
        setMessage(e instanceof ApiError && e.status === 404 ? 'No eval report has been published yet.' : errorMessage(e));
        setLoading(false);
      },
    );
  }, []);

  return (
    <section className="mx-auto w-full max-w-4xl">
      <h1 className="text-[28px] font-semibold text-pp-navy">Eval report</h1>
      {loading && <p className="mt-3 text-sm text-pp-muted">Loading report…</p>}
      {message && <p className="mt-3 text-sm text-pp-muted">{message}</p>}
      {report && <Report report={report} />}
    </section>
  );
}

export function Report({ report }: { report: EvalReport }) {
  const { meta, summary, targets } = report;
  return (
    <>
      <p className="mt-1 text-sm text-pp-muted">
        {summary.cases} labelled cases · {meta.split} split · {meta.data_source} data · run {meta.created_at.slice(0, 10)}
      </p>
      <p className="text-sm text-pp-muted">Extraction: {meta.vision_model} · drafting: {meta.text_model}</p>
      {meta.llm === 'oracle' && (
        <p role="note" className="mt-2 rounded border border-pp-warning bg-white px-3 py-2 text-sm">
          Oracle run: extraction returned the ground truth, so these numbers test the pipeline, not the model.
        </p>
      )}

      <div className="mt-5 rounded-lg border border-pp-border bg-white p-5">
        <h2 className="font-semibold text-pp-navy">Targets</h2>
        <table className="mt-2 w-full text-left text-sm">
          <thead className="text-xs text-pp-muted">
            <tr><th className="py-1 font-semibold">Metric</th><th className="font-semibold">Result</th><th className="font-semibold">Target</th><th className="font-semibold">Met</th></tr>
          </thead>
          <tbody className="divide-y divide-pp-border">
            {Object.entries(targets).map(([key, t]) => (
              <tr key={key}>
                <td className="py-1.5">{TARGET_LABELS[key] ?? label(key)}</td>
                <td>{key.endsWith('_s') ? (t.value ?? 'n/a') : pct(t.value)}</td>
                <td className="text-pp-muted">{t.target}</td>
                <td className={t.met === false ? 'font-semibold text-pp-danger' : t.met ? 'text-pp-success' : 'text-pp-muted'}>
                  {t.met === null ? 'n/a' : t.met ? 'Yes' : 'No'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-5 grid gap-5 md:grid-cols-2">
        <div className="rounded-lg border border-pp-border bg-white p-5">
          <h2 className="font-semibold text-pp-navy">Accuracy by bucket</h2>
          <dl className="mt-2 grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-sm">
            {Object.entries(summary.verdict_accuracy_by_bucket).map(([bucket, value]) => (
              <div key={bucket} className="contents">
                <dt className="text-pp-muted">{label(bucket)}</dt>
                <dd>{pct(value)}</dd>
              </div>
            ))}
          </dl>
        </div>
        <div className="rounded-lg border border-pp-border bg-white p-5">
          <h2 className="font-semibold text-pp-navy">Confusion matrix</h2>
          <p className="text-xs text-pp-muted">Rows: expected. Columns: result.</p>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-right text-xs">
              <tbody className="divide-y divide-pp-border">
                {Object.entries(summary.confusion_matrix).map(([expected, row]) => (
                  <tr key={expected}>
                    <th scope="row" className="py-1 pr-2 text-left font-semibold">{label(expected.toLowerCase())}</th>
                    {Object.entries(row).map(([got, n]) => <td key={got} title={label(got.toLowerCase())} className="px-1">{n}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {summary.copilot && (
        <div className="mt-5 rounded-lg border border-pp-border bg-white p-5 text-sm">
          <h2 className="font-semibold text-pp-navy">Case Copilot</h2>
          <p className="mt-1">Answer accuracy {pct(summary.copilot.answer_accuracy)}. Write attempts: {summary.copilot.write_attempts}, executed: {summary.copilot.write_attempts_executed}.</p>
        </div>
      )}
    </>
  );
}
