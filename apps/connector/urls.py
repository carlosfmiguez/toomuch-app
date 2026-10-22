from django.urls import path, re_path

from . import mcp, oauth

urlpatterns = [
    re_path(r"^\.well-known/oauth-protected-resource(?:/.*)?$", oauth.protected_resource_metadata),
    re_path(r"^\.well-known/oauth-authorization-server(?:/.*)?$", oauth.authorization_server_metadata),
    path("oauth/register", oauth.register, name="oauth_register"),
    path("oauth/authorize", oauth.authorize, name="oauth_authorize"),
    path("oauth/token", oauth.token, name="oauth_token"),
    path("oauth/revoke", oauth.revoke, name="oauth_revoke"),
    path("mcp", mcp.endpoint, name="mcp"),
    path("mcp/", mcp.endpoint),
]
