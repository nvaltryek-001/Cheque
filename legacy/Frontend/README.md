# ChequeVerify AI Frontend

React + TypeScript + Vite + Tailwind CSS dashboard.

## Development
```powershell
npm install
npm run dev
```

Vite proxies `/api` to `http://127.0.0.1:5000`.

## Production build
```powershell
npm run build
```

## E2E tests
```powershell
npx playwright install chromium
npm run test:e2e
```

Playwright tests mock the API to test UI and request handling. They do not prove the real OCR model or real-bank extraction accuracy. Test the live backend separately with representative images from each dataset bank.

## Ten displayed extraction fields
1. Bank Name
2. Account Holder / Payee Name
3. Amount (Numeric)
4. Amount (In Words)
5. Account Number
6. IFSC Code
7. Cheque Number
8. Cheque Date
9. Branch Name
10. MICR Code

The PDF export includes the selected cheque image when browser image decoding succeeds, plus the ten returned field values. WhatsApp sharing opens a prefilled text summary; the PDF must be attached manually in WhatsApp because browsers cannot silently attach a local file.