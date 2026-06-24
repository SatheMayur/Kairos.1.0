"""Regression test: the WhatsApp send queue must not re-send a claimed message.

Bug fixed: poll_queue returned PENDING rows without claiming them, so the bridge's
3-second re-poll re-fetched and RE-SENT the same message many times (candidates got
10+ duplicate messages). Polling now claims rows so a second poll won't return them.
"""
import pytest

from app.api import wa_bridge
from app.models.wa_queue import WAQueue, WAQueueStatus


@pytest.mark.asyncio
async def test_poll_claims_messages_no_duplicate_send(client, db_session):
    h = {"x-bridge-key": wa_bridge.BRIDGE_SECRET}
    db_session.add(WAQueue(phone="919876543210", message="hello", status=WAQueueStatus.PENDING))
    await db_session.commit()

    first = await client.get("/api/v1/wa/poll", headers=h)
    assert first.status_code == 200
    assert len(first.json()) == 1            # first poll hands it to the bridge

    second = await client.get("/api/v1/wa/poll", headers=h)
    assert second.json() == []               # claimed → NOT re-sent on the next poll


@pytest.mark.asyncio
async def test_poll_requires_bridge_key(client):
    bad = await client.get("/api/v1/wa/poll", headers={"x-bridge-key": "wrong"})
    assert bad.status_code == 401


@pytest.mark.asyncio
async def test_inbound_message_id_is_unique(db_session):
    """The idempotency key behind no-duplicate-replies: the same WhatsApp message
    id can be recorded only once."""
    from sqlalchemy.exc import IntegrityError
    from app.models.wa_inbound import WaInbound
    db_session.add(WaInbound(message_id="WAMSG-1", phone="919876543210"))
    await db_session.commit()
    db_session.add(WaInbound(message_id="WAMSG-1", phone="919876543210"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
