import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/api/client';
import * as endpoints from '@/api/endpoints';
import type { CopilotTurn } from '@/api/types';
import { CopilotPanel } from './copilot-panel';

afterEach(() => vi.restoreAllMocks());

const turn = (over: Partial<CopilotTurn> = {}): CopilotTurn => ({
  question: 'What was the total?',
  answer: 'The total was 29.24 USD.',
  tools_called: ['get_order_details'],
  created_at: '2026-11-06T10:00:00Z',
  ...over,
});

const type = (text: string) => fireEvent.change(screen.getByRole('textbox', { name: 'Ask the Copilot' }), { target: { value: text } });
const send = () => fireEvent.click(screen.getByRole('button', { name: 'Ask' }));

describe('CopilotPanel', () => {
  it('shows past turns and which lookups were used', async () => {
    vi.spyOn(endpoints, 'copilotHistory').mockResolvedValue([turn()]);
    render(<CopilotPanel caseId="c1" />);
    expect(await screen.findByText('The total was 29.24 USD.')).toBeInTheDocument();
    expect(screen.getByText(/Checked: Order lookup/)).toBeInTheDocument();
  });

  it('says it is read-only', () => {
    vi.spyOn(endpoints, 'copilotHistory').mockResolvedValue([]);
    render(<CopilotPanel caseId="c1" />);
    expect(screen.getByText(/cannot approve, refund or change the verdict/)).toBeInTheDocument();
  });

  it('asks a question and clears the box', async () => {
    vi.spyOn(endpoints, 'copilotHistory').mockResolvedValue([]);
    const spy = vi.spyOn(endpoints, 'askCopilot').mockResolvedValue({ answer: 'Delivered.', tools_called: ['get_shipment_tracking'] });
    render(<CopilotPanel caseId="c1" />);
    type('  Where is it?  ');
    send();
    expect(await screen.findByText('Delivered.')).toBeInTheDocument();
    expect(spy).toHaveBeenCalledWith('c1', 'Where is it?');
    expect(screen.getByRole('textbox', { name: 'Ask the Copilot' })).toHaveValue('');
  });

  it('sends a suggestion with one click', async () => {
    vi.spyOn(endpoints, 'copilotHistory').mockResolvedValue([]);
    const spy = vi.spyOn(endpoints, 'askCopilot').mockResolvedValue({ answer: 'Shipped.', tools_called: [] });
    render(<CopilotPanel caseId="c1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Show the tracking for this order' }));
    expect(await screen.findByText('Shipped.')).toBeInTheDocument();
    expect(spy).toHaveBeenCalledWith('c1', 'Show the tracking for this order');
    expect(screen.getByText(/No PayPal lookup/)).toBeInTheDocument();
  });

  it('renders answers as plain text, never as HTML or Markdown', async () => {
    const hostile = '<img src=x onerror=alert(1)> **bold** [link](http://example.test)';
    vi.spyOn(endpoints, 'copilotHistory').mockResolvedValue([turn({ answer: hostile, question: '<b>hi</b>' })]);
    const { container } = render(<CopilotPanel caseId="c1" />);
    expect(await screen.findByText(hostile)).toBeInTheDocument();
    expect(screen.getByText('<b>hi</b>')).toBeInTheDocument();
    expect(container.querySelector('img, b, a, strong')).toBeNull();
  });

  it('keeps the question and shows the wait on a rate limit', async () => {
    vi.spyOn(endpoints, 'copilotHistory').mockResolvedValue([]);
    vi.spyOn(endpoints, 'askCopilot').mockRejectedValue(new ApiError(429, 'rate_limited', 'Too many questions.', undefined, 60));
    render(<CopilotPanel caseId="c1" />);
    type('Where is it?');
    send();
    expect(await screen.findByRole('alert')).toHaveTextContent('Try again in 60 seconds');
    expect(screen.getByRole('textbox', { name: 'Ask the Copilot' })).toHaveValue('Where is it?');
  });

  it('disables Ask while waiting, so a question is not sent twice', async () => {
    vi.spyOn(endpoints, 'copilotHistory').mockResolvedValue([]);
    let finish: (v: { answer: string; tools_called: [] }) => void = () => undefined;
    const spy = vi.spyOn(endpoints, 'askCopilot').mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    render(<CopilotPanel caseId="c1" />);
    type('Where is it?');
    send();
    expect(await screen.findByText('Checking PayPal…')).toBeInTheDocument();
    send();
    expect(spy).toHaveBeenCalledTimes(1);
    finish({ answer: 'Done.', tools_called: [] });
    await waitFor(() => expect(screen.queryByText('Checking PayPal…')).toBeNull());
  });

  it('still works when history cannot be loaded', async () => {
    vi.spyOn(endpoints, 'copilotHistory').mockRejectedValue(new ApiError(500, 'internal_error', 'Nope'));
    render(<CopilotPanel caseId="c1" />);
    expect(await screen.findByRole('textbox', { name: 'Ask the Copilot' })).toBeEnabled();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
