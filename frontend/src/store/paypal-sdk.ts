// PayPal JS SDK v6, loaded from PayPal's sandbox CDN (CLAUDE.md rule 9: sandbox only, no live
// switch). There is no published types package for v6, so the small surface we use is declared
// here. Source: https://developer.paypal.com/sdk/js/reference (v6) and the v6 set-up guide.

const SANDBOX_SDK_URL = 'https://www.sandbox.paypal.com/web-sdk/v6/core';

export interface ApproveData {
  orderId: string;
  payerId?: string;
}

export interface SdkError {
  code?: string;
  message?: string;
}

export interface OneTimePaymentSession {
  start(options: { presentationMode: 'auto' | 'popup' | 'modal' }, orderPromise: Promise<{ orderId: string }>): Promise<void>;
}

export interface SdkInstance {
  findEligibleMethods(options?: { currencyCode?: string }): Promise<{ isEligible(method: 'paypal'): boolean }>;
  createPayPalOneTimePaymentSession(options: {
    onApprove: (data: ApproveData) => Promise<void> | void;
    onCancel?: (data: { orderId?: string }) => void;
    onError?: (error: SdkError) => void;
  }): OneTimePaymentSession;
}

interface PayPalNamespace {
  createInstance(options: {
    clientToken: string;
    components: ['paypal-payments'];
    pageType: 'checkout';
  }): Promise<SdkInstance>;
}

declare global {
  interface Window {
    paypal?: PayPalNamespace;
  }
}

let loading: Promise<PayPalNamespace> | null = null;

/** Adds the v6 core script once and resolves with `window.paypal`. */
export function loadPayPalSdk(): Promise<PayPalNamespace> {
  if (window.paypal) return Promise.resolve(window.paypal);
  loading ??= new Promise<PayPalNamespace>((resolve, reject) => {
    const script = document.createElement('script');
    script.src = SANDBOX_SDK_URL;
    script.async = true;
    script.onload = () => (window.paypal ? resolve(window.paypal) : reject(new Error('PayPal SDK did not load')));
    script.onerror = () => {
      loading = null;
      script.remove();
      reject(new Error('PayPal SDK did not load'));
    };
    document.head.append(script);
  });
  return loading;
}

/** A v6 instance for one-time PayPal payments, or null when PayPal isn't eligible here. */
export async function createCheckoutInstance(clientToken: string): Promise<SdkInstance | null> {
  const paypal = await loadPayPalSdk();
  const instance = await paypal.createInstance({ clientToken, components: ['paypal-payments'], pageType: 'checkout' });
  const methods = await instance.findEligibleMethods({ currencyCode: 'USD' });
  return methods.isEligible('paypal') ? instance : null;
}
