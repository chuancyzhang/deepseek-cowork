"""HTTP adapter for team spaces; both clients use the same authorization rules."""
import logging
from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse

from .security import PlatformError

log = logging.getLogger(__name__)


def team_space_router(service, principal, read_json):
    router = APIRouter(prefix="/api/v1")

    @router.get("/team-spaces")
    def spaces(request: Request, search: str = "", archived: bool = False,
               offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
        return service.list(principal(request)["id"], search=search, archived=archived, offset=offset, limit=limit)

    @router.post("/team-spaces")
    async def create(request: Request):
        actor = principal(request)["id"]
        return service.execute(actor, "", "space.create", await read_json(request, 65536))

    @router.get("/team-space-invites")
    def invites(request: Request):
        return service.inbox(principal(request)["id"])

    @router.get("/team-space-members")
    def candidates(request: Request, search: str = Query("", max_length=200)):
        return service.candidates(principal(request)["id"], search)

    @router.get("/team-space-operations/{operation_id}")
    def operation(operation_id: str, request: Request):
        return service.operation(principal(request)["id"], operation_id)

    @router.get("/team-spaces/{space_id}")
    def detail(space_id: str, request: Request):
        return service.detail(principal(request)["id"], space_id)

    @router.post("/team-spaces/{space_id}/actions/{action}")
    async def execute(space_id: str, action: str, request: Request):
        actor = principal(request)["id"]
        try:
            return service.execute(actor, space_id, action, await read_json(request, 1024 * 1024))
        except PlatformError as error:
            log.warning("space.operation_failed space=%s member=%s action=%s code=%s", space_id, actor, action, error.code)
            raise

    @router.post("/team-spaces/{space_id}/model-policy")
    async def model_policy(space_id: str, request: Request):
        actor = principal(request)["id"]
        payload = await read_json(request, 32768)
        if set(payload) - {"source", "model_id", "assets", "snapshots"}:
            raise PlatformError("invalid_policy_request", "模型策略核对不接收请求正文、凭据或私人任务信息。")
        return service.policy(actor, space_id, payload.get("source"), payload.get("model_id", ""),
                              assets=payload.get("assets", []), snapshots=payload.get("snapshots", []))

    @router.get("/team-spaces/{space_id}/objects/{kind}/{object_id}")
    def object_detail(space_id: str, kind: str, object_id: str, request: Request):
        return service.get(principal(request)["id"], space_id, kind, object_id)

    @router.get("/team-spaces/{space_id}/shares/{share_id}/withdrawal-preview")
    def withdrawal_preview(space_id: str, share_id: str, request: Request):
        return service.withdrawal_preview(principal(request)["id"], space_id, share_id)

    @router.put("/team-spaces/{space_id}/uploads/{upload_id}/content")
    async def receive(space_id: str, upload_id: str, request: Request):
        actor = principal(request)["id"]
        return await service.receive(actor, space_id, upload_id, request.stream())

    @router.get("/team-spaces/{space_id}/files/{revision_id}")
    def download(space_id: str, revision_id: str, request: Request):
        path, filename, sha256 = service.download(principal(request)["id"], space_id, revision_id)
        return FileResponse(path, media_type="application/octet-stream", filename=filename,
                            headers={"X-Content-SHA256": sha256})

    @router.get("/team-spaces/{space_id}/{collection}")
    def collection(space_id: str, collection: str, request: Request, offset: int = Query(0, ge=0),
                   limit: int = Query(50, ge=1, le=100), search: str = Query("", max_length=200),
                   after: int = Query(0, ge=0), target_kind: str = "", target_id: str = "",
                   assignee_id: str = "", state: str = "", folder_id: str | None = None):
        return service.collection(principal(request)["id"], space_id, collection, offset=offset, limit=limit,
            search=search, after=after, target_kind=target_kind, target_id=target_id,
            assignee_id=assignee_id, state=state, folder_id=folder_id)

    @router.get("/admin/members/{member_id}/owned-spaces")
    def recovery_spaces(member_id: str, request: Request):
        return service.owner_recovery_list(principal(request, admin=True)["id"], member_id)

    @router.post("/admin/team-spaces/{space_id}/recover-owner")
    async def recover_owner(space_id: str, request: Request):
        actor = principal(request, admin=True)["id"]
        return service.recover_owner(actor, space_id, await read_json(request, 8192))

    return router
