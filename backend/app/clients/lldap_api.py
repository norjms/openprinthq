"""LLDAP, for giving invited people a login when the hub signs people in through Authelia with LLDAP behind it.

The hub uses an LLDAP account in the lldap_admin group: it looks people up by email, creates the login of someone
invited who has none, adds people to or takes them out of the hub-users group, and sets the password a person
chooses on the hub's own one-time page (/account/password/...) or changes on /account/password/change, after checking
the current one with a bind as them. People and groups go through LLDAP's GraphQL API;
passwords through LDAP's password modify operation, which LLDAP supports, so the password never passes anywhere
else.
"""
import re
import time

import httpx

from ..config import settings
from .base import ApiError


class LldapClient:
    service = "lldap"

    def __init__(self, url: str, ldap_url: str, base_dn: str, user: str, password: str, group: str):
        self.url = url.rstrip("/")
        self.ldap_url = ldap_url
        self.base_dn = base_dn
        self.bind_user = user   # the hub's own LLDAP account (not self.user: that is the lookup below)
        self.password = password
        self.group = group
        self._token: str | None = None
        self._token_at = 0.0
        self._group_id: int | None = None

    # GraphQL

    def _login(self) -> str:
        if self._token and time.monotonic() - self._token_at < 600:
            return self._token
        try:
            r = httpx.post(f"{self.url}/auth/simple/login", json={"username": self.bind_user, "password": self.password},
                           timeout=15)
        except httpx.HTTPError as e:
            raise ApiError(self.service, None, f"LLDAP did not answer: {type(e).__name__}") from None
        if r.status_code != 200:
            raise ApiError(self.service, r.status_code, "LLDAP refused the hub's account")
        self._token, self._token_at = r.json()["token"], time.monotonic()
        return self._token

    def gql(self, query: str, variables: dict | None = None) -> dict:
        try:
            r = httpx.post(f"{self.url}/api/graphql", headers={"Authorization": f"Bearer {self._login()}"},
                           json={"query": query, "variables": variables or {}}, timeout=15)
        except httpx.HTTPError as e:
            raise ApiError(self.service, None, f"LLDAP did not answer: {type(e).__name__}") from None
        body = r.json() if r.content else {}
        if r.status_code != 200 or body.get("errors"):
            message = "; ".join(e.get("message", "") for e in body.get("errors") or []) or f"HTTP {r.status_code}"
            raise ApiError(self.service, r.status_code, message)
        return body.get("data") or {}

    def user_by_email(self, email: str) -> dict | None:
        email = email.strip().lower()
        rows = self.gql('query($e: String!) { users(filters: {eq: {field: "email", value: $e}}) { id email '
                        'displayName groups { id displayName } } }', {"e": email}).get("users") or []
        rows = [r for r in rows if (r.get("email") or "").lower() == email]
        return rows[0] if rows else None

    def user(self, uid: str) -> dict | None:
        try:
            return self.gql('query($u: String!) { user(userId: $u) { id email displayName groups { id displayName } } }',
                            {"u": uid}).get("user")
        except ApiError:
            return None

    def group_id(self, name: str | None = None) -> int:
        name = name or self.group
        if name == self.group and self._group_id is not None:
            return self._group_id
        rows = self.gql("{ groups { id displayName } }").get("groups") or []
        found = next((g["id"] for g in rows if g["displayName"] == name), None)
        if found is None:
            raise ApiError(self.service, None, f"group {name} not found in LLDAP")
        if name == self.group:
            self._group_id = found
        return found

    def group_members(self, name: str) -> list[dict]:
        """The logins in a group: id and email of each."""
        found = self.gql('query($g: Int!) { group(groupId: $g) { users { id email } } }',
                         {"g": self.group_id(name)}).get("group") or {}
        return found.get("users") or []

    def _free_uid(self, email: str) -> str:
        base = re.sub(r"[^a-z0-9._-]", "-", email.split("@")[0].lower()).strip("-.") or "user"
        base = base[:40]
        uid, n = base, 1
        while self.user(uid) is not None:
            n += 1
            uid = f"{base}-{n}"
        return uid

    def create_user(self, email: str, name: str | None) -> str:
        uid = self._free_uid(email)
        self.gql("mutation($u: CreateUserInput!) { createUser(user: $u) { id } }",
                 {"u": {"id": uid, "email": email.strip().lower(), "displayName": (name or "").strip() or email}})
        return uid

    def add_to_group(self, uid: str, group: str | None = None) -> None:
        self.gql("mutation($u: String!, $g: Int!) { addUserToGroup(userId: $u, groupId: $g) { ok } }",
                 {"u": uid, "g": self.group_id(group)})

    def remove_from_group(self, uid: str, group: str | None = None) -> None:
        self.gql("mutation($u: String!, $g: Int!) { removeUserFromGroup(userId: $u, groupId: $g) { ok } }",
                 {"u": uid, "g": self.group_id(group)})

    def create_group(self, name: str) -> int:
        """A new group (a shared folder's), its id."""
        made = self.gql("mutation($n: String!) { createGroup(name: $n) { id } }", {"n": name}).get("createGroup") or {}
        return made.get("id")

    def delete_group(self, name: str) -> None:
        self.gql("mutation($g: Int!) { deleteGroup(groupId: $g) { ok } }", {"g": self.group_id(name)})

    def delete_user(self, uid: str) -> None:
        self.gql("mutation($u: String!) { deleteUser(userId: $u) { ok } }", {"u": uid})

    # LDAP

    def set_password(self, uid: str, password: str) -> None:
        from ldap3 import Connection, Server
        from ldap3.core.exceptions import LDAPException

        admin_dn = f"uid={self.bind_user},ou=people,{self.base_dn}"
        user_dn = f"uid={uid},ou=people,{self.base_dn}"
        try:
            conn = Connection(Server(self.ldap_url, connect_timeout=10), admin_dn, self.password, auto_bind=True,
                              receive_timeout=15)
            try:
                ok = conn.extend.standard.modify_password(user_dn, None, password)
            finally:
                conn.unbind()
        except LDAPException as e:
            raise ApiError(self.service, None, f"LLDAP did not take the password: {type(e).__name__}") from None
        if not ok:
            raise ApiError(self.service, None, "LLDAP did not take the password")

    def check_password(self, uid: str, password: str) -> bool:
        """Whether this is the person's current password: a bind as them, which LLDAP answers for its own users."""
        from ldap3 import Connection, Server
        from ldap3.core.exceptions import LDAPException

        if not password:
            return False
        user_dn = f"uid={uid},ou=people,{self.base_dn}"
        try:
            conn = Connection(Server(self.ldap_url, connect_timeout=10), user_dn, password, receive_timeout=15)
            try:
                return bool(conn.bind())
            finally:
                conn.unbind()
        except LDAPException as e:
            raise ApiError(self.service, None, f"LLDAP did not answer: {type(e).__name__}") from None


def client() -> LldapClient | None:
    s = settings()
    if not (s.lldap_url and s.lldap_password):
        return None
    return LldapClient(s.lldap_url, s.ldap_url, s.ldap_base_dn, s.lldap_user, s.lldap_password, s.lldap_group)
