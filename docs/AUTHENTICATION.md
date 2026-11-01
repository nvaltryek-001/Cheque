# Authentication and access control

ChequeVerify AI has two ways to sign in. Both give the same kind of session token and both go through the same
role checks.

| | Sign in by institutional email | Administrator sign-in |
|---|---|---|
| Who it is for | Anyone with an `@saividya.ac.in` mailbox | The built-in `admin` account and accounts an administrator created with a password |
| What the person types | The email address (the part before the `@` is enough), then an 8-digit code that was emailed to them | Username and password |
| Needs a password | No | Yes |
| Needs mail delivery on the server | Yes (`CV_EMAIL_DELIVERY`, see below) | No |
| Can be switched off | Leave `CV_EMAIL_DELIVERY` empty | `CV_PASSWORD_LOGIN=0` |

## Signing in by email

1. The person enters their address, for example `first.last@saividya.ac.in`, or only `first.last`. The screen adds
   the domain.
2. The server mails an 8-digit code to that address. The code works once and expires after 10 minutes.
3. The person types the code. If it is right, they are signed in. The first time, the server creates their account
   automatically.
4. A new account is **awaiting approval**: the person is signed in and sees a "Waiting for approval" page, but the
   server refuses every data request (`403 approval_pending`) until an administrator gives them a role under
   Settings → Users and roles. The page notices the approval within 20 seconds, or on "Check again".

### Which addresses are accepted

Any address whose domain is exactly `saividya.ac.in`. There is no list of permitted people; nothing has to be
registered in advance. The rule is deliberately strict because the domain check is what keeps outsiders out:

* sub-domains (`x@mail.saividya.ac.in`), look-alike domains (`x@evilsaividya.ac.in`, `x@saividya.ac.in.example`) and
  two `@` signs are refused;
* the part before the `@` may contain ASCII letters, digits, `.`, `_` and `-` (so `student.23ds` is fine). Plus
  addressing (`x+tag@…`), spaces, quotes, control characters and non-ASCII characters are refused, so one mailbox
  cannot become several accounts and no header can be injected into the mail;
* upper and lower case are the same address, and there is one account per address.

Other or additional domains can be allowed with `CV_EMAIL_DOMAINS` (comma separated, exact match).

### Why typing the address is not enough

A sign-in that accepted a bare address would let anyone who knows or guesses an address sign in as that person,
including as an administrator, and this application holds cheque images and signature specimens. The emailed code
is the "password-less" part: nobody has to remember or manage a password, but the person must be able to open the
mailbox. The code is the only thing that turns an address into a session.

## What this does and does not protect

It proves that the person **controls the mailbox** of that address. It does not prove who is sitting at the
keyboard. Consequences you should know about:

* Anyone who can read a user's mail can sign in as that user: a compromised mailbox, a shared or forwarded mailbox,
  a mail administrator. The security of the application is therefore bounded by the security of the institution's
  mail accounts (their own sign-in protections, multi-factor authentication on the mail system, and so on).
* A session token is a bearer token: whoever holds it acts as the user until it expires (`CV_TOKEN_MAX_AGE`,
  8 hours by default) or the person signs out. Signing out, disabling an account or changing a role takes effect
  immediately for the next request.
* The mail travels through the mail provider. The code is short-lived and single-use, but a provider that keeps
  copies of messages also keeps the code until it expires.
* Mail delivery is not guaranteed: messages can be delayed or filtered. The screen tells the person to check the
  spam folder and offers a new code after 30 seconds. Keep administrator (password) sign-in available until you
  have confirmed that delivery works for your users.

## Roles and approval

| Role | What the person can do |
|---|---|
| `pending` (awaiting approval) | Nothing except see the waiting page and sign out |
| `auditor` | Read-only access, including the audit log |
| `operator` | Process cheques, verify signatures and review decisions |
| `admin` | Everything, including users, enrollment of signers and specimens, settings, datasets and training |

* **New accounts are `pending` by default** (`CV_EMAIL_DEFAULT_ROLE=pending`). Setting it to `auditor` or
  `operator` makes first-time users active at once with that role; use that only if everyone with an institutional
  mailbox should be able to see (auditor) or process (operator) cheque and signature data. It can never be `admin`.
* **Approving** is giving a role: Settings → Users and roles → choose a role in the Role column. The person's open
  session keeps working and picks the role up on the next check. **Withdrawing** access is choosing "withdraw
  access" for an email account: the account stays but is locked out of all data again. **Disabling** or **deleting**
  works as before.
* **The first administrator.** Either use the built-in `admin` account (administrator sign-in) and approve people
  from there, or list addresses in `CV_ADMIN_EMAILS` (comma separated). A listed address becomes an administrator
  when it signs in and proves the mailbox, if its account is new or still awaiting approval. The list is not a list
  of who may sign in, and it never overrides a role an administrator has set on purpose.
* An administrator cannot make themselves pending, remove their own admin role or disable their own account.
* **Email accounts have no password.** The server refuses to set one (`400`), and the Settings page shows no
  password controls for them. Their stored password is a random value nobody knows.

## Setting up mail delivery

Email sign-in stays **off** until delivery is configured; the sign-in screen then shows only the administrator
form, and the API answers `503 email_signin_unavailable`.

```
CV_EMAIL_DELIVERY=smtp
CV_SMTP_HOST=<your mail server>
CV_SMTP_PORT=587                  # 465 with CV_SMTP_SECURITY=ssl
CV_SMTP_SECURITY=starttls         # starttls (default) | ssl | none (only for a server on this machine)
CV_SMTP_USER=<the mailbox that sends>
CV_SMTP_FROM=<the From address, if different from the user>
CV_SMTP_PASSWORD=<set as a secret in the hosting dashboard>
```

* **Secrets.** `CV_SMTP_PASSWORD` is read from the environment when a message is sent. Never put it in a file in
  the repository, in `render.yaml`, in a commit message, an issue or a chat. On Render, add the variables in the
  dashboard under Environment (a secret there stays hidden); `render.yaml` only switches the delivery mode on.
* **Encryption.** STARTTLS or SSL is required. If STARTTLS is not offered the message is not sent (it is never
  silently sent in clear text), and `none` is refused unless the mail server is `localhost`, `127.0.0.1` or `::1`.
* **Which service.** Any SMTP service your institution or hosting provider allows works. Check your provider's
  sending limits and whether it needs a verified sender address. Free tiers and sender verification are decisions
  for the person who owns that account; this repository does not create or pay for any mail service.
* **Check it** by signing in with your own address and confirming that the message arrives. Delivery to a real mail
  provider has **not** been exercised from the development sandbox (it cannot send mail); the SMTP path is tested
  against a local fake SMTP server, including STARTTLS, refusal of unencrypted remote servers and unreachable
  servers.
* **Local development only:** `CV_EMAIL_DELIVERY=log` writes the code into the server log instead of sending it.
  Anyone who can read that log can sign in as anyone, so never use it on a server other people can reach. The server
  prints a warning at start-up when it is on.

## What limits abuse

| Protection | Setting (default) |
|---|---|
| Code is 8 random digits from the system random generator | fixed |
| Only a keyed hash (HMAC-SHA-256 with the server secret) is stored; the code never appears in a response, the audit log or any log except `log` mode | fixed |
| A code works once, then it is consumed. Asking for a new code retires the old one | fixed |
| Lifetime of a code | `CV_EMAIL_CODE_TTL` (600 s) |
| Wrong guesses before the code is burned | `CV_EMAIL_CODE_ATTEMPTS` (5) |
| Wrong codes also feed the sign-in throttle used for passwords (per client address and per mailbox) | `CV_LOGIN_MAX_ATTEMPTS`, `CV_LOGIN_LOCK_SECONDS` |
| Minimum time between two codes for one address | `CV_EMAIL_RESEND_SECONDS` (30 s) |
| Codes per address per window | `CV_EMAIL_REQUESTS_PER_ADDRESS` (5 per `CV_EMAIL_REQUEST_WINDOW` = 900 s) |
| Codes per client address per window | `CV_EMAIL_REQUESTS_PER_IP` (20 per window) |
| The same answer whether or not an account exists, so the form cannot be used to find out who has one | fixed |
| Failed delivery gives a generic `503`, leaves no usable code behind and logs the reason server-side only | fixed |

The audit log records `auth.email_code_sent`, `auth.email_code_failed` (with a reason such as `wrong_code`),
`user.create` for first sign-ins and `auth.login` with `"method": "email"` or `"password"`. Entries about codes
show the address masked (`fi***@saividya.ac.in`) because nobody is signed in at that point; entries about an account
show its address as they show any user name.

## Things deliberately left for the operator

* Multi-factor authentication, single sign-on with the institution's identity provider, and cookie-based
  sessions are not part of this change. If the institution runs an identity provider that supports OpenID Connect or
  SAML, that is the stronger replacement for mailed codes; it is a separate piece of work and needs the provider's
  details.
* The distributed-guessing limit: the per-address limits do not stop an attacker who is spread over many addresses
  from asking many mailboxes for codes (each person simply receives unexpected mail). A web application firewall or
  a CAPTCHA in front of `/api/auth/email/request` is the usual answer.
* Backups of `/data` contain the user table (addresses, roles). Treat them as personal data.

## Tests

`backend/tests/test_email_login.py` covers the address rule (a matrix of valid and malicious addresses), the full
flow, single use, expiry, attempt limits, enumeration, throttling, secrecy of codes (database, logs and audit log),
roles and approval, the guarantee that every protected endpoint refuses an unapproved account, mail delivery
(including a local SMTP server) and upgrade of an older database.
`backend/tests/browser_check.py --email-log <server log>` drives the same flow in Chromium against a server started
with `CV_EMAIL_DELIVERY=log` and checks that the page's Content-Security-Policy is never violated.
