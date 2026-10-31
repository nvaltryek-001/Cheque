import { test, expect } from "@playwright/test";

const fakeFields = {
  bank_name: "Axis Bank",
  account_holder_name: "Demo Payee",
  amount_numeric: "12500.00",
  amount_words: "Twelve Thousand Five Hundred Only",
  account_number: "1234567890",
  ifsc_code: "UTIB0000123",
  cheque_number: "123456",
  cheque_date: "2026-10-30",
  branch_name: "Demo Branch",
  micr_code: "560211001"
};

test("dashboard renders all 10 field labels and clear empty state", async ({ page }) => {
  await page.route("**/api/health", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "healthy" }) }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /AI Cheque Verification/i })).toBeVisible();
  for (const label of ["Bank Name", "Account Holder / Payee Name", "Amount (Numeric)", "Amount (In Words)", "Account Number", "IFSC Code", "Cheque Number", "Cheque Date", "Branch Name", "MICR Code"]) {
    await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
  }
  await expect(page.getByText("0/10 populated")).toBeVisible();
});

test("uploads a cheque and displays 10 backend-returned fields", async ({ page }) => {
  await page.route("**/api/health", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "healthy" }) }));
  await page.route("**/api/upload", async route => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ upload_id: "e2e-upload-1", message: "uploaded" }) });
  });
  await page.route("**/api/process", async route => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "processed", extracted_fields: fakeFields }) });
  });
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "sample-cheque.png",
    mimeType: "image/png",
    buffer: Buffer.from("fake image content")
  });
  await page.getByRole("button", { name: "Analyze Cheque" }).click();
  await expect(page.locator(".field-row").filter({ hasText: "Bank Name" }).locator(".field-value")).toHaveText("Axis Bank");
  await expect(page.locator(".field-row").filter({ hasText: "Account Holder / Payee Name" }).locator(".field-value")).toHaveText("Demo Payee");
  await expect(page.getByText("12,500.00", { exact: true })).toHaveCount(0); // backend string is rendered as returned
  await expect(page.locator(".field-row").filter({ hasText: "Amount (Numeric)" }).locator(".field-value")).toHaveText("12500.00");
  await expect(page.getByText("10/10 populated")).toBeVisible();
});

test("PDF and WhatsApp actions are disabled until analysis result exists", async ({ page }) => {
  await page.route("**/api/health", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "healthy" }) }));
  await page.goto("/");
  await expect(page.getByRole("button", { name: /Download PDF/i })).toBeDisabled();
  await expect(page.getByRole("button", { name: /Share on WhatsApp/i })).toBeDisabled();
});

test("rejects unsupported file extensions", async ({ page }) => {
  await page.route("**/api/health", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "healthy" }) }));
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "not-a-cheque.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("not an image")
  });
  await expect(page.getByRole("status")).toContainText("Unsupported file type");
});