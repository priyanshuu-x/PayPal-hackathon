// Case detail: mismatches first, "document says / PayPal says" side by side. All text here comes
// from models, buyers or PayPal, so it is only ever rendered as React text (CLAUDE.md rule 12).

import { useCallback, useEffect, useState, type ChangeEvent } from 'react';
import { Link, useParams } from 'react-router';
import { attachEvidence, evidenceUrl, getCase, reanalyzeCase } from '@/api/endpoints';
import type { CaseDetail, CheckResult } from '@/api/types';
import { errorMessage, formatDate, formatMoney } from '@/store/format';
import { ACCEPTED_TYPES, MAX_UPLOAD_BYTES } from '@/store/validation';
import { CopilotPanel } from './copilot-panel';
import { DecisionPanel } from './decision-panel';
import { fieldLabel, isAnalysing, SOURCE_LABELS, sortChecks, STATUS_LABELS, VERDICT_LABELS, VERDICT_STYLES } from './labels';

const POLL_MS = 2000; // docs/api-contract.md: no faster than every 2 seconds
const MAX_DISPUTE_DOCUMENTS = 3;

export function CaseDetailPage() {
  const { caseId = '' } = useParams();
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setDetail(await getCase(caseId));
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    }
  }, [caseId]);

  useEffect(() => {
    void load();
  }, [load]);

  const analysing = detail ? isAnalysing(detail.status) : false;
  useEffect(() => {
    if (!analysing) return undefined;
    const timer = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(timer);
  }, [analysing, load]);

  const onChanged = (updated?: CaseDetail) => {
    if (updated) setDetail(updated);
    else void load();
  };

  return (
    <section className="mx-auto w-full max-w-6xl">
      <Link to="/merchant" className="text-sm font-semibold text-pp-blue hover:underline">← Cases</Link>
      {error && <p role="alert" className="mt-4 text-sm text-pp-danger">{error}</p>}
      {!detail && !error && <p className="mt-4 text-sm text-pp-muted">Loading case…</p>}
      {detail && (
        <>
          <Header detail={detail} />
          <VerdictCard detail={detail} onChanged={onChanged} />
          <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
            <div className="flex flex-col gap-5">
              <EvidencePanel detail={detail} onChanged={onChanged} />
              <ForensicNotes notes={detail.forensic_notes} />
            </div>
            <div className="flex flex-col gap-5">
              <ComparisonTable checks={detail.checks} />
              <PayPalRecordCard detail={detail} />
              <DecisionPanel key={detail.id} detail={detail} onChanged={onChanged} />
              <CopilotPanel key={`copilot-${detail.id}`} caseId={detail.id} />
              <AuditTrail detail={detail} />
            </div>
          </div>
        </>
      )}
    </section>
  );
}

function Header({ detail }: { detail: CaseDetail }) {
  return (
    <div className="mt-3 flex flex-wrap items-baseline justify-between gap-2">
      <div>
        <h1 className="text-[28px] font-semibold text-pp-navy">{SOURCE_LABELS[detail.source]}</h1>
        <p className="text-sm text-pp-muted">
          {detail.paypal_order_id ? <>Order <span className="font-mono">{detail.paypal_order_id}</span> · </> : null}
          {formatDate(detail.created_at)}
        </p>
      </div>
      {detail.amount && detail.currency && <span className="text-xl font-semibold">{formatMoney(detail.amount, detail.currency)}</span>}
    </div>
  );
}

function VerdictCard({ detail, onChanged }: { detail: CaseDetail; onChanged: (updated?: CaseDetail) => void }) {
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reasons = sortChecks(detail.checks).filter((c) => c.result === 'mismatch');

  const reanalyze = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await reanalyzeCase(detail.id);
      onChanged();
    } catch (e) {
      setMessage(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const style = detail.verdict ? VERDICT_STYLES[detail.verdict] : 'bg-pp-canvas text-pp-muted border-pp-border';
  return (
    <div className={`mt-4 rounded-lg border p-4 ${style}`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-lg font-semibold">
          {detail.verdict ? VERDICT_LABELS[detail.verdict] : STATUS_LABELS[detail.status]}
          {isAnalysing(detail.status) && '…'}
        </p>
        <div className="flex items-center gap-3 text-sm text-pp-ink">
          <span>{STATUS_LABELS[detail.status]}</span>
          {detail.risk_score !== null && <span>Risk {detail.risk_score}/100</span>}
          {detail.refund_id && <span>Refund <span className="font-mono">{detail.refund_id}</span></span>}
          {(detail.status === 'error' || detail.status === 'ready') && (
            <button type="button" disabled={busy} onClick={() => void reanalyze()} className="font-semibold text-pp-blue hover:underline disabled:text-pp-muted">
              Analyse again
            </button>
          )}
        </div>
      </div>
      {reasons.length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-sm text-pp-ink">
          {reasons.map((c) => <li key={`${c.field}-${c.note}`}>{c.note}</li>)}
        </ul>
      )}
      {message && <p role="alert" className="mt-2 text-sm text-pp-danger">{message}</p>}
    </div>
  );
}

function EvidencePanel({ detail, onChanged }: { detail: CaseDetail; onChanged: (updated?: CaseDetail) => void }) {
  const [unavailable, setUnavailable] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const isDispute = detail.source === 'dispute';
  const canAttach = isDispute && detail.status === 'ready' && detail.evidence_count < MAX_DISPUTE_DOCUMENTS;

  const onFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    if (file.size > MAX_UPLOAD_BYTES || (file.type && !ACCEPTED_TYPES.includes(file.type))) {
      setMessage('Use a PNG, JPEG, WebP or PDF file up to 10 MB.');
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      await attachEvidence(detail.id, file);
      setUnavailable(false);
      onChanged();
    } catch (err) {
      setMessage(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="rounded-lg border border-pp-border bg-white p-5">
      <h2 className="font-semibold text-pp-navy">Evidence</h2>
      {detail.evidence_count > 0 && !unavailable ? (
        <img
          src={`${evidenceUrl(detail.id)}?n=${detail.evidence_count}`}
          alt="The evidence submitted for this case"
          onError={() => setUnavailable(true)}
          className="mt-3 w-full rounded border border-pp-border"
        />
      ) : (
        <p className="mt-2 text-sm text-pp-muted">
          {detail.evidence_count > 0
            ? 'No preview is available for this document.'
            : isDispute
              ? 'No document is attached. PayPal\'s dispute data is what was checked.'
              : 'No document is attached.'}
        </p>
      )}
      {isDispute && <p className="mt-2 text-xs text-pp-muted">{detail.evidence_count} of {MAX_DISPUTE_DOCUMENTS} documents attached. They go to PayPal only when you approve.</p>}
      {canAttach && (
        <label className="mt-3 block text-sm">
          <span className="font-semibold">Attach a document</span>
          <input type="file" accept={ACCEPTED_TYPES.join(',')} disabled={busy} onChange={(e) => void onFile(e)} className="mt-1 block" />
        </label>
      )}
      {message && <p role="alert" className="mt-2 text-sm text-pp-danger">{message}</p>}
    </section>
  );
}

function ForensicNotes({ notes }: { notes: string[] }) {
  if (notes.length === 0) return null;
  return (
    <section className="rounded-lg border border-pp-border bg-white p-5">
      <h2 className="font-semibold text-pp-navy">File notes</h2>
      <p className="mt-1 text-xs text-pp-muted">Weak signals for context. They never decide the verdict.</p>
      <ul className="mt-2 list-disc pl-5 text-sm">
        {notes.map((n) => <li key={n}>{n}</li>)}
      </ul>
    </section>
  );
}

const RESULT_STYLES: Record<CheckResult['result'], string> = {
  mismatch: 'text-pp-danger',
  unknown: 'text-pp-warning',
  match: 'text-pp-success',
};
const RESULT_LABELS: Record<CheckResult['result'], string> = {
  mismatch: 'Differs',
  unknown: 'Unknown',
  match: 'Matches',
};

export function ComparisonTable({ checks }: { checks: CheckResult[] }) {
  return (
    <section className="rounded-lg border border-pp-border bg-white p-5">
      <h2 className="font-semibold text-pp-navy">Document vs PayPal</h2>
      {checks.length === 0 ? (
        <p className="mt-2 text-sm text-pp-muted">No checks yet.</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[32rem] text-left text-sm">
            <thead className="text-xs text-pp-muted">
              <tr>
                <th className="py-2 pr-3 font-semibold">Check</th>
                <th className="py-2 pr-3 font-semibold">Document says</th>
                <th className="py-2 pr-3 font-semibold">PayPal says</th>
                <th className="py-2 font-semibold">Result</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-pp-border align-top">
              {sortChecks(checks).map((c) => (
                <tr key={`${c.field}-${c.note}`}>
                  <td className="py-2 pr-3 font-semibold">{fieldLabel(c.field)}</td>
                  <td className="py-2 pr-3 break-words">{c.claimed ?? '—'}</td>
                  <td className="py-2 pr-3 break-words">{c.actual ?? '—'}</td>
                  <td className="py-2">
                    <span className={`font-semibold ${RESULT_STYLES[c.result]}`}>{RESULT_LABELS[c.result]}</span>
                    {c.result !== 'match' && <p className="text-xs text-pp-muted">{c.note}</p>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function PayPalRecordCard({ detail }: { detail: CaseDetail }) {
  const r = detail.paypal_record;
  if (!r && !detail.dispute) return null;
  const rows: [string, string | null | undefined][] = [
    ['Created', r ? formatDate(r.created) : null],
    ['Payer', r?.payer_name],
    ['Shipping status', r?.shipping_status],
    ['Tracking', r?.tracking_number],
    ['Dispute reason', detail.dispute?.reason],
    ['Dispute status', detail.dispute?.status],
    ['Response due', detail.dispute?.response_due],
  ];
  return (
    <section className="rounded-lg border border-pp-border bg-white p-5">
      <h2 className="font-semibold text-pp-navy">PayPal record</h2>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        {rows.filter(([, v]) => v).map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-pp-muted">{k}</dt>
            <dd className="break-words">{v}</dd>
          </div>
        ))}
      </dl>
      {r && r.items.length > 0 && (
        <ul className="mt-3 border-t border-pp-border pt-2 text-sm">
          {r.items.map((i) => <li key={i.name}>{i.quantity} × {i.name} at {formatMoney(i.unit_price, r.currency)}</li>)}
        </ul>
      )}
    </section>
  );
}

function AuditTrail({ detail }: { detail: CaseDetail }) {
  return (
    <section className="rounded-lg border border-pp-border bg-white p-5">
      <div className="flex items-center justify-between gap-2">
        <h2 className="font-semibold text-pp-navy">Audit trail</h2>
        <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${detail.audit_chain_valid ? 'bg-green-50 text-pp-success' : 'bg-red-50 text-pp-danger'}`}>
          {detail.audit_chain_valid ? 'Log verified' : 'Log does not verify'}
        </span>
      </div>
      <ol className="mt-2 divide-y divide-pp-border text-sm">
        {detail.audit.map((a) => (
          <li key={a.hash} className="flex flex-wrap justify-between gap-2 py-1.5">
            <span>{a.action.replaceAll('_', ' ')} <span className="text-pp-muted">by {a.actor}</span></span>
            <span className="text-xs text-pp-muted">{formatDate(a.at)} · <span className="font-mono">{a.hash.slice(0, 10)}</span></span>
          </li>
        ))}
      </ol>
    </section>
  );
}
