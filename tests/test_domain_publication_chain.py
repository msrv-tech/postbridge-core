"""Цепочка домена публикации: tenant_id и FK (фаза 1)."""

from uuid import uuid4

import pytest

from postbridge.db import (  # noqa: E402
    Base,
    ENGINE,
    SESSION_LOCAL,
    BatchImportRunOrm,
    init_db,
)
from postbridge.models.domain import (  # noqa: E402
    ChannelOrm,
    ContentItemOrm,
    PublicationPlanOrm,
    PublicationTargetOrm,
    RenderVariantOrm,
    TenantOrm,
)
from postbridge.services.publication_planning import (  # noqa: E402
    create_content_with_plan_and_targets,
)


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=ENGINE)
    init_db()
    session = SESSION_LOCAL()
    session.query(BatchImportRunOrm).delete()
    session.commit()
    session.close()
    yield


def _seed_tenant_and_channels(session, tenant_id: str) -> tuple[str, str]:
    session.add(TenantOrm(id=tenant_id, name="t1"))
    session.flush()
    c1, c2 = str(uuid4()), str(uuid4())
    session.add(
        ChannelOrm(
            id=c1,
            tenant_id=tenant_id,
            platform="telegram",
            kind="destination",
            title="Ch1",
            external_id="@a",
            status="connected",
        )
    )
    session.add(
        ChannelOrm(
            id=c2,
            tenant_id=tenant_id,
            platform="vk",
            kind="destination",
            title="Ch2",
            external_id="club1",
            status="connected",
        )
    )
    session.commit()
    return c1, c2


def test_create_chain_tenant_id_consistent():
    session = SESSION_LOCAL()
    tenant_id = str(uuid4())
    c1, c2 = _seed_tenant_and_channels(session, tenant_id)

    result = create_content_with_plan_and_targets(
        session,
        tenant_id=tenant_id,
        channel_ids=[c1, c2],
        author_user_id="saas-user-1",
        title="Hello",
        body_markdown="# Hi",
        content_status="ready",
        plan_strategy="immediate",
        plan_status="scheduled",
        target_status="pending",
    )
    session.commit()

    ci = session.get(ContentItemOrm, result.content_item_id)
    assert ci is not None
    assert ci.tenant_id == tenant_id

    pp = session.get(PublicationPlanOrm, result.publication_plan_id)
    assert pp is not None
    assert pp.tenant_id == tenant_id
    assert pp.content_item_id == result.content_item_id

    for rv_id in result.render_variant_ids:
        rv = session.get(RenderVariantOrm, rv_id)
        assert rv is not None
        assert rv.tenant_id == tenant_id
        assert rv.content_item_id == result.content_item_id

    for pt_id in result.publication_target_ids:
        pt = session.get(PublicationTargetOrm, pt_id)
        assert pt is not None
        assert pt.tenant_id == tenant_id
        assert pt.publication_plan_id == result.publication_plan_id
        assert pt.render_variant_id in result.render_variant_ids

    session.close()


def test_rejects_foreign_channel_tenant():
    session = SESSION_LOCAL()
    t_a, t_b = str(uuid4()), str(uuid4())
    session.add(TenantOrm(id=t_a, name="a"))
    session.add(TenantOrm(id=t_b, name="b"))
    session.flush()
    c_b = str(uuid4())
    session.add(
        ChannelOrm(
            id=c_b,
            tenant_id=t_b,
            platform="telegram",
            kind="destination",
            title="Other",
            external_id="@x",
            status="connected",
        )
    )
    session.commit()

    with pytest.raises(ValueError, match="another tenant"):
        create_content_with_plan_and_targets(
            session,
            tenant_id=t_a,
            channel_ids=[c_b],
            title="x",
        )

    session.close()
