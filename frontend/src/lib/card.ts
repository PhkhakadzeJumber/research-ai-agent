/**
 * The card form's checks (step C), all in the browser: the full number never leaves it. Only what a receipt shows
 * (brand, last four digits, expiry) is sent with the checkout.
 */

/** The digits of a typed card number. */
export const digits = (text: string) => text.replace(/\D/g, "");

/** "4242424242424242" → "4242 4242 4242 4242" (Amex: 4-6-5). */
export function formatNumber(text: string): string {
  const d = digits(text).slice(0, 19);
  if (brandOf(d) === "Amex") return [d.slice(0, 4), d.slice(4, 10), d.slice(10, 15)].filter(Boolean).join(" ");
  return d.replace(/(.{4})/g, "$1 ").trim();
}

/** "128" → "12 / 8", "1228" → "12 / 28". */
export function formatExpiry(text: string): string {
  const d = digits(text).slice(0, 4);
  return d.length > 2 ? `${d.slice(0, 2)} / ${d.slice(2)}` : d;
}

export function brandOf(number: string): "Visa" | "Mastercard" | "Amex" | "Card" {
  const d = digits(number);
  if (d.startsWith("4")) return "Visa";
  if (/^(5[1-5]|2[2-7])/.test(d)) return "Mastercard";
  if (/^3[47]/.test(d)) return "Amex";
  return "Card";
}

/** The Luhn check every card number passes (catches most typos). */
export function luhn(number: string): boolean {
  const d = digits(number);
  if (d.length < 12) return false;
  let sum = 0;
  for (let i = 0; i < d.length; i++) {
    let n = Number(d[d.length - 1 - i]);
    if (i % 2 === 1) { n *= 2; if (n > 9) n -= 9; }
    sum += n;
  }
  return sum % 10 === 0;
}

/** Whether "MM / YY" is a real month that hasn't passed. */
export function expiryValid(text: string, today: Date = new Date()): boolean {
  const m = /^(0[1-9]|1[0-2]) \/ (\d{2})$/.exec(text);
  if (!m) return false;
  const year = 2000 + Number(m[2]);
  const month = Number(m[1]);
  return year > today.getFullYear() || (year === today.getFullYear() && month >= today.getMonth() + 1);
}

/** What the form may send: nothing that could charge the card. */
export function receiptOf(number: string, expiry: string) {
  return { card_brand: brandOf(number), card_last4: digits(number).slice(-4), card_expiry: expiry };
}
