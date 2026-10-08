"""cptr API routers."""

from cptr.routers.admin import router as admin_router
from cptr.routers.auth import router as auth_router
from cptr.routers.automations import router as automations_router
from cptr.routers.chat import router as chat_router
from cptr.routers.grok import router as grok_router
from cptr.routers.events import router as events_router
from cptr.routers.files import router as files_router
from cptr.routers.git import router as git_router
from cptr.routers.search import router as search_router
from cptr.routers.state import router as state_router
from cptr.routers.update import router as update_router
from cptr.routers.workspace import router as workspace_router

__all__ = [
    "admin_router",
    "auth_router",
    "automations_router",
    "chat_router",
    "events_router",
    "files_router",
    "grok_router",
    "git_router",
    "search_router",
    "state_router",
    "update_router",
    "workspace_router",
]
