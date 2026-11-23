/** Step C: the card form keeps the card number in the browser and sends only what a receipt shows. */
import { describe, expect, it } from "vitest";

import { brandOf, expiryValid, formatExpiry, formatNumber, luhn, receiptOf } from "@/lib/card";

describe("the card form", () => {
  it("formats as people type", () => {
    expect(formatNumber("4242424242424242")).toBe("4242 4242 4242 4242");
    expect(formatNumber("378282246310005")).toBe("3782 822463 10005"); // Amex: 4-6-5
    expect(formatExpiry("1228")).toBe("12 / 28");
  });
  it("catches typos and past dates", () => {
    expect(luhn("4242 4242 4242 4242")).toBe(true);
    expect(luhn("4242 4242 4242 4241")).toBe(false);
    const today = new Date("2026-11-23");
    expect(expiryValid("11 / 26", today)).toBe(true);
    expect(expiryValid("10 / 26", today)).toBe(false);
    expect(expiryValid("13 / 30", today)).toBe(false);
  });
  it("knows the brand, and sends only brand, last four and expiry", () => {
    expect([brandOf("4242"), brandOf("5555"), brandOf("3782"), brandOf("6011")]).toEqual(["Visa", "Mastercard", "Amex", "Card"]);
    const sent = receiptOf("4242 4242 4242 4242", "12 / 28");
    expect(sent).toEqual({ card_brand: "Visa", card_last4: "4242", card_expiry: "12 / 28" });
    expect(JSON.stringify(sent)).not.toContain("42424242");
  });
});
