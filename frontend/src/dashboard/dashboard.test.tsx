import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/api/client';
import * as endpoints from '@/api/endpoints';
import type { CaseDetail, CheckResult } from '@/api/types';
import { caseColumns } from './case-columns';
import { CaseDetailPage, ComparisonTable } from './case-detail-page';
import { DecisionPanel } from './decision-panel';
import { sortChecks, VERDICT_LABELS } from './labels';
import { LoginForm, MerchantGate } from './merchant-gate';

afterEach(() => vi.restoreAllMocks());

const check = (over: Partial<CheckResult>): CheckResult => ({
  field: 'amount',
  claimed: '10.00',
  actual: '10.00',
  result: 'match',
  severity: 'info',
  note: 'Amount matches.',
  ...over,
});

const detail = (over: Partial<CaseDetail> = {}): CaseDetail => ({
  id: 'c1',
  source: 'refund',
  status: 'ready',
  verdict: 'NEEDS_REVIEW',
  risk_score: 25,
  amount: '29.24',
  currency: 'USD',
  paypal_order_id: 'ORDER1',
  created_at: '2026-11-05T10:00:00Z',
  version: 3,
  capture_id: 'CAP1',
  refund_id: null,
  evidence_count: 1,
  dispute: null,
  dispute_id: null,
  checks: [],
  forensic_notes: [],
  paypal_record: null,
  draft: { text: 'Your refund is on its way.', grounded: true, unsupported_values: [], edited_by_human: false },
  audit: [{ at: '2026-11-05T10:00:01Z', actor: 'system', action: 'analysis_completed', hash: 'abcdef0123456789' }],
  audit_chain_valid: true,
  ...over,
});

describe('labels', () => {
  it('sorts mismatches first, most severe first', () => {
    const sorted = sortChecks([
      check({ field: 'a', result: 'match' }),
      check({ field: 'b', result: 'unknown', severity: 'major' }),
      check({ field: 'c', result: 'mismatch', severity: 'minor' }),
      check({ field: 'd', result: 'mismatch', severity: 'critical' }),
    ]);
    expect(sorted.map((c) => c.field)).toEqual(['d', 'c', 'b', 'a']);
  });

  it('never uses accusing words', () => {
    expect(Object.values(VERDICT_LABELS).join(' ').toLowerCase()).not.toMatch(/fraud|fake|scam/);
  });

  it('has a verdict column with neutral text and worst-first sorting', () => {
    const verdict = caseColumns.find((c) => c.field === 'verdict');
    const compare = verdict?.comparator as unknown as (a: string | null, b: string | null) => number;
    expect(compare('DOES_NOT_MATCH_RECORDS', 'MATCHES_RECORDS')).toBeLessThan(0);
    expect(compare('MATCHES_RECORDS', null)).toBeLessThan(0);
  });
});

describe('ComparisonTable', () => {
  it('renders model and document text as plain text', () => {
    const { container } = render(
      <ComparisonTable checks={[check({ result: 'mismatch', severity: 'critical', claimed: '<img src=x onerror=alert(1)>', note: '<script>alert(1)</script>' })]} />,
    );
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument();
    expect(container.querySelector('img, script')).toBeNull();
  });
});

describe('DecisionPanel', () => {
  it('needs a second click before anything is sent', async () => {
    const spy = vi.spyOn(endpoints, 'decideCase').mockResolvedValue(detail({ status: 'approved', refund_id: 'R1' }));
    const onChanged = vi.fn();
    render(<DecisionPanel detail={detail()} onChanged={onChanged} />);

    fireEvent.click(screen.getByRole('button', { name: 'Approve refund of $29.24' }));
    expect(spy).not.toHaveBeenCalled();
    expect(screen.getByText(/cannot be undone/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Yes, confirm' }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith('c1', { action: 'approve', version: 3 }));
    expect(onChanged).toHaveBeenCalledWith(expect.objectContaining({ refund_id: 'R1' }));
  });

  it('sends draft_text only when the merchant edited it', async () => {
    const spy = vi.spyOn(endpoints, 'decideCase').mockResolvedValue(detail());
    render(<DecisionPanel detail={detail()} onChanged={vi.fn()} />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Refund note' }), { target: { value: 'A shorter note.' } });
    fireEvent.click(screen.getByRole('button', { name: /Approve refund/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Yes, confirm' }));
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('c1', { action: 'approve', version: 3, draft_text: 'A shorter note.' }),
    );
  });

  it('blocks a note over the limit', () => {
    render(<DecisionPanel detail={detail()} onChanged={vi.fn()} />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Refund note' }), { target: { value: 'x'.repeat(241) } });
    expect(screen.getByRole('button', { name: /Approve refund/ })).toBeDisabled();
  });

  it('says what a dispute approve does and that decline sends nothing', () => {
    render(<DecisionPanel detail={detail({ source: 'dispute', dispute_id: 'PP-D-1' })} onChanged={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Send evidence to PayPal' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Decline' }));
    expect(screen.getByText(/Nothing is sent to PayPal/)).toBeInTheDocument();
  });

  it('reloads the case after a stale version or a PayPal failure', async () => {
    vi.spyOn(endpoints, 'decideCase').mockRejectedValue(new ApiError(409, 'conflict', 'This case changed. Reload it and try again.'));
    const onChanged = vi.fn();
    render(<DecisionPanel detail={detail()} onChanged={onChanged} />);
    fireEvent.click(screen.getByRole('button', { name: /Approve refund/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Yes, confirm' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('This case changed');
    expect(onChanged).toHaveBeenCalledWith();
  });

  it('shows the grounding message and keeps the edit on a 422', async () => {
    vi.spyOn(endpoints, 'decideCase').mockRejectedValue(new ApiError(422, 'validation_error', "'99.00' is not in the PayPal record."));
    render(<DecisionPanel detail={detail()} onChanged={vi.fn()} />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Refund note' }), { target: { value: 'We refunded 99.00.' } });
    fireEvent.click(screen.getByRole('button', { name: /Approve refund/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Yes, confirm' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('99.00');
    expect(screen.getByRole('textbox', { name: 'Refund note' })).toHaveValue('We refunded 99.00.');
  });

  it('is hidden once the case is decided', () => {
    const { container } = render(<DecisionPanel detail={detail({ status: 'approved' })} onChanged={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('CaseDetailPage', () => {
  const renderPage = () =>
    render(
      <MemoryRouter initialEntries={['/merchant/cases/c1']}>
        <Routes>
          <Route path="/merchant/cases/:caseId" element={<CaseDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

  it('shows the verdict, reasons and audit badge', async () => {
    vi.spyOn(endpoints, 'getCase').mockResolvedValue(
      detail({ verdict: 'DOES_NOT_MATCH_RECORDS', checks: [check({ result: 'mismatch', severity: 'critical', note: 'The amount differs.' })] }),
    );
    renderPage();
    expect(await screen.findByText('Does not match records')).toBeInTheDocument();
    expect(screen.getAllByText('The amount differs.').length).toBeGreaterThan(0);
    expect(screen.getByText('Log verified')).toBeInTheDocument();
  });

  it('polls while the case is being analysed', async () => {
    const spy = vi.spyOn(endpoints, 'getCase').mockResolvedValue(detail({ status: 'processing', verdict: null, draft: null }));
    renderPage();
    await vi.waitFor(() => expect(spy.mock.calls.length).toBeGreaterThanOrEqual(2), { timeout: 3000 });
  });
});

describe('MerchantGate', () => {
  it('shows the login form on 401 and the page once signed in', async () => {
    vi.spyOn(endpoints, 'me').mockRejectedValue(new ApiError(401, 'unauthenticated', 'Sign in.'));
    const login = vi.spyOn(endpoints, 'login').mockResolvedValue({ merchant_id: 'm1', csrf_token: 't' });
    render(
      <MemoryRouter initialEntries={['/merchant']}>
        <Routes>
          <Route path="/merchant" element={<MerchantGate />}>
            <Route index element={<p>Protected page</p>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByRole('heading', { name: 'Merchant sign in' })).toBeInTheDocument();
    expect(screen.queryByText('Protected page')).toBeNull();

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'merchant@example.test' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'pw' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByText('Protected page')).toBeInTheDocument();
    expect(login).toHaveBeenCalledWith('merchant@example.test', 'pw');
  });

  it('clears the password after a failed sign in', async () => {
    vi.spyOn(endpoints, 'login').mockRejectedValue(new ApiError(401, 'unauthenticated', 'Incorrect email or password.'));
    render(<LoginForm onSignedIn={vi.fn()} />);
    const password = screen.getByLabelText('Password');
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'merchant@example.test' } });
    fireEvent.change(password, { target: { value: 'wrong' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Incorrect email or password.');
    expect(password).toHaveValue('');
  });
});
