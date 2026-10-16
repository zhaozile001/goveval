# GovEval

**Treasury grants with on-chain proposal review — built on GenLayer Intelligent Contracts.**

A grant DAO has a structural problem: the treasury is on-chain, but the judgement about *who
deserves the money* happens in a forum, in Discord, or in a multisig call. GovEval moves that
judgement into the contract, where the money already is.

- **Anyone can fund the treasury** (`fund_treasury`, payable) — the GEN becomes contract custody.
- **An applicant posts a bond with the proposal** (`submit_proposal`, payable).
- **An on-chain jury reads the proposal and scores it** (`evaluate_proposal`) — the contract fetches
  the proposal URL inside the non-deterministic block and rules against the DAO's funding standards.
- **Approved proposals get paid and their bond back** (`fund_proposal`).
- **Rejected proposals have their bond slashed into the treasury** (`reject_proposal`).
- **The admin can always withdraw unallocated treasury** (`withdraw_treasury`).

Every path ends in a real transfer. There is no state label that stands in for money, and no bond
that can get stranded.

---

## Why this needs GenLayer

Classic smart contracts can't fetch a URL, can't read prose, and can't form a judgement. That is
exactly what a grant decision needs. GenLayer's non-deterministic execution (`gl.nondet.web.get` +
`gl.nondet.exec_prompt` under `gl.vm.run_nondet_unsafe` consensus) lets the contract read the actual
proposal text and reach a verdict that validators agree on — while the GEN custody and payouts stay
deterministic and real.

---

## Contract

`contracts/goveval.py` — deployed on **Bradbury**.

```
0x65A63336f432f9F6c780Adae6E32378665c26221
```

### Money flow

| Step | Call | What moves |
|---|---|---|
| 1 | `fund_treasury()` — **payable** | Funder → contract custody, credited to `treasury` |
| 2 | `submit_proposal(title, category, url, requested)` — **payable** | Applicant → contract custody, booked as the proposal's `stake` (anti-spam bond) |
| 3 | `evaluate_proposal(id)` | No funds move. `gl.nondet.web.get` fetches the proposal, `exec_prompt` returns `{approved, score, reason, confidence}` |
| 4a | `fund_proposal(id)` — admin | `treasury -= requested`; grant paid to the author **and** the bond returned, both via `emit_transfer(..., on='finalized')` |
| 4b | `reject_proposal(id)` — admin | The bond is slashed: `treasury += stake` |
| 5 | `withdraw_treasury(amount, to)` — admin | Unallocated treasury leaves the contract |

### Guarantees the contract enforces

- A grant can never exceed the treasury (`Treasury does not hold enough for this grant`).
- A proposal can only be evaluated once, and only while `OPEN`.
- Only the admin (the deployer) can pay a grant, slash a bond, or withdraw treasury.
- A bond is always settled exactly once — returned on `fund_proposal`, or slashed on `reject_proposal`.
- The jury's `score` is clamped to 0–100 before it is stored.

---

## Repository layout

```
contracts/goveval.py              the Intelligent Contract
tests/direct/test_goveval.py      17 direct-mode tests
index.html                        web UI (no build step, opens straight in a browser)
genlayer.config.yaml              network configuration (Bradbury default)
```

---

## Run the tests

```bash
pip install genlayer-test
pytest tests/direct/ -v
```

```
17 passed
```

The suite covers treasury funding, proposal validation, the one-shot evaluation rule, the score
clamp, admin-only payouts, the treasury cap on grants, bond settlement on both paths, and a full
fund → propose → review → grant → withdraw lifecycle.

Note: the direct VM does not model the contract's own GEN balance, so the tests assert the contract's
internal accounting. The actual movement of GEN is verified end-to-end on Bradbury (below).

---

## Deploy

```bash
genlayer network set testnet-bradbury
genlayer deploy --contract contracts/goveval.py
```

## Use it

```bash
# 1. anyone funds the treasury (payable)
genlayer write <CONTRACT> fund_treasury --value 0.5

# 2. applicant posts a bond with the proposal (payable)
genlayer write <CONTRACT> submit_proposal --args "Fund a standards documentation effort" "docs" "https://example.com/proposal.md" 0.3 --value 0.1

# 3. the jury reads the proposal on-chain
genlayer write <CONTRACT> evaluate_proposal --args 1

# 4. settle: pay the grant (bond returned) or slash the bond
genlayer write <CONTRACT> fund_proposal --args 1
genlayer write <CONTRACT> reject_proposal --args 1

# 5. admin withdraws unallocated treasury
genlayer write <CONTRACT> withdraw_treasury --args 0.2 <recipient>
```

Web UI: open `index.html` — no build step, no dependencies.

---

## Verified on Bradbury

The end-to-end run exercises the real custody and the real payouts: a treasury is funded, a proposal
is submitted with a bond, the jury reviews it on-chain, the grant is settled, and the remaining
treasury is withdrawn. See `_提交材料.md` and `ge_audit.txt` / `ge_evidence.txt` for the transaction
hashes produced by that run.

---

## License

MIT
