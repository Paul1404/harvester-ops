"""v1.73.0 : les membres Rancher d'un cluster et de ses projets : rôles
proposés selon le contexte, liaisons nommées (utilisateur, groupe, compte
système), corps de l'API de Rancher, retrait refusé pour un compte système
ou le dernier propriétaire."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin" / "lib"))
import hv_members as mb  # noqa: E402

TEMPLATES = [{"id": "cluster-owner", "name": "Cluster Owner", "context": "cluster", "builtin": True},
             {"id": "cluster-member", "name": "Cluster Member", "context": "cluster", "builtin": True},
             {"id": "nodes-view", "name": "View Nodes", "context": "cluster", "builtin": True},
             {"id": "old", "name": "Old", "context": "cluster", "locked": True},
             {"id": "project-owner", "name": "Project Owner", "context": "project"}]
# vu sur le Rancher de harv1 : l'admin, l'agent du cluster, fleet
BINDINGS = [{"id": "c-sg2q6:creator-cluster-owner", "name": "creator-cluster-owner", "userId": "user-kk67j",
             "userPrincipalId": "local://user-kk67j", "roleTemplateId": "cluster-owner"},
            {"id": "c-sg2q6:u-s6n3mci3el-admin", "name": "u-s6n3mci3el-admin", "userId": "u-s6n3mci3el",
             "userPrincipalId": "system://c-sg2q6", "roleTemplateId": "cluster-owner"},
            {"id": "c-sg2q6:c-sg2q6-fleet-default-owner", "name": "c-sg2q6-fleet-default-owner", "userId": "u-irmawbmaz4",
             "roleTemplateId": "cluster-owner"},
            {"id": "c-sg2q6:crtb-x1", "name": "crtb-x1", "groupPrincipalId": "keycloakoidc_group://rancher-admins",
             "roleTemplateId": "cluster-member"}]
USERS = {"user-kk67j": {"displayName": "Default Admin", "username": "admin"},
         "u-s6n3mci3el": {"displayName": "System account for Cluster c-sg2q6"}}


def test_the_roles_offered_follow_the_context():
    assert [r["id"] for r in mb.role_rows(TEMPLATES, "cluster")] == ["cluster-owner", "cluster-member", "nodes-view"]
    assert [r["id"] for r in mb.role_rows(TEMPLATES, "project")] == ["project-owner"]


def test_members_are_named_and_system_accounts_marked():
    rows = mb.member_rows(BINDINGS, users=USERS, roles={"cluster-owner": "Cluster Owner", "cluster-member": "Cluster Member"})
    assert [(r["name"], r["kind"], r["system"]) for r in rows] == [
        ("Default Admin", "user", False), ("keycloakoidc_group://rancher-admins", "group", False),
        ("System account for Cluster c-sg2q6", "user", True), ("u-irmawbmaz4", "user", True)]
    assert rows[1]["provider"] == "keycloakoidc_group" and rows[0]["role_name"] == "Cluster Owner"


def test_a_binding_body_uses_the_group_or_user_principal():
    assert mb.binding_body("cluster", "c-sg2q6", "keycloakoidc_user://jdoe", "cluster-member") == {
        "type": "clusterRoleTemplateBinding", "clusterId": "c-sg2q6", "roleTemplateId": "cluster-member",
        "userPrincipalId": "keycloakoidc_user://jdoe"}
    assert mb.binding_body("project", "c-sg2q6:p-abcde", "keycloakoidc_group://ops", "project-member")["groupPrincipalId"] == \
        "keycloakoidc_group://ops"
    with pytest.raises(ValueError, match="Rancher principal"):
        mb.binding_body("cluster", "c-sg2q6", "jdoe", "cluster-member")


def test_system_accounts_and_the_last_owner_stay():
    rows = mb.member_rows(BINDINGS, users=USERS)
    with pytest.raises(ValueError, match="system account"):
        mb.check_removal(rows, "c-sg2q6:u-s6n3mci3el-admin", "cluster")
    with pytest.raises(ValueError, match="last cluster-owner"):
        mb.check_removal(rows, "c-sg2q6:creator-cluster-owner", "cluster")
    assert mb.check_removal(rows, "c-sg2q6:crtb-x1", "cluster")["kind"] == "group"
