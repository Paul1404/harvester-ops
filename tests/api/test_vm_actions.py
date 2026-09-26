"""v1.60.0 : le menu d'actions d'une VM (bin/harvester-resources.py vm et ses routes).

Les chemins des sous-ressources de KubeVirt, la suppression avec le choix des
volumes (annotation posée avant la suppression, puis les volumes vérifiés),
les refus dits avant d'agir (VM arrêtée, agent absent, nœud en maintenance),
et les routes qui en font des actions suivies.
"""

import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "bin" / "lib"))
sys.path.insert(0, str(ROOT / "web"))
import hv_vm as hv  # noqa: E402

_spec = importlib.util.spec_from_file_location("hres_vm", ROOT / "bin" / "harvester-resources.py")
hres = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hres)


def a_vm(extra_volumes=()):
    return {"apiVersion": "kubevirt.io/v1", "kind": "VirtualMachine",
            "metadata": {"name": "web", "namespace": "default", "resourceVersion": "9",
                         "annotations": {hv.VCT: json.dumps([{"metadata": {"name": "web-root"},
                                                              "spec": {"accessModes": ["ReadWriteMany"], "volumeMode": "Block",
                                                                       "resources": {"requests": {"storage": "10Gi"}},
                                                                       "storageClassName": "lh-x"}}])}},
            "spec": {"runStrategy": "RerunOnFailure", "template": {"spec": {
                "domain": {"devices": {"disks": [{"name": "root", "disk": {"bus": "virtio"}},
                                                 {"name": "cloudinitdisk", "disk": {"bus": "virtio"}}]}},
                "volumes": [{"name": "root", "persistentVolumeClaim": {"claimName": "web-root"}},
                            {"name": "cloudinitdisk", "cloudInitNoCloud": {"secretRef": {"name": "web-ci"}}},
                            *extra_volumes]}}}}


class Kube:
    """Un cluster en mémoire : objets par (type, ns, nom), traces des appels."""

    def __init__(self, objs):
        self.objs = dict(objs)
        self.calls = []

    def get(self, kind, ns, name):
        return json.loads(json.dumps(self.objs[(kind, ns, name)])) if (kind, ns, name) in self.objs else None

    def list(self, kind, ns=None, selector=None):
        return [json.loads(json.dumps(o)) for (k, n, _), o in self.objs.items() if k == kind and (ns is None or n == ns)]

    def patch(self, kind, ns, name, patch):
        self.calls.append(("patch", kind, name, patch))
        obj = self.objs.get((kind, ns, name))
        if obj is not None and "metadata" in patch:
            obj.setdefault("metadata", {}).setdefault("annotations", {}).update(patch["metadata"].get("annotations", {}))
        if obj is not None and "spec" in patch:
            obj.setdefault("spec", {}).update(patch["spec"])

    def delete(self, kind, ns, name, cascade=None):
        self.calls.append(("delete", kind, name))
        self.objs.pop((kind, ns, name), None)

    def create(self, obj):
        self.calls.append(("create", obj["kind"], obj["metadata"].get("name") or obj["metadata"].get("generateName")))
        made = json.loads(json.dumps(obj))
        made["metadata"].setdefault("name", (obj["metadata"].get("generateName") or "x-") + "abcde")
        return made

    def run(self, *args, input=None, timeout=None):
        self.calls.append(("run",) + tuple(a for a in args if not str(a).startswith("/tmp")))
        return "{}"


def args(**kw):
    base = {"namespace": "default", "name": "web", "timeout": 30, "remove_volumes": None, "remove_cloudinit": True,
            "new_name": None, "with_data": False, "start": False, "volume": None, "delete_volume": False,
            "claim": None, "bus": "scsi", "node": None, "template_name": None, "description": None,
            "set_default": False, "user_data": None, "network_data": None, "guest_agent": False, "ssh_names": None}
    base.update(kw)
    return type("A", (), base)()


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(hres.time, "sleep", lambda s: None)


def vmi(conds=("Ready",), node="n1"):
    return {"status": {"nodeName": node, "conditions": [{"type": c, "status": "True"} for c in conds]}}


def test_pause_uses_the_vmi_subresource(capsys, monkeypatch):
    k = Kube({(hres.K_VM, "default", "web"): a_vm(), (hres.K_VMI, "default", "web"): vmi()})

    def put(kube, path, body=None):
        kube.calls.append(("put", path))
        kube.objs[(hres.K_VMI, "default", "web")] = vmi(("Ready", "Paused"))
    monkeypatch.setattr(hres, "_put_sub", put)
    assert hres.vm_pause(k, args(), True) == hres.EXIT_OK
    assert ("put", "/apis/subresources.kubevirt.io/v1/namespaces/default/virtualmachineinstances/web/pause") in k.calls
    assert "STEP_EVENT|pause|done|default/web paused" in capsys.readouterr().err


def test_a_stopped_vm_cannot_be_paused_nor_soft_rebooted():
    k = Kube({(hres.K_VM, "default", "web"): a_vm()})
    with pytest.raises(ValueError, match="not running"):
        hres.vm_pause(k, args(), True)
    k.objs[(hres.K_VMI, "default", "web")] = vmi()
    with pytest.raises(ValueError, match="guest agent"):
        hres.vm_softreboot(k, args())


def test_force_stop_halts_then_drops_the_instance():
    k = Kube({(hres.K_VM, "default", "web"): a_vm(), (hres.K_VMI, "default", "web"): vmi()})
    real_run = k.run

    def run(*a, input=None, timeout=None):
        if a[:1] == ("delete",):
            k.objs.pop((hres.K_VMI, "default", "web"), None)
        return real_run(*a, input=input, timeout=timeout)
    k.run = run
    assert hres.vm_force_stop(k, args()) == hres.EXIT_OK
    assert ("patch", hres.K_VM, "web", {"spec": {"runStrategy": "Halted"}}) in k.calls
    assert any(c[0] == "run" and "--grace-period=0" in c for c in k.calls)


def test_delete_annotates_then_checks_the_chosen_volumes():
    k = Kube({(hres.K_VM, "default", "web"): a_vm(),
              ("persistentvolumeclaims", "default", "web-root"): {"metadata": {"name": "web-root"}},
              ("secrets", "default", "web-ci"): {"metadata": {"name": "web-ci"}}})
    assert hres.vm_delete(k, args(remove_volumes="web-root")) == hres.EXIT_OK
    ops = [c[:3] for c in k.calls if c[0] in ("patch", "delete")]
    assert ops[0] == ("patch", hres.K_VM, "web")                          # annotation avant la suppression
    assert k.calls[0][3] == {"metadata": {"annotations": {hv.REMOVED_PVCS: "web-root"}}}
    assert ("delete", hres.K_VM, "web") in ops
    assert ("delete", "persistentvolumeclaims", "web-root") in ops
    assert ("delete", "secrets", "web-ci") in ops                          # plus personne ne s'en sert


def test_delete_keeps_a_cloudinit_secret_another_vm_uses():
    other = a_vm()
    other["metadata"]["name"] = "api"
    k = Kube({(hres.K_VM, "default", "web"): a_vm(), (hres.K_VM, "default", "api"): other,
              ("secrets", "default", "web-ci"): {"metadata": {"name": "web-ci"}}})
    assert hres.vm_delete(k, args()) == hres.EXIT_OK
    assert ("delete", "secrets", "web-ci") not in [c[:3] for c in k.calls]
    with pytest.raises(ValueError, match="not volumes of this VM"):
        hres.vm_delete(Kube({(hres.K_VM, "default", "web"): a_vm()}), args(remove_volumes="nope"))


def test_clone_copies_the_cloudinit_and_checks_first():
    k = Kube({(hres.K_VM, "default", "web"): a_vm(),
              ("persistentvolumeclaims", "default", "web-root"): {"spec": {"storageClassName": "lh-x"}},
              ("secrets", "default", "web-ci"): {"metadata": {"name": "web-ci", "namespace": "default"}, "data": {}}})
    real_run = k.run

    def run(*a, input=None, timeout=None):
        if input:
            vm2 = json.loads(input)
            for t in json.loads(vm2["metadata"]["annotations"][hv.VCT]):
                k.objs[("persistentvolumeclaims", "default", t["metadata"]["name"])] = {"status": {"phase": "Bound"}}
            k.objs[(hres.K_VM, "default", "web2")] = vm2
        return real_run(*a, input=input, timeout=timeout)
    k.run = run
    code = hres.vm_clone(k, args(new_name="web2", with_data=True))
    assert code == hres.EXIT_OK
    kinds = [c[1] for c in k.calls if c[0] == "create"]
    assert kinds == ["Secret", "VirtualMachine"]
    dry = [c for c in k.calls if c[0] == "run" and "--dry-run=server" in c]
    assert dry, "the copy is checked by the cluster before anything is created"
    with pytest.raises(ValueError, match="already exists"):
        hres.vm_clone(k, args(new_name="web2"))


def test_hotplug_only_on_a_running_vm_and_a_free_volume(monkeypatch):
    k = Kube({(hres.K_VM, "default", "web"): a_vm(), ("persistentvolumeclaims", "default", "data"): {},
              ("persistentvolumeclaims", "default", "web-root"): {}})
    with pytest.raises(ValueError, match="stopped"):
        hres.vm_hotplug(k, args(claim="data"), True)
    k.objs[(hres.K_VMI, "default", "web")] = vmi()
    with pytest.raises(ValueError, match="already a disk"):
        hres.vm_hotplug(k, args(claim="web-root"), True)
    puts = []

    def put(kube, path, body=None):
        puts.append((path, body))
        kube.objs[(hres.K_VMI, "default", "web")] = {"status": {"volumeStatus": [{"name": "data", "phase": "Ready"}]}}
    monkeypatch.setattr(hres, "_put_sub", put)
    assert hres.vm_hotplug(k, args(claim="data"), True) == hres.EXIT_OK
    assert puts[0][0].endswith("/virtualmachines/web/addvolume")
    assert puts[0][1]["volumeSource"]["persistentVolumeClaim"] == {"claimName": "data", "hotpluggable": True}
    with pytest.raises(ValueError, match="not plugged while running"):
        hres.vm_hotplug(k, args(volume="root"), False)


def test_migrate_refuses_a_cordoned_or_same_node():
    k = Kube({(hres.K_VMI, "default", "web"): vmi(node="n1"),
              ("nodes", None, "n2"): {"spec": {"unschedulable": True}}})
    with pytest.raises(ValueError, match="already runs on n1"):
        hres.vm_migrate(k, args(node="n1"))
    with pytest.raises(ValueError, match="cordoned"):
        hres.vm_migrate(k, args(node="n2"))
    k.objs[(hres.K_VMIM, "default", "m1")] = {"metadata": {"name": "m1"}, "spec": {"vmiName": "web"}, "status": {"phase": "Running"}}
    with pytest.raises(ValueError, match="already migrating"):
        hres.vm_migrate(k, args())
    assert hres.vm_abort_migration(k, args()) == hres.EXIT_OK
    assert ("delete", hres.K_VMIM, "m1") in k.calls


def test_eject_patches_with_a_version_guard(tmp_path):
    iso = {"name": "iso", "persistentVolumeClaim": {"claimName": "web-iso"}}
    vm = a_vm((iso,))
    vm["spec"]["template"]["spec"]["domain"]["devices"]["disks"].append({"name": "iso", "cdrom": {"bus": "sata"}})
    k = Kube({(hres.K_VM, "default", "web"): vm})
    assert hres.vm_eject(k, args(volume="iso", delete_volume=True)) == hres.EXIT_OK
    patch = [c for c in k.calls if c[0] == "run" and c[1] == "patch"][0]
    ops = json.loads(patch[-1])
    assert ops[0] == {"op": "test", "path": "/metadata/resourceVersion", "value": "9"}
    assert ("delete", "persistentvolumeclaims", "web-iso") in k.calls        # VM arrêtée : le volume part


# -- les routes --------------------------------------------------------------------

import accounts as acc  # noqa: E402
import app as wapp  # noqa: E402

PW = "a long enough password"


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setattr(wapp, "AUTH_OPEN_ALLOWED", False)
    monkeypatch.setattr(wapp, "HTPASSWD_PATH", tmp_path / "none")
    monkeypatch.setattr(wapp, "ROLES_PATH", tmp_path / "none.yaml")
    monkeypatch.setattr(wapp, "_roles_cache", {"mtime": None, "data": None})
    monkeypatch.setattr(wapp, "ACCOUNTS_PATH", tmp_path / "accounts.json")
    monkeypatch.setattr(wapp, "_ACCOUNTS", {"store": None})
    monkeypatch.setattr(wapp, "_LOCAL_SESSIONS", acc.LocalSessions())
    monkeypatch.setattr(wapp, "load_config", lambda: {"clusters": [{"name": "harv1", "kubeconfig": "/kc"}]})
    wapp._accounts().create("ops", PW, "operator")
    wapp._accounts().create("eye", PW, "viewer")
    w = {"actions": [], "files": []}

    def fake_action(cluster_, label, cmd, tool, spec=None, dry_run=False, after=None):
        files = {cmd[i + 1]: Path(cmd[i + 1]).read_text() for i, a in enumerate(cmd)
                 if a in ("--user-data", "--network-data")}
        w["actions"].append((label, cmd, files))
        if after:
            after()

        class Run:
            id = "vma000000001"
        return Run(), None
    monkeypatch.setattr(wapp, "_cli_action", fake_action)
    objs = {"virtualmachines.kubevirt.io": a_vm(),
            "virtualmachineinstances.kubevirt.io": vmi(("Ready", "AgentConnected"), node="n1"),
            "virtualmachineinstancemigrations.kubevirt.io": {"items": []},
            "nodes": {"items": [{"metadata": {"name": "n1"}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}},
                                {"metadata": {"name": "n2"}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}},
                                {"metadata": {"name": "n3"}, "spec": {"unschedulable": True},
                                 "status": {"conditions": [{"type": "Ready", "status": "True"}]}}]},
            "keypairs.harvesterhci.io": {"spec": {"publicKey": "ssh-ed25519 AAAA ops@x"}}}
    monkeypatch.setattr(wapp, "_kubectl_json", lambda kc, *a, **k: json.loads(json.dumps(objs.get(a[1]))))
    w["objs"] = objs
    return w


def auth(user):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{PW}".encode()).decode()}


def test_the_menu_state(world):
    with wapp.app.test_client() as c:
        d = c.get("/api/vm/harv1/default/web/state", headers=auth("eye")).get_json()
    assert d["running"] and d["agent"] and not d["paused"] and not d["migrating"]
    assert d["node"] == "n1" and d["targets"] == ["n2"]          # ni lui-même ni un nœud en maintenance
    assert d["cloudinit_secrets"] == ["web-ci"]
    assert [v["claim"] for v in d["volumes"] if v["claim"]] == ["web-root"]


def test_actions_are_checked_then_run_by_the_tool(world):
    with wapp.app.test_client() as c:
        assert c.post("/api/vm/harv1/default/web/do/pause", headers=auth("eye")).status_code == 403
        assert c.post("/api/vm/harv1/default/web/do/explode", headers=auth("ops")).status_code == 400
        assert c.post("/api/vm/harv1/default/web/do/clone", json={"new_name": "Bad Name"},
                      headers=auth("ops")).status_code == 400
        assert c.post("/api/vm/harv1/default/web/do/add-volume", json={"claim": "data", "bus": "ide"},
                      headers=auth("ops")).status_code == 400
        r = c.post("/api/vm/harv1/default/web/do/clone", json={"new_name": "web2", "with_data": True},
                   headers=auth("ops"))
        assert r.status_code == 202
        assert c.post("/api/vm/harv1/default/web/do/migrate", json={"node": "n2"}, headers=auth("ops")).status_code == 202
    label, cmd, _ = world["actions"][0]
    assert label == "vm:clone:default/web" and cmd[2:4] == ["vm", "clone"]
    assert cmd[-3:] == ["--new-name", "web2", "--with-data"]
    assert world["actions"][1][1][-2:] == ["--node", "n2"]


def test_deleting_a_vm_with_its_chosen_volumes(world):
    with wapp.app.test_client() as c:
        assert c.delete("/api/vm/harv1/default/web", json={"remove_volumes": ["../x"]},
                        headers=auth("ops")).status_code == 400
        r = c.delete("/api/vm/harv1/default/web", json={"remove_volumes": ["web-root"]}, headers=auth("ops"))
        assert r.status_code == 202 and r.get_json()["removed"] == ["web-root"]
    cmd = world["actions"][-1][1]
    assert cmd[2:4] == ["vm", "delete"] and cmd[-2:] == ["--remove-volumes", "web-root"]


def test_saving_cloudinit_is_a_tracked_action(world):
    with wapp.app.test_client() as c:
        r = c.put("/api/vm/harv1/default/web/cloudinit", headers=auth("ops"),
                  json={"userData": "#cloud-config\n", "networkData": "", "guestAgent": True, "sshNames": ["k1"]})
        assert r.status_code == 202
    label, cmd, files = world["actions"][-1]
    assert label == "vm:cloudinit:default/web" and "--guest-agent" in cmd
    assert cmd[-2:] == ["--ssh-names", "k1"]
    assert "#cloud-config\n" in files.values()
    assert not any(Path(f).exists() for f in files)                   # fichiers effacés après l'action


def test_creating_a_vm_keeps_its_cloudinit(world, monkeypatch):
    seen = {}

    def fake_track(label, cluster, fn, *a):
        seen["args"] = a
        return "create00000001"
    monkeypatch.setattr(wapp, "track_action", fake_track)
    body = {"namespace": "default", "name": "web", "manifest": a_vm(), "ssh_keys": ["default/ops"],
            "guest_agent": True, "cloudinit": {"user_data": "#cloud-config\nhostname: web\n", "network_data": ""}}
    with wapp.app.test_client() as c:
        r = c.post("/api/vms/harv1/create", json=body, headers=auth("ops"))
        assert r.status_code == 202, r.get_json()
    ci = seen["args"][-1]
    import yaml
    data = yaml.safe_load(ci["user_data"])
    assert data["hostname"] == "web" and data["ssh_authorized_keys"] == ["ssh-ed25519 AAAA ops@x"]
    assert "qemu-guest-agent" in data["packages"] and ci["ssh_names"] == ["ops"]


def test_the_create_runner_makes_one_secret_per_vm(monkeypatch):
    created = []

    class R:
        returncode, stdout, stderr = 0, "ok", ""

    def fake_run(cmd, input=None, **k):
        created.append(json.loads(input))
        return R()
    monkeypatch.setattr(wapp.subprocess, "run", fake_run)
    run = wapp.ActionRun("t00000000001", "vm-create:default/web", "harv1", [], dry_run=False)
    manifest = a_vm()
    manifest["spec"]["template"]["spec"]["volumes"] = manifest["spec"]["template"]["spec"]["volumes"][:1]
    wapp._vm_create_runner(run, "harv1", "/kc", "default", ["web-01", "web-02"], False, manifest, False,
                           {"user_data": "#cloud-config\n", "network_data": "", "ssh_names": ["ops"]})
    kinds = [o["kind"] for o in created]
    assert kinds == ["Secret", "VirtualMachine", "Secret", "VirtualMachine"]
    s1, vm1 = created[0], created[1]
    assert hv.cloudinit_secrets(vm1) == [s1["metadata"]["name"]]
    assert vm1["metadata"]["annotations"][hv.SSH_NAMES] == '["ops"]'
    assert created[0]["metadata"]["name"] != created[2]["metadata"]["name"]


def test_no_migration_while_the_vm_node_is_unknown():
    """Vu sur harvlab : un nœud momentanément inconnu laissait viser le nœud
    même de la VM, et la migration restait à « Scheduling »."""
    k = Kube({(hres.K_VMI, "default", "web"): {"status": {"conditions": []}}})
    with pytest.raises(ValueError, match="not known yet"):
        hres.vm_migrate(k, args(node="n2"))


def test_the_state_offers_no_target_without_a_known_node(world):
    world["objs"]["virtualmachineinstances.kubevirt.io"] = {"status": {"conditions": []}}
    with wapp.app.test_client() as c:
        d = c.get("/api/vm/harv1/default/web/state", headers=auth("eye")).get_json()
    assert d["running"] and d["node"] is None and d["targets"] == []
