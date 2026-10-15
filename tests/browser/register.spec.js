const { test, expect } = require("@playwright/test");

async function closeExistingShift(page) {
  await expect(page.locator("#workspace")).toBeVisible();
  const session = await (await page.request.get("/api/session")).json();
  const state = await (await page.request.get("/api/state")).json();
  if (state.shift) {
    const result = await page.request.post("/api/shifts/close", {
      headers: { "X-CSRF-Token": session.csrf },
      data: {
        shift_id: state.shift.id,
        counted_cents: Math.max(0, state.shift.expected_cents),
      },
    });
    expect(result.ok()).toBeTruthy();
    await page.reload();
  }
}

test("a cashier can open, sell, return and reconcile a real shift", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page
    .getByLabel("Password", { exact: true })
    .fill("browser-test-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(
    page.getByRole("heading", { name: "The register", exact: true }),
  ).toBeVisible();
  await closeExistingShift(page);
  await page.getByRole("button", { name: "Open your shift" }).click();
  await page.getByRole("button", { name: "Open shift", exact: true }).click();
  await expect(page.getByText("Shift opened.")).toBeVisible();
  await page
    .getByRole("button", { name: "Register", exact: false })
    .first()
    .click();
  await page.getByRole("button", { name: /Add House espresso,/ }).click();
  await page.getByRole("button", { name: /Add Almond croissant,/ }).click();
  await page.getByLabel("Cash received").fill("20.00");
  await page.screenshot({ path: "docs/register.png", fullPage: true });
  await page.getByRole("button", { name: "Complete sale" }).click();
  await expect(
    page.getByRole("heading", { name: "Another good moment." }),
  ).toBeVisible();
  await expect(
    page.getByRole("dialog").getByText("Change to return:"),
  ).toContainText("$12.00");
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page
    .getByRole("button", { name: "Receipts", exact: false })
    .first()
    .click();
  await page.getByRole("button", { name: "Return", exact: true }).click();
  await page
    .getByRole("spinbutton", { name: "Return quantity for House espresso" })
    .fill("1");
  await page.getByLabel("Reason", { exact: true }).fill("Wrong drink");
  await page.getByRole("button", { name: "Record return" }).click();
  await expect(page.getByRole("status")).toContainText(
    "Return recorded: $3.50",
  );
  await page
    .getByRole("button", { name: "Shift & cash", exact: false })
    .first()
    .click();
  await page.getByRole("button", { name: "Count & close shift" }).click();
  await page.getByLabel("Counted cash").fill("104.00");
  await page.getByRole("button", { name: "Close & record difference" }).click();
  await expect(page.getByRole("status")).toContainText(
    "Cash difference: -$0.50",
  );
  await page.screenshot({ path: "docs/reconciliation.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("mobile layout fits and failed tender can be corrected", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page
    .getByLabel("Password", { exact: true })
    .fill("browser-test-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await closeExistingShift(page);
  await page.getByRole("button", { name: "Open your shift" }).click();
  await page.getByRole("button", { name: "Open shift", exact: true }).click();
  await page
    .getByRole("button", { name: "Register", exact: false })
    .first()
    .click();
  await page.getByRole("button", { name: /Add House espresso,/ }).click();
  await page.getByLabel("Cash received").fill("1.00");
  await page.getByRole("button", { name: "Complete sale" }).click();
  await expect(page.getByRole("status")).toContainText("less than the total");
  await page.getByLabel("Cash received").fill("10.00");
  await page.getByRole("button", { name: "Complete sale" }).click();
  await expect(
    page.getByRole("heading", { name: "Another good moment." }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close dialog" }).click();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  );
  expect(overflow).toBe(false);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: "docs/mobile.png", fullPage: true });
});

test("lost response survives a closed shift and a new login without a second sale", async ({
  page,
  context,
}) => {
  await page.goto("/");
  await page
    .getByLabel("Password", { exact: true })
    .fill("browser-test-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(
    page.getByRole("heading", { name: "The register", exact: true }),
  ).toBeVisible();
  await closeExistingShift(page);
  await page.getByRole("button", { name: "Open your shift" }).click();
  await page.getByRole("button", { name: "Open shift", exact: true }).click();
  await page
    .getByRole("button", { name: "Register", exact: false })
    .first()
    .click();
  const before = await (await page.request.get("/api/state")).json();
  await page.getByRole("button", { name: /Add House espresso,/ }).click();
  await page.route(
    "**/api/sales",
    async (route) => {
      const committed = await route.fetch();
      expect(committed.status()).toBe(201);
      await route.abort("failed");
    },
    { times: 1 },
  );
  await page.getByRole("button", { name: "Complete sale" }).click();
  await expect(page.getByRole("status")).toContainText(
    "preserved for a safe retry",
  );
  await closeExistingShift(page);
  await expect(page.getByRole("status")).toContainText(
    "unfinished sale request was restored",
  );
  await context.clearCookies();
  await page.reload();
  await page
    .getByLabel("Password", { exact: true })
    .fill("browser-test-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  const retry = page.getByRole("button", { name: "Retry sale safely" });
  await expect(retry).toBeEnabled();
  await retry.click();
  await expect(
    page.getByRole("heading", { name: "Another good moment." }),
  ).toBeVisible();
  const after = await (await page.request.get("/api/state")).json();
  expect(after.sales.length).toBe(before.sales.length + 1);
  const pid = before.products.find((p) => p.sku === "COF-01").id;
  expect(after.products.find((p) => p.id === pid).stock).toBe(
    before.products.find((p) => p.id === pid).stock - 1,
  );
  expect(after.shift).toBeNull();
  expect(
    await page.evaluate(() => sessionStorage.getItem("shift-ledger.pending")),
  ).toBeNull();
});
