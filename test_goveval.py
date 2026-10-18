# -*- coding: utf-8 -*-
"""GovEval direct-mode tests — treasury grants with on-chain proposal review.

Run: pytest tests/direct/ -v

Note: the direct VM does not model the contract's own GEN balance, so the tests assert the
contract's internal accounting (treasury / bonds / custody bookkeeping) rather than self.balance.
The real money movement is verified end-to-end on Bradbury instead.
"""


def _deploy(direct_vm, direct_deploy, admin):
    import pathlib
    import tempfile

    src = pathlib.Path(__file__).resolve().parents[2] / "contracts" / "goveval.py"
    code = src.read_text(encoding="utf-8")
    tmp = pathlib.Path(tempfile.gettempdir()) / "goveval_local.py"
    tmp.write_text(code, encoding="utf-8")
    direct_vm.sender = admin              # whoever deploys becomes the treasury admin
    return direct_deploy(str(tmp))


def _addr(v):
    from genlayer import Address
    return Address(v)


def _fund(direct_vm, contract, who, amount):
    direct_vm.sender = who
    direct_vm.value = amount
    contract.fund_treasury()
    direct_vm.value = 0


def _propose(direct_vm, contract, author, requested=400, stake=100,
             title="Fund a docs site", url="https://example.com/proposal"):
    direct_vm.sender = author
    direct_vm.value = stake
    pid = contract.submit_proposal(title, "docs", url, requested)
    direct_vm.value = 0
    return pid


# ------------------------------------------------- 1. treasury funding ----

def test_fund_treasury_requires_value(direct_vm, direct_deploy, direct_alice):
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_alice
    direct_vm.value = 0
    with direct_vm.expect_revert("Send GEN with the call"):
        contract.fund_treasury()


def test_treasury_tracks_funding(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    assert int(contract.get_treasury()["treasury"]) == 0
    _fund(direct_vm, contract, direct_alice, 1000)
    _fund(direct_vm, contract, direct_bob, 500)
    assert int(contract.get_treasury()["treasury"]) == 1500


# ------------------------------------------------- 2. proposal intake ----

def test_submit_proposal_requires_bond(direct_vm, direct_deploy, direct_alice):
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_alice
    direct_vm.value = 0
    with direct_vm.expect_revert("A bond is required"):
        contract.submit_proposal("Fund a docs site", "docs", "https://example.com/p", 400)


def test_proposal_input_validation(direct_vm, direct_deploy, direct_alice):
    contract = _deploy(direct_vm, direct_deploy, direct_alice)

    direct_vm.sender = direct_alice
    direct_vm.value = 100
    with direct_vm.expect_revert("Title is required"):
        contract.submit_proposal("", "docs", "https://example.com/p", 400)
    with direct_vm.expect_revert("Category is required"):
        contract.submit_proposal("Docs", "", "https://example.com/p", 400)
    with direct_vm.expect_revert("A valid proposal URL is required"):
        contract.submit_proposal("Docs", "docs", "not-a-url", 400)
    with direct_vm.expect_revert("Requested amount must be positive"):
        contract.submit_proposal("Docs", "docs", "https://example.com/p", 0)
    direct_vm.value = 0


def test_bond_is_recorded_as_custody(direct_vm, direct_deploy, direct_alice):
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    pid = _propose(direct_vm, contract, direct_alice, requested=400, stake=100)
    p = contract.get_proposal(pid)
    assert p["title"] == "Fund a docs site"
    assert int(p["stake"]) == 100              # the bond is booked by the contract
    assert int(p["state"]) == 0                # OPEN
    assert p["stake_settled"] is False         # still held
    assert int(contract.get_proposal_count()) == 1
    assert int(contract.get_treasury()["treasury"]) == 0


# ------------------------------------------------- 3. on-chain review ----

def test_evaluate_requires_open_proposal(direct_vm, direct_deploy, direct_alice):
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    pid = _propose(direct_vm, contract, direct_alice)
    direct_vm.mock_web(r".*example\.com/proposal.*",
                       {"status": 200, "body": "<html>We will ship a docs site for 400 GEN</html>"})
    direct_vm.mock_llm(r".*", {"approved": True, "score": 82,
                               "reason": "Concrete deliverable and plausible budget.", "confidence": "high"})
    direct_vm.sender = direct_alice
    contract.evaluate_proposal(pid)
    with direct_vm.expect_revert("Proposal has already been evaluated"):
        contract.evaluate_proposal(pid)


def test_unreachable_proposal_is_rejected(direct_vm, direct_deploy, direct_alice):
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    pid = _propose(direct_vm, contract, direct_alice)
    direct_vm.mock_web(r".*", {"status": 404, "body": ""})
    direct_vm.mock_llm(r".*", {"approved": False, "score": 5,
                               "reason": "The proposal text could not be fetched.", "confidence": "high"})
    direct_vm.sender = direct_alice
    contract.evaluate_proposal(pid)
    p = contract.get_proposal(pid)
    assert int(p["state"]) == 2                # REJECTED
    assert int(p["score"]) == 5


def test_score_is_clamped(direct_vm, direct_deploy, direct_alice):
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    pid = _propose(direct_vm, contract, direct_alice)
    direct_vm.mock_web(r".*", {"status": 200, "body": "<html>proposal</html>"})
    direct_vm.mock_llm(r".*", {"approved": True, "score": 9999,
                               "reason": "Excellent.", "confidence": "high"})
    direct_vm.sender = direct_alice
    contract.evaluate_proposal(pid)
    assert int(contract.get_proposal(pid)["score"]) == 100


def test_unknown_proposal_reverts(direct_vm, direct_deploy, direct_alice):
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Proposal not found"):
        contract.evaluate_proposal(7)


# ------------------------------------------------- 4. payouts and bonds ----

def test_approved_proposal_is_funded_and_bond_returned(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, contract, direct_bob, 1000)
    pid = _propose(direct_vm, contract, direct_alice, requested=400, stake=100)
    direct_vm.mock_web(r".*", {"status": 200, "body": "<html>docs site for 400 GEN</html>"})
    direct_vm.mock_llm(r".*", {"approved": True, "score": 88,
                               "reason": "Clear scope and budget.", "confidence": "high"})
    direct_vm.sender = direct_alice
    contract.evaluate_proposal(pid)

    # settling an approved grant is permissionless: the applicant can force it herself,
    # so an idle admin can never strand an approved liability
    direct_vm.sender = direct_bob             # a third party, not the admin
    contract.fund_proposal(pid)

    p = contract.get_proposal(pid)
    assert int(p["state"]) == 3               # FUNDED
    assert p["stake_settled"] is True         # bond released back to the author
    assert int(contract.get_treasury()["treasury"]) == 600   # 1000 - 400 grant


def test_rejected_proposal_bond_is_slashed_into_treasury(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, contract, direct_bob, 1000)
    pid = _propose(direct_vm, contract, direct_alice, requested=400, stake=100)
    direct_vm.mock_web(r".*", {"status": 200, "body": "<html>buy my token</html>"})
    direct_vm.mock_llm(r".*", {"approved": False, "score": 12,
                               "reason": "Purely promotional, no deliverable.", "confidence": "high"})
    direct_vm.sender = direct_alice
    contract.evaluate_proposal(pid)

    # slashing a rejected bond is permissionless too — the jury already decided
    direct_vm.sender = direct_bob
    contract.reject_proposal(pid)

    # the slash moved the bond into the treasury: 1000 + 100
    assert int(contract.get_treasury()["treasury"]) == 1100
    assert contract.get_proposal(pid)["stake_settled"] is True


def test_reject_requires_rejected_state(direct_vm, direct_deploy, direct_alice):
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    pid = _propose(direct_vm, contract, direct_alice)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Proposal is not rejected"):
        contract.reject_proposal(pid)          # still OPEN


def test_cannot_fund_beyond_treasury(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, contract, direct_bob, 300)     # only 300 available
    pid = _propose(direct_vm, contract, direct_alice, requested=400, stake=100)
    direct_vm.mock_web(r".*", {"status": 200, "body": "<html>docs site</html>"})
    direct_vm.mock_llm(r".*", {"approved": True, "score": 70,
                               "reason": "Reasonable.", "confidence": "medium"})
    direct_vm.sender = direct_alice
    contract.evaluate_proposal(pid)

    with direct_vm.expect_revert("Treasury does not hold enough for this grant"):
        contract.fund_proposal(pid)
    assert int(contract.get_treasury()["treasury"]) == 300   # nothing moved


def test_fund_requires_approved_proposal(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, contract, direct_bob, 1000)
    pid = _propose(direct_vm, contract, direct_alice)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Proposal is not approved"):
        contract.fund_proposal(pid)             # still OPEN


# ------------------------------------------------- 5. withdrawal ----

def test_withdraw_treasury_admin_only_and_capped(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, contract, direct_bob, 1000)

    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("Only the treasury admin can do this"):
        contract.withdraw_treasury(100, _addr(direct_bob))

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Amount must be positive"):
        contract.withdraw_treasury(0, _addr(direct_alice))
    with direct_vm.expect_revert("Amount exceeds the uncommitted treasury"):
        contract.withdraw_treasury(1500, _addr(direct_alice))

    contract.withdraw_treasury(400, _addr(direct_alice))
    assert int(contract.get_treasury()["treasury"]) == 600


# ------------------------------------------------- 6. the lifecycle ----

def test_full_grant_lifecycle(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Fund -> propose -> review -> grant -> withdraw. Treasury ends at exactly zero."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, contract, direct_bob, 1000)
    pid = _propose(direct_vm, contract, direct_alice, requested=250, stake=50)

    direct_vm.mock_web(r".*", {"status": 200, "body": "<html>A tooling grant for 250 GEN</html>"})
    direct_vm.mock_llm(r".*", {"approved": True, "score": 91,
                               "reason": "Needed tooling with a concrete plan.", "confidence": "high"})
    direct_vm.sender = direct_alice
    contract.evaluate_proposal(pid)
    contract.fund_proposal(pid)

    assert int(contract.get_treasury()["treasury"]) == 750   # 1000 - 250
    # unspent treasury is still the admin's to take
    contract.withdraw_treasury(750, _addr(direct_alice))
    assert int(contract.get_treasury()["treasury"]) == 0


def test_bond_always_settles_one_way_or_the_other(direct_vm, direct_deploy, direct_alice, direct_bob):
    """No proposal can leave a bond stranded: it returns on funding, is slashed on rejection."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, contract, direct_bob, 1000)

    ok = _propose(direct_vm, contract, direct_alice, requested=200, stake=100,
                  title="Good one", url="https://example.com/good")
    direct_vm.mock_web(r".*example\.com/good.*", {"status": 200, "body": "<html>real deliverable</html>"})
    direct_vm.mock_web(r".*example\.com/spam.*", {"status": 200, "body": "<html>airdrop scam</html>"})
    direct_vm.mock_llm(r".*real deliverable.*", {"approved": True, "score": 80,
                                                 "reason": "Solid.", "confidence": "high"})
    direct_vm.mock_llm(r".*airdrop scam.*", {"approved": False, "score": 3,
                                             "reason": "Spam.", "confidence": "high"})
    direct_vm.sender = direct_alice
    contract.evaluate_proposal(ok)
    contract.fund_proposal(ok)
    assert contract.get_proposal(ok)["stake_settled"] is True

    bad = _propose(direct_vm, contract, direct_bob, requested=200, stake=100,
                   title="Spam", url="https://example.com/spam")
    direct_vm.sender = direct_bob
    contract.evaluate_proposal(bad)
    assert int(contract.get_proposal(bad)["state"]) == 2      # REJECTED
    direct_vm.sender = direct_alice          # admin settles the slash
    contract.reject_proposal(bad)
    assert contract.get_proposal(bad)["stake_settled"] is True

    # treasury: 1000 funded + 100 slashed - 200 granted = 900, and nothing is stranded
    assert int(contract.get_treasury()["treasury"]) == 900

# ================================================= 7. review round: idempotency,
# =================================================    liability protection, no admin stalls

def test_rejected_bond_settlement_is_idempotent(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Replaying reject_proposal must not credit the same stake twice."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, contract, direct_bob, 1000)
    pid = _propose(direct_vm, contract, direct_alice, requested=400, stake=100)
    direct_vm.mock_web(r".*", {"status": 200, "body": "<html>spam</html>"})
    direct_vm.mock_llm(r".*", {"approved": False, "score": 5,
                               "reason": "Spam.", "confidence": "high"})
    direct_vm.sender = direct_alice
    contract.evaluate_proposal(pid)

    contract.reject_proposal(pid)
    assert int(contract.get_treasury()["treasury"]) == 1100     # 1000 + 100 once

    # replay: rejected, and the stake must NOT be credited a second time
    for _ in range(3):
        with direct_vm.expect_revert("Bond already settled"):
            contract.reject_proposal(pid)
        assert int(contract.get_treasury()["treasury"]) == 1100
    assert contract.get_proposal(pid)["stake_settled"] is True


def test_grant_payout_is_idempotent(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Replaying fund_proposal must not pay the grant twice."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, contract, direct_bob, 1000)
    pid = _propose(direct_vm, contract, direct_alice, requested=400, stake=100)
    direct_vm.mock_web(r".*", {"status": 200, "body": "<html>real proposal</html>"})
    direct_vm.mock_llm(r".*", {"approved": True, "score": 85,
                               "reason": "Solid.", "confidence": "high"})
    direct_vm.sender = direct_alice
    contract.evaluate_proposal(pid)

    contract.fund_proposal(pid)
    assert int(contract.get_treasury()["treasury"]) == 600

    for _ in range(3):
        with direct_vm.expect_revert("Grant already paid out"):
            contract.fund_proposal(pid)
        assert int(contract.get_treasury()["treasury"]) == 600


def test_one_bond_one_settlement_path_only(direct_vm, direct_deploy, direct_alice, direct_bob):
    """A bond settles on exactly one path: paid out, or slashed. Never both."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, contract, direct_bob, 1000)
    pid = _propose(direct_vm, contract, direct_alice, requested=400, stake=100)
    direct_vm.mock_web(r".*", {"status": 200, "body": "<html>decided</html>"})
    direct_vm.mock_llm(r".*", {"approved": False, "score": 8,
                               "reason": "No.", "confidence": "high"})
    direct_vm.sender = direct_alice
    contract.evaluate_proposal(pid)

    contract.reject_proposal(pid)               # rejected => slash path taken

    # the bond is already settled, so the grant path must refuse to touch it
    with direct_vm.expect_revert("Grant already paid out"):
        contract.fund_proposal(pid)
    # and the slash itself cannot be replayed
    with direct_vm.expect_revert("Bond already settled"):
        contract.reject_proposal(pid)


def test_anyone_can_settle_an_approved_grant(direct_vm, direct_deploy, direct_alice, direct_bob):
    """An idle admin cannot stall an approved grant: a third party can force the payout."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, contract, direct_bob, 1000)
    pid = _propose(direct_vm, contract, direct_alice, requested=300, stake=50)
    direct_vm.mock_web(r".*", {"status": 200, "body": "<html>good</html>"})
    direct_vm.mock_llm(r".*", {"approved": True, "score": 90,
                               "reason": "Good.", "confidence": "high"})
    direct_vm.sender = direct_alice
    contract.evaluate_proposal(pid)

    direct_vm.sender = direct_bob                # not the admin, not the applicant
    contract.fund_proposal(pid)
    assert int(contract.get_proposal(pid)["state"]) == 3
    assert int(contract.get_treasury()["treasury"]) == 700


def test_anyone_can_settle_a_rejected_bond(direct_vm, direct_deploy, direct_alice, direct_bob):
    """An idle admin cannot stall a rejected bond either."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, contract, direct_bob, 1000)
    pid = _propose(direct_vm, contract, direct_alice, requested=300, stake=50)
    direct_vm.mock_web(r".*", {"status": 200, "body": "<html>bad</html>"})
    direct_vm.mock_llm(r".*", {"approved": False, "score": 9,
                               "reason": "No.", "confidence": "high"})
    direct_vm.sender = direct_alice
    contract.evaluate_proposal(pid)

    direct_vm.sender = direct_bob
    contract.reject_proposal(pid)
    assert int(contract.get_treasury()["treasury"]) == 1050
    assert contract.get_proposal(pid)["stake_settled"] is True


def test_withdraw_cannot_touch_committed_grant(direct_vm, direct_deploy, direct_alice, direct_bob):
    """An approved-but-unpaid grant is protected: withdrawal can only take the excess."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, contract, direct_bob, 1000)
    pid = _propose(direct_vm, contract, direct_alice, requested=700, stake=100)
    direct_vm.mock_web(r".*", {"status": 200, "body": "<html>approved</html>"})
    direct_vm.mock_llm(r".*", {"approved": True, "score": 88,
                               "reason": "Approved.", "confidence": "high"})
    direct_vm.sender = direct_alice
    contract.evaluate_proposal(pid)

    t = contract.get_treasury()
    assert int(t["committed"]) == 700            # the grant is a registered liability
    assert int(t["withdrawable"]) == 300         # only the excess may leave

    # the admin cannot drain the committed 700
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Amount exceeds the uncommitted treasury"):
        contract.withdraw_treasury(700, _addr(direct_alice))
    with direct_vm.expect_revert("Amount exceeds the uncommitted treasury"):
        contract.withdraw_treasury(1000, _addr(direct_alice))

    # withdrawing the excess is fine, and the grant is still payable afterwards
    contract.withdraw_treasury(300, _addr(direct_alice))
    assert int(contract.get_treasury()["withdrawable"]) == 0
    direct_vm.sender = direct_bob
    contract.fund_proposal(pid)                  # still reachable
    assert int(contract.get_proposal(pid)["state"]) == 3
    assert int(contract.get_treasury()["committed"]) == 0


def test_committed_is_released_on_payout(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Paying a grant releases its commitment exactly once."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, contract, direct_bob, 1000)
    pid = _propose(direct_vm, contract, direct_alice, requested=400, stake=100)
    direct_vm.mock_web(r".*", {"status": 200, "body": "<html>ok</html>"})
    direct_vm.mock_llm(r".*", {"approved": True, "score": 80,
                               "reason": "Ok.", "confidence": "high"})
    direct_vm.sender = direct_alice
    contract.evaluate_proposal(pid)
    assert int(contract.get_treasury()["committed"]) == 400

    contract.fund_proposal(pid)
    t = contract.get_treasury()
    assert int(t["committed"]) == 0
    assert int(t["treasury"]) == 600
    assert int(t["withdrawable"]) == 600         # everything left is free again


def test_rejected_proposal_never_commits_treasury(direct_vm, direct_deploy, direct_alice, direct_bob):
    """A rejected proposal must not register a liability."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, contract, direct_bob, 1000)
    pid = _propose(direct_vm, contract, direct_alice, requested=400, stake=100)
    direct_vm.mock_web(r".*", {"status": 200, "body": "<html>no</html>"})
    direct_vm.mock_llm(r".*", {"approved": False, "score": 5,
                               "reason": "No.", "confidence": "high"})
    direct_vm.sender = direct_alice
    contract.evaluate_proposal(pid)
    assert int(contract.get_treasury()["committed"]) == 0
    assert int(contract.get_treasury()["withdrawable"]) == 1000


def test_every_bond_and_liability_has_a_reachable_path(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Three proposals, three outcomes; every bond and every approved liability settles."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, contract, direct_bob, 3000)

    good = _propose(direct_vm, contract, direct_alice, requested=500, stake=100,
                    title="Good", url="https://example.com/good")
    bad = _propose(direct_vm, contract, direct_bob, requested=500, stake=100,
                   title="Bad", url="https://example.com/bad")
    pending = _propose(direct_vm, contract, direct_bob, requested=500, stake=100,
                       title="Pending", url="https://example.com/pending")

    direct_vm.mock_web(r".*example\.com/good.*", {"status": 200, "body": "<html>great proposal</html>"})
    direct_vm.mock_web(r".*example\.com/bad.*", {"status": 200, "body": "<html>terrible proposal</html>"})
    direct_vm.mock_llm(r".*great proposal.*", {"approved": True, "score": 90, "reason": "Good.", "confidence": "high"})
    direct_vm.mock_llm(r".*terrible proposal.*", {"approved": False, "score": 5, "reason": "Bad.", "confidence": "high"})

    direct_vm.sender = direct_alice
    contract.evaluate_proposal(good)
    contract.evaluate_proposal(bad)

    # good: approved liability -> paid
    contract.fund_proposal(good)
    # bad: rejected bond -> slashed
    contract.reject_proposal(bad)
    # pending: still OPEN, bond still held, and the OPEN state is the documented pre-review path
    p = contract.get_proposal(pending)
    assert int(p["state"]) == 0 and p["stake_settled"] is False

    assert contract.get_proposal(good)["stake_settled"] is True
    assert contract.get_proposal(bad)["stake_settled"] is True
    assert int(contract.get_treasury()["committed"]) == 0
    # 3000 funded - 500 grant + 100 slashed = 2600, nothing stranded on the two settled ones
    assert int(contract.get_treasury()["treasury"]) == 2600


def test_cannot_settle_before_the_jury_rules(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Neither settlement path is reachable before evaluation — no premature payouts."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _fund(direct_vm, contract, direct_bob, 1000)
    pid = _propose(direct_vm, contract, direct_alice, requested=400, stake=100)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Proposal is not approved"):
        contract.fund_proposal(pid)
    with direct_vm.expect_revert("Proposal is not rejected"):
        contract.reject_proposal(pid)
