import pytest
import pytest_asyncio
from datasette.app import Datasette
from datasette.actor_context import ActorContext
from datasette.resources import DatabaseResource, TableResource
from datasette.utils.asgi import Forbidden


@pytest_asyncio.fixture
async def test_ds():
    ds = Datasette()
    await ds.invoke_startup()
    db = ds.add_memory_database("test_db")
    await db.execute_write("create table if not exists test_table (id integer primary key, name text)")
    await db.execute_write("insert or ignore into test_table (id, name) values (1, 'test')")
    await ds.client.get("/")
    return ds


class TestActorContext:
    @pytest.mark.asyncio
    async def test_actor_context_allowed_matches_datasette_allowed(self, test_ds):
        actor = {"id": "test_user"}
        actor_ctx = ActorContext(test_ds, actor)
        
        direct_result = await test_ds.allowed(
            action="view-instance",
            actor=actor
        )
        ctx_result = await actor_ctx.allowed(action="view-instance")
        
        assert direct_result == ctx_result
    
    @pytest.mark.asyncio
    async def test_actor_context_allowed_with_resource(self, test_ds):
        actor = {"id": "test_user"}
        actor_ctx = ActorContext(test_ds, actor)
        resource = DatabaseResource(database="test_db")
        
        direct_result = await test_ds.allowed(
            action="view-database",
            resource=resource,
            actor=actor
        )
        ctx_result = await actor_ctx.allowed(
            action="view-database",
            resource=resource
        )
        
        assert direct_result == ctx_result
    
    @pytest.mark.asyncio
    async def test_actor_context_ensure_permission_raises_forbidden(self, test_ds):
        actor_ctx = ActorContext(test_ds, None)
        
        test_ds.config["allow"] = {"id": "root"}
        
        with pytest.raises(Forbidden):
            await actor_ctx.ensure_permission(action="view-instance")
        
        del test_ds.config["allow"]
    
    @pytest.mark.asyncio
    async def test_actor_context_check_visibility_matches(self, test_ds):
        actor = {"id": "test_user"}
        actor_ctx = ActorContext(test_ds, actor)
        resource = DatabaseResource(database="test_db")
        
        direct_result = await test_ds.check_visibility(
            actor,
            action="view-database",
            resource=resource
        )
        ctx_result = await actor_ctx.check_visibility(
            action="view-database",
            resource=resource
        )
        
        assert direct_result == ctx_result
    
    @pytest.mark.asyncio
    async def test_actor_context_none_actor(self, test_ds):
        actor_ctx = ActorContext(test_ds, None)
        
        direct_result = await test_ds.allowed(
            action="view-instance",
            actor=None
        )
        ctx_result = await actor_ctx.allowed(action="view-instance")
        
        assert direct_result == ctx_result
    
    @pytest.mark.asyncio
    async def test_actor_context_allowed_resources(self, test_ds):
        actor = {"id": "test_user"}
        actor_ctx = ActorContext(test_ds, actor)
        
        direct_result = await test_ds.allowed_resources(
            action="view-database",
            actor=actor
        )
        ctx_result = await actor_ctx.allowed_resources(
            action="view-database"
        )
        
        direct_resources = [r async for r in direct_result.all()]
        ctx_resources = [r async for r in ctx_result.all()]
        
        assert len(direct_resources) == len(ctx_resources)


class TestPermissionErrorsConsistency:
    @pytest.mark.asyncio
    async def test_forbidden_exception_same_type(self, test_ds):
        actor_ctx = ActorContext(test_ds, None)
        
        test_ds.config["allow"] = {"id": "root"}
        
        try:
            await actor_ctx.ensure_permission(action="view-instance")
        except Forbidden as e:
            assert isinstance(e, Forbidden)
        
        del test_ds.config["allow"]


class TestViewPermissions:
    @pytest.mark.asyncio
    async def test_index_view_uses_actor_context(self, test_ds):
        from datasette.views.index import IndexView
        
        view = IndexView(test_ds)
        test_ds.config["allow"] = {"id": "root"}
        
        class MockRequest:
            def __init__(self):
                self.actor = None
                self.url_vars = {"format": None}
                self.args = {}
                self.path = "/"
        
        request = MockRequest()
        
        with pytest.raises(Forbidden):
            await view.get(request)
        
        del test_ds.config["allow"]
