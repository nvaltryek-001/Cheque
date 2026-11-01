// How a person is shown in the interface. Accounts created by email sign-in have no full name, so a readable one
// is derived from the address; it is only ever a label, never used to decide anything.
import type { User } from "./api";

/** "student.one@saividya.ac.in" -> "Student One". Parts with digits (batch or branch codes such as "23ds") are left out. */
export function nameFromEmail(email: string): string {
  const parts = (email.split("@")[0] ?? "").split(/[._-]+/).filter(Boolean);
  const words = parts.filter((w) => !/\d/.test(w));
  return (words.length ? words : parts).map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

export function displayName(u: Pick<User, "full_name" | "username" | "email">): string {
  return u.full_name?.trim() || (u.email ? nameFromEmail(u.email) : "") || u.username;
}

export function initials(name: string): string {
  const words = name.split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  return (words.length > 1 ? words[0][0] + words[words.length - 1][0] : words[0].slice(0, 2)).toUpperCase();
}

export const ROLE_LABEL: Record<string, string> = {
  admin: "Administrator", operator: "Operator", auditor: "Auditor", pending: "Awaiting approval",
};
