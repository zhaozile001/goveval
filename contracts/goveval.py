# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

import json
from dataclasses import dataclass
from genlayer import *

# ---------------------------------------------------------------- storage ---


@allow_storage
@dataclass
class Proposal:
    author: Address          # who wrote the proposal
    title: str
    category: str            # e.g. "protocol", "docs", "tooling"
    proposal_url: str        # where the full proposal text is published
    requested: u256          # GEN the author asks from the treasury
    stake: u256              # anti-spam bond posted by the author
    score: u256              # 0-100 quality score returned by the jury
    state: u8                # 0=OPEN 1=APPROVED 2=REJECTED 3=FUNDED
    verdict_reason: str
    confidence: str
    stake_settled: bool      # the bond has left this contract (refunded or slashed)


# external message interface for paying an EOA / EVM address on the chain layer
@gl.evm.contract_interface
class _Payee:
    class View:
        pass

    class Write:
        pass


# ------------------------------------------------------------------ contract --


class GovEval(gl.Contract):
    """Treasury grants with on-chain proposal review.

    A DAO cannot read its own proposal forum. GovEval puts the review on-chain: anyone funds the
    treasury (payable), an applicant submits a proposal and posts a bond, and an LLM jury then
    fetches the proposal text and rules on it against the DAO's published standards. Approved
    proposals can be paid out of the treasury and get their bond back; rejected proposals have
    their bond slashed into the treasury.

    Money is real in both directions: treasury funding and applicant bonds are custody
    (gl.message.value), and every payout, refund and slash leaves through emit_transfer.
    """

    proposals: TreeMap[u256, Proposal]
    next_id: u256
    treasury: u256
    committed: u256          # grants already approved by the jury but not yet paid out
    admin: Address

    def __init__(self):
        self.next_id = u256(1)
        self.treasury = u256(0)
        self.committed = u256(0)
        self.admin = gl.message.sender_address

    # -------------------------------------------------------- internals ----

    def _to_addr(self, value) -> Address:
        if isinstance(value, Address):
            return value
        return Address(value)

    def _addr_bytes(self, value) -> bytes:
        b = getattr(value, "as_bytes", None)
        if b is None:
            b = getattr(value, "_as_bytes", None)
        if callable(b):
            b = b()
        if isinstance(b, (bytes, bytearray)):
            return bytes(b)
        return bytes(self._to_addr(value))

    def _prop(self, proposal_id: u256):
        if proposal_id not in self.proposals:
            raise gl.vm.UserError("Proposal not found")
        return self.proposals[proposal_id]

    def _only_admin(self):
        if self._addr_bytes(gl.message.sender_address) != self._addr_bytes(self.admin):
            raise gl.vm.UserError("Only the treasury admin can do this")

    def _pay(self, to: Address, amount: u256):
        """Send GEN out of this contract (external message, executed on finalization).

        The explicit ``on='finalized'`` matches the documented form for value transfers;
        without it the message may not be emitted at all.
        """
        if amount <= u256(0):
            return
        _Payee(to).emit_transfer(value=u256(amount), on='finalized')

    # ------------------------------------------------------------- views ----

    @gl.public.view
    def get_treasury(self) -> dict:
        return {
            "treasury": self.treasury,
            "committed": self.committed,
            "withdrawable": self.treasury - self.committed,
            "contract_balance": self.balance,
            "admin": "0x" + self._addr_bytes(self.admin).hex(),
            "proposal_count": self.next_id - u256(1),
        }

    @gl.public.view
    def get_proposal(self, proposal_id: u256) -> dict:
        p = self.proposals[proposal_id]
        return {
            "id": proposal_id,
            "author": "0x" + self._addr_bytes(p.author).hex(),
            "title": p.title,
            "category": p.category,
            "proposal_url": p.proposal_url,
            "requested": p.requested,
            "stake": p.stake,
            "score": p.score,
            "state": p.state,
            "verdict_reason": p.verdict_reason,
            "confidence": p.confidence,
            "stake_settled": p.stake_settled,
        }

    @gl.public.view
    def get_proposal_count(self) -> u256:
        return self.next_id - u256(1)

    @gl.public.view
    def get_contract_balance(self) -> u256:
        return self.balance

    # ------------------------------------------------------------ writes ----

    @gl.public.write.payable
    def fund_treasury(self):
        """Anyone may add GEN to the grants treasury."""
        value = gl.message.value
        if value <= u256(0):
            raise gl.vm.UserError("Send GEN with the call")
        self.treasury = self.treasury + value

    @gl.public.write.payable
    def submit_proposal(self, title: str, category: str, proposal_url: str, requested: u256) -> u256:
        """Applicant publishes a proposal and posts a bond in the same (payable) call."""
        stake = gl.message.value
        if stake <= u256(0):
            raise gl.vm.UserError("A bond is required: send GEN with the call")
        if len(title.strip()) == 0:
            raise gl.vm.UserError("Title is required")
        if len(category.strip()) == 0:
            raise gl.vm.UserError("Category is required")
        if len(proposal_url.strip()) == 0 or not (
                proposal_url.startswith("http://") or proposal_url.startswith("https://")):
            raise gl.vm.UserError("A valid proposal URL is required")
        if requested <= u256(0):
            raise gl.vm.UserError("Requested amount must be positive")

        p = Proposal(
            author=self._to_addr(gl.message.sender_address),
            title=title,
            category=category,
            proposal_url=proposal_url,
            requested=requested,
            stake=stake,
            score=u256(0),
            state=u8(0),
            verdict_reason="",
            confidence="",
            stake_settled=False,
        )
        pid = self.next_id
        self.proposals[pid] = p
        self.next_id = pid + u256(1)
        return pid

    @gl.public.write
    def evaluate_proposal(self, proposal_id: u256):
        """LLM jury fetches the proposal and scores it against the DAO's funding standards."""
        p = self._prop(proposal_id)
        if p.state != u8(0):
            raise gl.vm.UserError("Proposal has already been evaluated")

        # Snapshot into plain Python values outside the nondet block.
        title_mem = str(p.title)
        category_mem = str(p.category)
        url_mem = str(p.proposal_url)
        requested_mem = int(p.requested)
        stake_mem = int(p.stake)
        treasury_mem = int(self.treasury)

        def _fetch_text(url: str, limit: int = 3000) -> str:
            try:
                resp = gl.nondet.web.get(url)
                if isinstance(resp, bytes):
                    raw = resp.decode("utf-8", errors="ignore")
                elif hasattr(resp, "body"):
                    raw = resp.body.decode("utf-8", errors="ignore")
                else:
                    raw = str(resp)
                return " ".join(raw.split())[:limit]
            except Exception:
                return "[could not fetch this proposal]"

        def leader_fn():
            page = _fetch_text(url_mem)
            prompt = (
                "You are the impartial review committee of a grant DAO.\n\n"
                "PROPOSAL TITLE: " + title_mem + "\n"
                "CATEGORY: " + category_mem + "\n"
                "REQUESTED AMOUNT: " + str(requested_mem) + " wei of GEN\n"
                "APPLICANT BOND AT RISK: " + str(stake_mem) + " wei of GEN\n"
                "TREASURY AVAILABLE: " + str(treasury_mem) + " wei of GEN\n"
                "PROPOSAL URL: " + url_mem + "\n\n"
                "PROPOSAL TEXT FETCHED RIGHT NOW:\n" + page + "\n\n"
                "Judge the proposal ONLY on the fetched text above. Approve it when it states a "
                "concrete deliverable, a plausible budget for that deliverable, and something "
                "the DAO actually needs. Reject it when the text is missing, unreachable, empty, "
                "purely promotional, or asks for more than the treasury holds.\n\n"
                'Respond as JSON with exactly these keys:\n'
                '- "approved": true or false\n'
                '- "score": integer 0-100 quality score\n'
                '- "reason": concise 2-4 sentence justification citing the fetched text\n'
                '- "confidence": one of "high", "medium", "low"'
            )
            return gl.nondet.exec_prompt(prompt, response_format='json')

        def validator_fn(leaders_res) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return False
            mine = leader_fn()
            if not isinstance(mine, dict) or not isinstance(leaders_res.calldata, dict):
                return False
            a = mine.get("approved")
            b = leaders_res.calldata.get("approved")
            if isinstance(a, str):
                a = a.strip().lower() == "true"
            if isinstance(b, str):
                b = b.strip().lower() == "true"
            return bool(a) == bool(b)

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        ok = result.get("approved", False)
        if isinstance(ok, str):
            ok = ok.strip().lower() == "true"

        score = result.get("score", 0)
        try:
            score_val = int(score)
        except Exception:
            score_val = 0
        if score_val < 0:
            score_val = 0
        if score_val > 100:
            score_val = 100

        approved = bool(ok)
        p.score = u256(score_val)
        p.verdict_reason = str(result.get("reason", ""))
        p.confidence = str(result.get("confidence", ""))

        if not approved:
            p.state = u8(2)                     # REJECTED
            return

        # Deterministic solvency check: an approval only becomes a payable liability when the
        # treasury can cover the aggregate of ALL outstanding approvals. Without this, several
        # individually-approvable grants could total more than the treasury holds and some of them
        # would be approved yet unpayable.
        if int(self.committed) + int(p.requested) > int(self.treasury):
            p.state = u8(5)                     # UNFUNDED - jury liked it, treasury cannot cover it
            p.verdict_reason = (
                "Jury approved, but the treasury cannot cover this grant: committed "
                + str(int(self.committed)) + " + requested " + str(int(p.requested))
                + " exceeds treasury " + str(int(self.treasury)) + ". Top up and activate."
            )
            return

        p.state = u8(1)                         # APPROVED - a fully funded liability
        self.committed = self.committed + p.requested

    @gl.public.write
    def activate_proposal(self, proposal_id: u256):
        """Promote an UNFUNDED proposal to a payable liability once the treasury can cover it.

        Permissionless: the applicant can activate her own grant after anyone tops the treasury up,
        so an approved-by-quality proposal never depends on admin goodwill to become payable.
        """
        p = self._prop(proposal_id)
        if p.state != u8(5):
            raise gl.vm.UserError("Proposal is not awaiting funding")
        if int(self.committed) + int(p.requested) > int(self.treasury):
            raise gl.vm.UserError("Treasury still cannot cover this grant")
        p.state = u8(1)                         # APPROVED
        p.verdict_reason = "Activated after the treasury could cover this grant."
        self.committed = self.committed + p.requested

    @gl.public.view
    def get_solvency(self) -> dict:
        """Aggregate solvency view: every approved liability is fully backed by the treasury."""
        return {
            "treasury": self.treasury,
            "committed": self.committed,
            "withdrawable": self.treasury - self.committed,
            "solvent": int(self.committed) <= int(self.treasury),
        }

    @gl.public.write
    def fund_proposal(self, proposal_id: u256):
        """Pay an approved proposal and return the applicant's bond.

        Permissionless on purpose: once the jury has approved the grant it is a liability of the
        treasury, so *anyone* — the applicant included — can force it to be settled. The admin
        cannot stall an approved grant by simply not calling this.
        """
        p = self._prop(proposal_id)
        if p.stake_settled:
            raise gl.vm.UserError("Grant already paid out")
        if p.state != u8(1):
            raise gl.vm.UserError("Proposal is not approved")
        if int(p.requested) > int(self.treasury):
            raise gl.vm.UserError("Treasury does not hold enough for this grant")

        self.treasury = self.treasury - p.requested
        self.committed = self.committed - p.requested
        p.state = u8(3)                  # FUNDED
        p.stake_settled = True
        self._pay(p.author, p.requested)
        self._pay(p.author, p.stake)     # bond returned on a successful grant

    @gl.public.write
    def reject_proposal(self, proposal_id: u256):
        """Settle a rejected proposal: the bond is slashed into the treasury.

        Idempotent — the bond can only be credited once. Also permissionless for the same reason
        as ``fund_proposal``: the jury already decided, so nobody should be able to stall the
        settlement by refusing to call it.
        """
        p = self._prop(proposal_id)
        if p.state != u8(2):
            raise gl.vm.UserError("Proposal is not rejected")
        if p.stake_settled:
            raise gl.vm.UserError("Bond already settled")

        p.stake_settled = True
        self.treasury = self.treasury + p.stake

    @gl.public.write
    def withdraw_treasury(self, amount: u256, to: Address):
        """The admin withdraws only the *uncommitted* treasury.

        Approved-but-unpaid grants are excluded, so a withdrawal can never leave an approved
        liability unpayable.
        """
        self._only_admin()
        if amount <= u256(0):
            raise gl.vm.UserError("Amount must be positive")
        withdrawable = int(self.treasury) - int(self.committed)
        if withdrawable < 0:
            withdrawable = 0
        if int(amount) > withdrawable:
            raise gl.vm.UserError("Amount exceeds the uncommitted treasury")

        self.treasury = self.treasury - amount
        self._pay(self._to_addr(to), amount)
