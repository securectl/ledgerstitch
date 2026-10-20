# LedgerStitch Governance

> Rename to `GOVERNANCE.md` at the repo root at publication. Review it every 6 months, or
> whenever the project gains a second regular maintainer.

## 1. Model: benevolent dictator for now, and said out loud

LedgerStitch is currently maintained by its founder, `{{FOUNDER_NAME}}` (GitHub `@{{FOUNDER_HANDLE}}`).
The founder acts as **BDFL** (benevolent dictator for life, in the usual open-source sense) and
makes the final call on:
- what gets merged, and the roadmap;
- releases, and the security response;
- licensing, and which features go in the Community core versus the paid editions.

We are saying this plainly because pretending a one-person project is run by committee helps
no one. As the project grows, this document will change, and §6 explains how.

The project is also a business. LedgerStitch the company (`{{COMPANY_LEGAL_NAME}}`) sells paid
add-ons and a hosted version, which extend the core through its public plugin API. Those pay for
the maintenance of the open-source core. §3 lists the promises that keep that relationship fair.

## 2. Roles

| Role | Who | Can do |
|---|---|---|
| **User** | Anyone | Use, study, change and share the Community edition under AGPL-3.0. Open issues and discussions |
| **Contributor** | Anyone with a merged PR, doc, bank-format profile, translation or reproducible bug report | Recognized in release notes and `CONTRIBUTORS`. Must sign the CLA for code or docs (see CONTRIBUTING) |
| **Triager** | Invited contributors with a track record of helpful issue triage | Label, close duplicates, request info. Cannot merge |
| **Maintainer** | Today, the founder only. Invited later by the BDFL (see §6) | Review and merge, cut releases, and act on security reports for the areas they own |
| **BDFL** | The founder | Final decision on anything disputed. Owns licensing, trademarks and security disclosure |

## 3. Promises to the community (the open-core charter)

These are commitments, not aspirations. If we ever break one, call us out publicly in an issue.

1. **The Community edition has no usage caps.** It has no limits on accounts, transactions,
   vaults/clients, receipts or exports. We will never add a cap to the AGPL core.
2. **Features only move toward free.** A feature that ships in Community stays in Community. We
   may move paid features into Community. We will never move a Community feature behind a paywall.
3. **Your data is never hostage.** Every edition can export everything it holds in open formats
   (CSV/Excel). The vault format is documented, and the code that reads it is AGPL.
4. **Privacy and security features are never paid features.** Encryption, redaction, the
   "Preview what AI sees" payload view, and offline mode are always free.
5. **No nagware and no telemetry in Community.** The Community edition makes no network calls
   unless the user explicitly starts one. Locked add-on features are shown as quiet labels, not
   pop-ups.
6. **Security fixes reach Community first, or at the same time.** Never later than the paid editions.
7. **No license bait-and-switch for the core.** We don't plan to relicense the AGPL core.
   Code already released under AGPL-3.0 stays available under AGPL-3.0 forever. That is how
   the license works, and we would not try to undo it. The CLA lets the company also offer the
   code under commercial terms (for the paid editions). It does **not**
   take the AGPL version away from anyone.
8. **Public roadmap.** What we're building, and which edition it lands in, is visible before it ships (§4).

## 4. Roadmap transparency

- The roadmap lives in a public GitHub Project, `{{ROADMAP_URL}}`, with three columns: *Now* (this
  release), *Next* (1–3 months) and *Later*. Every item is labelled `edition: community`,
  `edition: pro`, `edition: hosted` or `edition: enterprise`.
- Feature requests are GitHub Discussions in the "Ideas" category. Upvotes count, and the
  founder writes a short monthly roadmap note in Discussions explaining what moved and why.
- **Edition placement rule**, which we apply to every new feature and publish with it:
  - work that **one person on one computer** needs to turn statements into clean books goes to
    **Community**;
  - work that serves **a practice** (many clients, team workflows, hosted convenience, or a
    third-party service that costs us money to run or maintain) may go to **a paid add-on or the hosted version**.

  Borderline calls are explained in the issue.
- Breaking changes, and anything touching privacy behavior, get an RFC-style issue
  (label `rfc`) that stays open for comment for at least 7 days before merge. Security fixes are
  the exception.

## 5. How decisions are made

1. Most decisions happen in pull-request review: one maintainer approval and passing CI.
2. Disagreements are discussed in the issue or PR. Lazy consensus applies: if nobody objects
   within 7 days, the proposal stands.
3. If there is still no agreement, the BDFL decides and writes down the reasoning in the thread.
4. Code of Conduct matters follow `CODE_OF_CONDUCT.md` and are handled privately, not by vote.
5. Security matters follow `SECURITY.md` and are handled privately until disclosure.

## 6. How this evolves

- **Additional maintainers.** A contributor who has made sustained, high-quality contributions
  over at least 3 months in one area (for example bank-format parsers, exports or
  translations) may be invited to become a maintainer for that area.
- **Steering group.** When there are at least 3 active non-founder maintainers, we will propose a
  small steering group, and this document will be rewritten through a public RFC.
- **If the company stops.** If the company stops maintaining the project, the founder commits to
  transferring the repository and the right to use the project name for the open-source project
  to a community fork or foundation, rather than leaving it orphaned. *Have a lawyer turn this
  into enforceable wording, for example a trademark license.*

## 7. Trademarks

"LedgerStitch" and the logo are trademarks of `{{COMPANY_LEGAL_NAME}}`. The AGPL licenses the
code, not the name. You may fork. If you distribute a modified version, give it a different
name, and don't imply that it's the official LedgerStitch, so users know which build they can
trust with their financial data. Saying "based on LedgerStitch" is fine. The full trademark
policy is in `TRADEMARKS.md` (*to be drafted with counsel*).

## 8. Money

The company funds maintenance through its paid add-ons and hosted version. We will publish a
short annual note in Discussions saying roughly how the project is funded and how much
maintainer time goes into the Community core.
