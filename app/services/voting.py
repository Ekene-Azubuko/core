"""One active vote per member per round and conservative tie handling."""

from collections import Counter

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db.models import Vote, now


async def cast_vote(session, proposal, user_id, round_number, choice):
    """Insert or replace a member's vote while the matching round is still open."""
    if (proposal.status != "voting" or proposal.round != round_number
            or proposal.closes_at is None or proposal.closes_at <= now()):
        return False
    if choice < -1 or choice >= len(proposal.categories):
        return False
    statement = insert(Vote).values(
        proposal_id=proposal.id, round=round_number, user_id=user_id, choice=choice,
    ).on_conflict_do_update(
        index_elements=[Vote.proposal_id, Vote.round, Vote.user_id],
        set_={"choice": choice, "updated_at": now()},
    )
    await session.execute(statement)
    return True


def outcome(choices):
    """Choose the highest count; No wins its ties and category ties need a runoff."""
    counts = Counter(choices)
    if not counts:
        return "expired", []
    winners = sorted(choice for choice, count in counts.items() if count == max(counts.values()))
    if -1 in winners:
        return "rejected", [-1]
    if len(winners) > 1:
        return "runoff", winners
    return "generating", winners


async def close_vote(session, proposal):
    """Persist the decision or reset the proposal for a runoff with fresh votes."""
    choices = list(await session.scalars(select(Vote.choice).where(
        Vote.proposal_id == proposal.id, Vote.round == proposal.round,
    )))
    status, winners = outcome(choices)
    if status == "runoff":
        proposal.categories = [proposal.categories[index] for index in winners]
        proposal.round += 1
        proposal.closes_at = None
        proposal.telegram_message_id = None
    else:
        proposal.status = status
        if status == "generating":
            proposal.winner = proposal.categories[winners[0]]
    return status
