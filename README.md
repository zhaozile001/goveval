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
0xadBb62E3d1611b1f848C8fEA33D1191f32A82a4e
```

### Money flow

| Step | Call | What moves |
|---|---|---|
| 1 | `fund_treasury()` — **payable** | Funder → contract custody, credited to `treasury` |
| 2 | `submit_proposal(title, category, url, requested)` — **payable** | Applicant → contract custody, booked as the proposal's `stake` (anti-spam bond) |
| 3 | `evaluate_proposal(id)` | No funds move. `gl.nondet.web.get` fetches the proposal, `exec_prompt` returns `{approved, score, reason, confidence}` |
| 4a | `fund_proposal(id)` — **permissionless** | `treasury -= requested`, `committed -= requested`; grant paid to the author **and** the bond returned, both via `emit_transfer(..., on='finalized')` |
| 4b | `reject_proposal(id)` — **permissionless, idempotent** | The bond is slashed: `treasury += stake`, **once only** |
| 4c | `reclaim_unfunded_bond(id)` — applicant only, no funds needed from the treasury | An `UNFUNDED` proposal's bond (and the grant) is voided: the bond returned, state `WITHDRAWN`, **once only** |
| 5 | `withdraw_treasury(amount, to)` — admin | Unallocated treasury leaves the contract |

### Guarantees the contract enforces

- A grant can never exceed the treasury (`Treasury does not hold enough for this grant`).
- A proposal can only be evaluated once, and only while `OPEN`.
- **Bond settlement is idempotent.** `reject_proposal` reverts with `Bond already settled` on a
  second call, so a replayed transaction can never credit the same stake twice. `fund_proposal`
  likewise reverts with `Grant already paid out`.
- **Approved grants are protected from withdrawal.** `evaluate_proposal` registers an approved grant
  in `committed`; `withdraw_treasury` can only ever take `treasury - committed`, so a withdrawal can
  never leave an approved liability unpayable (`Amount exceeds the uncommitted treasury`).
- **Settlement is permissionless.** Once the jury has ruled, *anyone* — the applicant included — can
  call `fund_proposal` or `reject_proposal`. An idle admin cannot stall a bond or an approved grant.
- Only the admin can withdraw treasury (and only the uncommitted part).
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
38 passed
```

The suite covers treasury funding, proposal validation, the one-shot evaluation rule, the score
clamp, the treasury cap on grants, bond settlement on both paths, and a full
fund → propose → review → grant → withdraw lifecycle — plus, from the review round:
**replay rejection** for both `reject_proposal` and `fund_proposal`, **single-settlement** per bond,
**committed-grant protection** against treasury withdrawal, **permissionless settlement** (so an idle
admin cannot stall anything), and a test proving every bond and approved liability has a single,
reachable settlement path.

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

---

## v3 — aggregate solvency at approval

The second review round found that `evaluate_proposal` registered an approval as a liability without
checking whether the treasury could cover **all** of them: several individually-affordable grants
could total more than the treasury held, leaving some approved yet unpayable.

**v3 fixes this with a deterministic check inside `evaluate_proposal`:**

```
if approved and committed + requested <= treasury:
    state = APPROVED,  committed += requested          # a fully funded liability
else if approved:
    state = UNFUNDED                                    # jury liked it, treasury cannot cover it
    # verdict and score are kept, nothing is added to committed, fund_proposal refuses it
```

**The invariant `committed <= treasury` therefore holds at all times**, so every `APPROVED` grant is
fully payable. It is exposed by `get_solvency()` and asserted throughout the test suite.

`UNFUNDED` is not a dead end, and it is never a trap for the applicant's bond either. There are two
mutually exclusive ways out:

- `activate_proposal` promotes it to `APPROVED` once a top-up makes it affordable, and it is
  **permissionless** — the applicant can activate her own grant without relying on the admin.
- `reclaim_unfunded_bond` returns the bond **immediately and with no treasury top-up at all**. It is
  applicant-only, bounded to state `UNFUNDED`, and duplicate-safe. Reclaiming moves the proposal to
  `WITHDRAWN`, which voids the grant, so a bond can never be reclaimed *and* collected afterwards.

### Regression tests added for this round (38 total)

| Case | Test |
|---|---|
| competing approvals cannot exceed the treasury | `test_competing_approvals_never_exceed_the_treasury` |
| an UNFUNDED grant cannot be paid directly | `test_unfunded_proposal_cannot_be_paid_directly` |
| activation after a top-up | `test_unfunded_proposal_activates_after_topup` |
| every approved liability is fully payable | `test_every_approved_liability_is_fully_payable` |
| withdrawal never breaks solvency | `test_solvency_invariant_survives_withdrawals` |
| activation guard | `test_activate_requires_unfunded_state` |
| **bond recoverable with no top-up at all** | `test_unfunded_bond_recoverable_without_any_topup` |
| **reclaim is duplicate-safe** | `test_unfunded_bond_reclaim_is_duplicate_safe` |
| **reclaim is applicant-only** | `test_unfunded_bond_reclaim_is_applicant_only` |
| **a reclaimed grant cannot be activated later** | `test_reclaimed_unfunded_grant_cannot_be_activated` |
| **reclaim is bounded to UNFUNDED** | `test_reclaim_only_applies_to_unfunded` |

### Source-matched deployment evidence

Bradbury `0xadBb62E3d1611b1f848C8fEA33D1191f32A82a4e`, source **16,629 bytes** (identical to
`contracts/goveval.py`):

```
deploy                          0x4526ec2bba2a98fca7fc8a84de01194005c0e1f11a379fe1873cb970d06c0b45

treasury 0.3 GEN, applicant asks 0.8 GEN
  evaluate -> UNFUNDED, score 90   (jury approved; treasury cannot cover it)
  treasury still 0.3 GEN           (recovery needed no additional funding)
  reclaim_unfunded_bond            bond returned, state WITHDRAWN, stake_settled true
  replay reclaim                   reverted
  top-up 2.0 GEN then activate     reverted   (grant was voided, cannot be collected)
  committed stayed 0 throughout
```

`pytest tests/direct/ -v` → **38 passed**

### Prompt correction

The jury prompt previously instructed the model to reject a proposal that "asks for more than the
treasury holds". That duplicated — and contradicted — the deterministic solvency check, because it
made `UNFUNDED` unreachable: a grant the treasury could not cover was simply rejected instead of
being recorded as approved-but-unfunded. The prompt no longer mentions the treasury balance at all;
funding sufficiency is enforced by the contract, not by the model.
