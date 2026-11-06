# Demo video script (under 3 minutes)

Follows `plan.md` §13. Sandbox only. No copyrighted music or third-party trademarks. Publish on YouTube as public, then add the link to the README.

Use the deployed site. If a step needs a slow model call, cut the wait in editing and say so with an on-screen "sped up" label: the rest of the video should be real.

## Before recording

1. **Everything from `docs/deploy.md` §4 passes**, including one full sandbox refund.
2. **Set the AG Studio license key**, or the Insights page shows a watermark.
3. **Reset the data:** `python scripts/demo_reset.py --yes` against the deployed database.
4. **Create the orders on camera or just before.** The refund form only accepts orders paid from the same browser, so seeded orders won't work here. Pay for three orders in the browser you'll record with.
5. **Make the three documents** from those orders (`eval/demo_receipt.py`, run where it can reach the same database):

   ```bash
   python eval/demo_receipt.py --order-id ORDER_1 --variant genuine
   python eval/demo_receipt.py --order-id ORDER_2 --variant tampered
   python eval/demo_receipt.py --order-id ORDER_3 --variant injection
   ```

6. **Open a dispute case:** a real sandbox dispute if spike 3 worked, otherwise `POST /api/demo/disputes` with a paid order. If you use demo mode, say so on screen: nothing is sent to PayPal in that mode.
7. **Check the stat you open with.** Use a figure you can cite, and show the source on screen.

## Shot list

| Time | On screen | What to say |
|---|---|---|
| 0:00–0:20 | Title card, then a sourced statistic on AI-faked receipts | "Refund teams trust the document. AI has made documents cheap to fake, and small merchants can't tell. But a fake receipt can't change PayPal's records." |
| 0:20–0:45 | Store: add to cart, PayPal button, pay as the sandbox buyer. My orders → Request a refund → attach the genuine receipt. Merchant: case shows **Matches records**. Approve, confirm, refund ID appears | "A buyer pays with the PayPal JavaScript SDK, then asks for a refund with a genuine receipt. It matches the records, so the merchant approves and PayPal refunds it." |
| 0:45–1:20 | Second refund with the tampered receipt. Case: **Does not match records**. Move slowly across the Document vs PayPal table | "This one was edited: a changed amount and a fake order ID. It looks real. It doesn't matter, because the comparison is against PayPal's record, not the image." |
| 1:20–1:40 | Third refund with the injection receipt. Show the Hidden instructions row | "This receipt has text telling an AI reviewer to approve it. The model only transcribes into a schema, it has no tools, and code decides. The text is ignored and flagged. The AI reads; the records decide." |
| 1:40–2:05 | Copilot: "Show the tracking for this order", answer with the lookup label. Then "Refund this now": it declines | "The Case Copilot uses the PayPal Agent Toolkit with read tools only. It answers from live data. Asked to refund, it can't: the only path that moves money is the merchant's approve button." |
| 2:05–2:25 | Dispute case, grounded draft, Approve → evidence sent, audit trail with **Log verified** | "Disputes work the same way. A verified webhook opens a case, the draft can only use facts in PayPal's record, and every action lands in a tamper-evident log." |
| 2:25–2:50 | Insights: tiles, verdict mix, mismatch widget. Then the Eval report: false alarms, injection, Copilot writes | "Built with AG Studio. And it's measured: here's the false-alarm rate on genuine receipts, injection robustness, and zero Copilot write actions, on a labelled test set." |
| 2:50–3:00 | Who it's for, repo and demo links | "ProofCheck: for small merchants who can't afford a fraud team. The AI reads, the records decide, a person approves." |

## Rules for the recording

- **Say only what happened.** If a number on screen differs from this script, use the number on screen.
- **Read the eval numbers from the real report**, not from an oracle run. If a target was missed, say so; don't drop the metric.
- **Keep the verdict language neutral.** Say "doesn't match records", never "fraud" or "fake customer", about a buyer.
- **Blank out anything private:** real email addresses, API keys, and browser tabs or bookmarks.
- **Don't name Render Workflows** as running the analysis unless it was turned on (`docs/deploy.md`).

## If something breaks on the day

| Problem | Fallback |
|---|---|
| Model slow or rate limited | Record that shot again later; extraction results are cached per file |
| Sandbox dispute won't open | Demo mode, labelled on screen |
| Copilot rate limit (20 questions per 10 minutes) | Wait, or record that shot separately |
| Insights looks empty | Check that the cases were analysed, and refresh |
