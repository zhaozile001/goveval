# Grant Proposal: Documentation Site for the Protocol

**Applicant:** documentation working group
**Category:** docs
**Requested:** 0.3 GEN

## What we will deliver

A static documentation site for the protocol, published to GitHub Pages, containing:

1. **Quickstart** — install, configure and run the CLI against Testnet Bradbury, with a working
   example that a reader can copy and execute end to end.
2. **Contract reference** — one page per public method, generated from the contract source so the
   signatures cannot drift from the code.
3. **Troubleshooting** — the ten failure modes we hit while building the example project, each with
   the exact error text and the fix.

Every page ships with the commands that were actually run to produce it. No placeholder text.

## Why the DAO needs this

There is no single place that explains how to go from an empty folder to a deployed contract. Every
new builder re-derives that path from scattered sources, and the questions repeat in the support
channel. The reference pages also prevent a class of bug where the documented signature no longer
matches the deployed method.

## Deliverable

A published site at a stable URL, plus the source repository that generates it. The reviewer can
open the URL and read it — the site is the deliverable, not a promise of one.

## Budget justification

0.3 GEN covers roughly forty hours of writing, the example project, and the generator script. The
largest single item is the contract reference generator; the rest is prose and the example.

## Milestones

- **Week 1** — quickstart and the example project running end to end.
- **Week 2** — generated contract reference and the troubleshooting page.
- **Week 3** — polish, publish, respond to review comments.

## Bond

The applicant stakes 0.1 GEN alongside this proposal. It is returned if the proposal is funded and
slashed into the treasury if the committee rejects it.
