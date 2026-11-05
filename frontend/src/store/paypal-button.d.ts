// The v6 `<paypal-button>` web component (https://developer.paypal.com/sdk/js/reference).
import type { DetailedHTMLProps, HTMLAttributes } from 'react';

declare module 'react' {
  namespace JSX {
    interface IntrinsicElements {
      'paypal-button': DetailedHTMLProps<HTMLAttributes<HTMLElement>, HTMLElement> & {
        type?: 'pay' | 'checkout' | 'buynow';
        class?: 'paypal-gold' | 'paypal-blue' | 'paypal-white';
      };
    }
  }
}
