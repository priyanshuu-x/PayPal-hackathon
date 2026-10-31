// Shapes from docs/api-contract.md. Keep this file in step with the contract: when the contract
// changes, change these types in the same PR (or the next one) and let tsc find the call sites.
// Money is a decimal string with a separate currency; timestamps are ISO 8601 UTC strings.

export type Verdict = 'MATCHES_RECORDS' | 'NEEDS_REVIEW' | 'DOES_NOT_MATCH_RECORDS';
export type CaseStatus = 'new' | 'processing' | 'ready' | 'approved' | 'declined' | 'error';
export type CaseSource = 'refund' | 'dispute';
export type Severity = 'critical' | 'major' | 'minor' | 'info';

export type ErrorCode =
  | 'validation_error'
  | 'unauthenticated'
  | 'forbidden'
  | 'csrf_failed'
  | 'not_found'
  | 'conflict'
  | 'rate_limited'
  | 'upload_rejected'
  | 'upstream_error'
  | 'internal_error';

export interface ErrorBody {
  error: { code: ErrorCode; message: string; case_id?: string };
}

// --- Auth -------------------------------------------------------------------------

export interface Session {
  merchant_id: string;
  csrf_token: string;
}

// --- Demo store -------------------------------------------------------------------

export interface Product {
  id: string;
  name: string;
  price: string;
  currency: string;
  image: string;
}

export interface SdkConfig {
  client_id: string;
  client_token?: string;
}

export interface ShippingAddress {
  name: string;
  address_line_1: string;
  admin_area_2: string;
  admin_area_1: string;
  postal_code: string;
  country_code: string;
}

export interface CreateOrderRequest {
  items: { product_id: string; quantity: number }[];
  shipping: ShippingAddress;
}

export interface CapturedOrder {
  order_id: string;
  capture_id: string;
  status: string;
  amount: string;
  currency: string;
}

export interface OrderItem {
  name: string;
  quantity: number;
  unit_price: string;
}

export interface BuyerOrder extends CapturedOrder {
  created_at: string;
  items: OrderItem[];
}

// --- Cases ------------------------------------------------------------------------

export interface CheckResult {
  field: string;
  claimed: string | null;
  actual: string | null;
  result: 'match' | 'mismatch' | 'unknown';
  severity: Severity;
  /** Plain-language reason. Render as plain text only. */
  note: string;
}

export interface Draft {
  /** Model-written. Render as plain text only. */
  text: string;
  grounded: boolean;
  unsupported_values: string[];
  edited_by_human: boolean;
}

export interface CaseSummary {
  id: string;
  source: CaseSource;
  status: CaseStatus;
  verdict: Verdict | null;
  /** 0-100 */
  risk_score: number | null;
  amount: string | null;
  currency: string | null;
  paypal_order_id: string | null;
  created_at: string;
  version: number;
}

export interface PayPalRecord {
  order_id: string;
  amount: string;
  currency: string;
  created: string;
  items: OrderItem[];
  payer_name: string | null;
  shipping_status: string | null;
  tracking_number: string | null;
}

export interface AuditEntry {
  at: string;
  actor: string;
  action: string;
  hash: string;
}

export interface CaseDetail extends CaseSummary {
  capture_id: string | null;
  dispute_id: string | null;
  checks: CheckResult[];
  forensic_notes: string[];
  paypal_record: PayPalRecord | null;
  draft: Draft | null;
  audit: AuditEntry[];
  audit_chain_valid: boolean;
}

export interface CaseFilters {
  status?: CaseStatus;
  verdict?: Verdict;
  source?: CaseSource;
  limit?: number;
  cursor?: string;
}

export interface CasePage {
  items: CaseSummary[];
  next_cursor: string | null;
}

export interface CaseDecision {
  action: 'approve' | 'decline';
  version: number;
  draft_text?: string;
}

// --- Copilot ----------------------------------------------------------------------

export interface CopilotAnswer {
  /** Model-written. Render as plain text only. */
  answer: string;
  tools_called: string[];
}

export interface CopilotTurn extends CopilotAnswer {
  question: string;
  created_at: string;
}

// --- Insights and eval ------------------------------------------------------------

export interface InsightsSummary {
  by_verdict: Record<Verdict, number>;
  amount_at_risk_caught: string;
  currency: string;
  merchant_overrides: number;
  mismatch_types_by_day: { date: string; field: string; count: number }[];
}
