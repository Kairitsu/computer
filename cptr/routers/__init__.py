"""cptr API routers."""

from cptr.routers.admin import router as admin_router
from cptr.routers.audio import router as audio_router
from cptr.routers.auth import router as auth_router
from cptr.routers.automations import router as automations_router
from cptr.routers.bridge import router as bridge_router
from cptr.routers.bridge import webhook_router
from cptr.routers.browser import router as browser_router
from cptr.routers.chat import router as chat_router
from cptr.routers.gateway import router as gateway_router
from cptr.routers.grok import router as grok_router
from cptr.routers.events import router as events_router
from cptr.routers.files import router as files_router
from cptr.routers.git import router as git_router
from cptr.routers.images import router as images_router
from cptr.routers.memory import router as memory_router
from cptr.routers.notifications import router as notifications_router
from cptr.routers.search import router as search_router
from cptr.routers.skills import router as skills_router
from cptr.routers.state import router as state_router
from cptr.routers.terminal import router as terminal_router
from cptr.routers.workspace import router as workspace_router

__all__ = [
    "admin_router",
    "audio_router",
    "auth_router",
    "automations_router",
    "bridge_router",
    "browser_router",
    "webhook_router",
    "chat_router",
    "events_router",
    "files_router",
    "gateway_router",
    "grok_router",
    "git_router",
    "images_router",
    "memory_router",
    "notifications_router",
    "search_router",
    "skills_router",
    "state_router",
    "terminal_router",
    "workspace_router",
]
