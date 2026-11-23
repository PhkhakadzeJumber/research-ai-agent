/**
 * The website in a real browser (step 10d), logged in as the test account (global-setup.ts). Free: no test starts a
 * run. Run with `npm run e2e` while the site is up.
 */
import { expect, test } from "@playwright/test";

import { E2E } from "./global-setup";

test.describe("not logged in", () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test("a visitor is sent to log in, then back where they were going", async ({ page }) => {
    await page.goto("/investigations");
    await expect(page).toHaveURL(/\/login\?next=%2Finvestigations/);
    await page.getByLabel("Email").fill(E2E.email);
    await page.getByLabel("Password").fill("not the password");
    await page.getByRole("button", { name: "Log in →" }).click();
    // (Next.js has its own invisible route announcer with role "alert": pick ours by its text)
    await expect(page.getByRole("alert").filter({ hasText: "The email or the password is wrong." })).toBeVisible();
    await page.getByLabel("Password").fill(E2E.password);
    await page.getByRole("button", { name: "Log in →" }).click();
    await expect(page).toHaveURL(/\/investigations$/);
    await expect(page.getByRole("heading", { name: /Your investigations/ })).toBeVisible();
  });

  test("a forgotten password: the link is asked for without saying whether the account exists", async ({ page }) => {
    await page.goto("/login");
    await page.getByRole("link", { name: "Forgot password?" }).click();
    await expect(page).toHaveURL(/\/forgot-password$/);
    await expect(page.getByRole("heading", { name: "Forgot your password?" })).toBeVisible(); // the log-in form is gone
    await page.getByRole("textbox", { name: "Email" }).fill("nobody-here@example.com");
    await page.getByRole("button", { name: "Send the link →" }).click();
    await expect(page.getByText("If nobody-here@example.com has an account, the link is on its way.")).toBeVisible();
  });

  test("signing up needs the terms accepted", async ({ page }) => {
    await page.goto("/signup");
    await page.getByLabel("Name").fill("Someone");
    await page.getByLabel("Email").fill("someone@example.com");
    await page.getByLabel("Password").fill("a long enough password");
    await expect(page.getByRole("button", { name: "Create account →" })).toBeDisabled();
    await page.getByRole("checkbox").check();
    await expect(page.getByRole("button", { name: "Create account →" })).toBeEnabled();
  });

  test("a verification link that isn't valid says so", async ({ page }) => {
    await page.goto("/verify-email?token=not-a-real-token-at-all-123");
    await expect(page.getByRole("heading", { name: "This link didn't work" })).toBeVisible();
  });

  test("the pricing page is public, with the server's prices", async ({ page }) => {
    await page.goto("/pricing");
    await expect(page.getByRole("heading", { name: /Good research shouldn.t cost a fortune/ })).toBeVisible();
    await expect(page.getByText("Evaluate ideas: 25 credits each")).toBeVisible();
    await expect(page.getByRole("link", { name: "Start free" })).toBeVisible();
    await page.getByRole("button", { name: "Monthly" }).click();
    await expect(page.getByText("$10", { exact: true })).toBeVisible();
  });
});

// the test account is on the free plan (step P4)
test("a free account sees its free evaluation, and discovery locked", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("link", { name: "Your plan and credits" })).toContainText("Free");
  await expect(page.getByText(/Free · \d left this month · 10–15 min|Free evaluation used · 10–15 min/)).toBeVisible();
  await expect(page.getByText("Pro · 60 credits · 25–40 min")).toBeVisible();
  await expect(page.getByLabel("Field")).toBeDisabled();
  await page.getByRole("link", { name: "Upgrade to Pro →" }).click();
  await expect(page).toHaveURL(/\/pricing$/);
  await expect(page.getByText("Your plan")).toBeVisible(); // on the free card
});

test("plan & billing shows the plan and the credit history", async ({ page }) => {
  await page.goto("/profile");
  await page.getByRole("link", { name: "Plan & billing" }).click();
  await expect(page).toHaveURL(/\/profile\/billing$/);
  await expect(page.getByRole("heading", { name: /Your plan and credits/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Credit history" })).toBeVisible();
  await expect(page.getByRole("link", { name: "See Pro →" })).toBeVisible();
});


test("the start page offers both ways in", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Evaluate my idea" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Find me an idea" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Evaluate idea →" })).toBeDisabled(); // nothing typed yet
  await page.getByLabel("Your idea").fill("RAG for Georgian QA with small language models");
  await expect(page.getByRole("button", { name: "Evaluate idea →" })).toBeEnabled();
  await page.getByRole("button", { name: /Know related papers/ }).click();
  await expect(page.getByLabel("Papers you already know (optional)")).toBeVisible();
});

test("the profile's limits are saved", async ({ page }) => {
  await page.goto("/profile");
  const weeks = String(8 + (Date.now() % 20)); // a value that differs from run to run
  await page.getByLabel("Weeks available").fill(weeks);
  // clicking the selected option clears it (by design), so choose another first: works whatever was saved before
  await page.getByRole("radio", { name: "Cluster" }).click();
  await page.getByRole("radio", { name: "Free notebook GPU" }).click();
  await page.getByRole("button", { name: "Save limits" }).click();
  await expect(page.getByRole("status")).toContainText("Saved.");
  await page.reload();
  await expect(page.getByLabel("Weeks available")).toHaveValue(weeks);
  await expect(page.getByRole("radio", { name: "Free notebook GPU" })).toHaveAttribute("aria-checked", "true");
});

test("another account's investigations never show", async ({ page }) => {
  await page.goto("/investigations");
  await expect(page.getByText("Nothing here yet")).toBeVisible(); // the test account has run nothing
  await page.goto("/library");
  const first = page.locator('a[href^="/library/"]').first();
  if (await first.count()) {
    await first.click();
    await expect(page.getByText("Not in any of your investigations.")).toBeVisible();
  }
});

test("the library searches titles and abstracts", async ({ page }) => {
  await page.goto("/library");
  await expect(page.getByRole("heading", { name: /The library/ })).toBeVisible();
  await page.getByRole("button", { name: "Read closely" }).click(); // off: every paper found
  await page.getByLabel("Search the library").fill("zzzz-no-paper-has-this-zzzz");
  await expect(page.getByText("No papers match")).toBeVisible();
});

test("an unknown investigation says so @phone", async ({ page }) => {
  await page.goto("/investigations/00000000-0000-0000-0000-000000000000");
  await expect(page.getByRole("alert").filter({ hasText: "Investigation not found." })).toBeVisible();
});

test("the phone menu opens and navigates @phone", async ({ page, isMobile }) => {
  test.skip(!isMobile, "phones only");
  await page.goto("/");
  await page.getByRole("button", { name: "Menu" }).click();
  await page.locator("#phone-menu").getByRole("link", { name: "Library" }).click();
  await expect(page).toHaveURL(/\/library$/);
  await expect(page.locator("#phone-menu")).toHaveCount(0); // closed after choosing
});

// the test account isn't one of EUREKA_DEMO_ACCOUNTS: checkout is closed for it (step C)
test("checkout says payments aren't open for other accounts", async ({ page }) => {
  await page.goto("/checkout?product=pro_yearly");
  await expect(page.getByRole("heading", { name: "Payments aren't open yet" })).toBeVisible();
  await page.goto("/profile/billing/manage");
  await expect(page.getByRole("heading", { name: "Invoices" })).toBeVisible();
  await expect(page.getByText("No invoices yet.")).toBeVisible();
});
