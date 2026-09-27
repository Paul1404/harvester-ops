"""harvester-ops : les membres Rancher d'un cluster Harvester et de ses
projets (v1.73.0).

Rancher donne des droits sur un cluster par des ClusterRoleTemplateBinding
(un utilisateur ou un groupe, un rôle de contexte « cluster ») et sur un
projet par des ProjectRoleTemplateBinding (rôle de contexte « project »).
Ils n'existent que sur le serveur Rancher ; la console y passe avec le jeton
de la personne, comme pour les projets (v1.72.0). Les comptes système de
Rancher (agent du cluster, fleet) portent aussi des liaisons : la console
les montre sans permettre de les retirer, ni de retirer le dernier
propriétaire humain du cluster.
"""

import re

PRINCIPAL_RE = re.compile(r"^[a-z][a-z0-9_]*://[^\s]{1,300}$")
BINDING_RE = re.compile(r"^[a-z0-9:-]{3,120}$")
OWNER = {"cluster": "cluster-owner", "project": "project-owner"}


def check_principal(pid):
    pid = str(pid or "").strip()
    if not PRINCIPAL_RE.match(pid):
        raise ValueError("member: a Rancher principal (local://u-xxxxx, keycloakoidc_user://..., ..._group://...)")
    return pid


def check_binding(bid):
    bid = str(bid or "").strip()
    if not BINDING_RE.match(bid):
        raise ValueError("member: the id of a role binding")
    return bid


def role_rows(templates, context):
    """Les rôles qu'on peut donner dans ce contexte : ni cachés, ni
    verrouillés, ni externes ; propriétaire et membre d'abord."""
    out = []
    for r in templates or []:
        if r.get("context") != context or r.get("hidden") or r.get("locked") or r.get("external"):
            continue
        out.append({"id": r.get("id"), "name": r.get("name") or r.get("id"), "builtin": bool(r.get("builtin")),
                    "description": r.get("description") or ""})
    first = {OWNER[context]: 0, f"{context}-member": 1, "read-only": 2}
    return sorted(out, key=lambda r: (first.get(r["id"], 9), r["name"].lower()))


def principal_kind(pid):
    p = str(pid or "")
    return "group" if "_group://" in p or p.startswith("group://") else "user"


def member_rows(bindings, principals=None, users=None, roles=None):
    """Liaisons -> lignes : qui (nom, type, fournisseur), quel rôle, compte
    système ou non. `principals` {id: {name, loginName}}, `users` {u-id:
    {displayName, username}} servent à nommer ; `roles` {id: name}."""
    principals, users, roles = principals or {}, users or {}, roles or {}
    out = []
    for b in bindings or []:
        pid = b.get("groupPrincipalId") or b.get("userPrincipalId") or ""
        uid = b.get("userId") or ""
        p = principals.get(pid) or {}
        u = users.get(uid) or {}
        display = u.get("displayName") or u.get("name") or ""
        system = display.startswith("System account") or (b.get("name") or "").endswith("-fleet-default-owner")
        out.append({"id": b.get("id"), "principal": pid, "user": uid, "kind": principal_kind(pid) if pid else "user",
                    "name": p.get("name") or display or u.get("username") or p.get("loginName") or uid or pid,
                    "login": p.get("loginName") or u.get("username") or "",
                    "provider": pid.split("://", 1)[0] if "://" in pid else "",
                    "role": b.get("roleTemplateId") or "", "role_name": roles.get(b.get("roleTemplateId"), b.get("roleTemplateId") or ""),
                    "system": system, "created": b.get("created")})
    return sorted(out, key=lambda r: (r["system"], r["kind"] != "user", r["name"].lower()))


def binding_body(scope, target, principal, role):
    """Corps de /v3/clusterroletemplatebindings ou projectroletemplatebindings."""
    principal = check_principal(principal)
    if not str(role or "").strip():
        raise ValueError("role: a role template")
    key = "groupPrincipalId" if principal_kind(principal) == "group" else "userPrincipalId"
    if scope == "cluster":
        return {"type": "clusterRoleTemplateBinding", "clusterId": target, "roleTemplateId": role, key: principal}
    if scope == "project":
        return {"type": "projectRoleTemplateBinding", "projectId": target, "roleTemplateId": role, key: principal}
    raise ValueError("scope: cluster or project")


def check_removal(rows, bid, scope):
    """Retirer une liaison : jamais celle d'un compte système de Rancher, ni
    le dernier propriétaire humain (le cluster ou le projet deviendrait
    ingérable sauf par l'administrateur global)."""
    row = next((r for r in rows or [] if r["id"] == bid), None)
    if row is None:
        raise ValueError("member: no such role binding")
    if row["system"]:
        raise ValueError(f"{row['name']} is a Rancher system account: its binding stays")
    owner = OWNER[scope]
    if row["role"] == owner and not any(r["role"] == owner and not r["system"] and r["id"] != bid for r in rows):
        raise ValueError(f"{row['name']} is the last {owner}: add another owner first")
    return row
