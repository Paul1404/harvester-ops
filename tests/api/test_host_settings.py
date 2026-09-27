"""v1.62.0 : la configuration d'un hôte comme dans Harvester (bin/lib/hv_host.py,
bin/harvester-resources.py host et ses routes).

Les formats sont ceux de Harvester 1.9 : annotations du Node, tags du nœud
Longhorn, BlockDevice provisionné, Hugepage et Ksmtuned du node-manager,
annotation du CPU manager, Inventory du seeder. Les refus du webhook sont
dits avant d'agir ; le mot de passe du BMC ne passe que par un fichier privé.
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
import hv_host as hh  # noqa: E402

_spec = importlib.util.spec_from_file_location("hres_host", ROOT / "bin" / "harvester-resources.py")
hres = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hres)


def a_node(name="n1", labels=None, ann=None):
    return {"metadata": {"name": name, "labels": {"kubernetes.io/hostname": name, "cpumanager": "false",
                                                   "rack": "r2", **(labels or {})},
                         "annotations": dict(ann or {})}}


def a_bd(name="bd1", node="n1", provision=False, mount="", state="Active", dtype="disk", fs_type=""):
    return {"apiVersion": "harvesterhci.io/v1beta1", "kind": "BlockDevice",
            "metadata": {"name": name, "namespace": "longhorn-system", "resourceVersion": "5"},
            "spec": {"nodeName": node, "devPath": f"/dev/{name}", "provision": provision, "fileSystem": {}},
            "status": {"state": state, "provisionPhase": "Provisioned" if provision else "Unprovisioned",
                       "deviceStatus": {"devPath": f"/dev/{name}", "capacity": {"sizeBytes": 10 * 2**30},
                                        "details": {"deviceType": dtype},
                                        "fileSystem": {"mountPoint": mount, "type": fs_type}},
                       "conditions": []}}


def an_lh(tags=(), disks=None):
    return {"metadata": {"name": "n1", "namespace": "longhorn-system"},
            "spec": {"tags": list(tags), "disks": disks or {}},
            "status": {"diskStatus": {}}}


# -- les formats --------------------------------------------------------------------

def test_basics_patch_writes_the_harvester_annotations_and_nulls_removed_labels():
    node = a_node()
    p = hh.basics_patch(node, " rack 2 ", "https://10.0.0.21", {"zone": "a"})
    assert p["metadata"]["annotations"] == {hh.ANN_NAME: "rack 2", hh.ANN_CONSOLE: "https://10.0.0.21"}
    assert p["metadata"]["labels"] == {"zone": "a", "rack": None}                # le système n'est pas touché
    assert "kubernetes.io/hostname" not in p["metadata"]["labels"]
    assert hh.basics_patch(node, "", "")["metadata"]["annotations"] == {hh.ANN_NAME: None, hh.ANN_CONSOLE: None}
    with pytest.raises(ValueError, match="console URL"):
        hh.basics_patch(node, console_url="10.0.0.21")
    with pytest.raises(ValueError, match="reserved"):
        hh.basics_patch(node, labels={"kubevirt.io/schedulable": "true"})
    ovn = a_node(labels={"kube-ovn/role": "master"})                     # vu sur harv1
    assert "kube-ovn/role" not in hh.user_labels(ovn)
    assert "kube-ovn/role" not in hh.basics_patch(ovn, labels={})["metadata"]["labels"]
    assert hh.basics_patch(node, labels={"topology.kubernetes.io/zone": "z1"})["metadata"]["labels"][
        "topology.kubernetes.io/zone"] == "z1"                                  # montré par Harvester


def test_block_devices_offer_only_what_harvester_offers():
    bds = [a_bd("sdb"), a_bd("sdc", mount="/var/lib/x"), a_bd("sdd", dtype="part"),
           a_bd("sde", provision=True), a_bd("sdf", node="n2"), a_bd("sdg", state="Inactive")]
    lh = an_lh(disks={"sde": {"tags": ["ssd"], "allowScheduling": True}})
    lh["status"]["diskStatus"]["sde"] = {"storageAvailable": 5, "storageMaximum": 10,
                                         "conditions": [{"type": "Ready", "status": "True"},
                                                        {"type": "Schedulable", "status": "True"}]}
    rows = {r["name"]: r for r in hh.block_devices(bds, "n1", lh)}
    assert set(rows) == {"sdb", "sdc", "sdd", "sde", "sdg"}                     # pas le disque d'un autre nœud
    assert [n for n, r in rows.items() if r["addable"]] == ["sdb"]
    assert rows["sde"]["in_longhorn"] and rows["sde"]["tags"] == ["ssd"] and rows["sde"]["ready"]
    assert list(rows)[0] == "sde"                                                # les disques du stockage d'abord
    lh["spec"]["disks"]["default-disk-1"] = {"path": "/var/lib/harvester/defaultdisk", "tags": ["sys"], "allowScheduling": True}
    rows = {r["name"]: r for r in hh.block_devices(bds, "n1", lh)}
    d = rows["default-disk-1"]                                                   # le disque par défaut, comme Harvester
    assert d["in_longhorn"] and d["tags"] == ["sys"] and d["removable"] is False and not d["addable"]
    assert rows["sde"]["removable"] is True


def test_disk_add_and_remove_follow_node_disk_manager():
    out = hh.disk_add(a_bd("sdb"))
    assert out["spec"]["provision"] is True and out["spec"]["fileSystem"]["forceFormatted"] is True
    assert out["spec"]["provisioner"] == {"longhorn": {"engineVersion": "LonghornV1"}}
    assert out["status"]["state"] == "Active"                 # le CRD exige status (vu sur harvlab)
    assert hh.disk_add(a_bd("sdb", fs_type="xfs"))["spec"]["fileSystem"]["forceFormatted"] is False
    assert hh.disk_add(a_bd("sdb"), provisioner="lvm", vg="vg1")["spec"]["provisioner"] == {"lvm": {"vgName": "vg1"}}
    with pytest.raises(ValueError, match="mounted"):
        hh.disk_add(a_bd("sdc", mount="/x"))
    with pytest.raises(ValueError, match="already"):
        hh.disk_add(a_bd("sde", provision=True))
    assert hh.disk_remove(a_bd("sde", provision=True))["spec"]["provision"] is False
    with pytest.raises(ValueError, match="not added"):
        hh.disk_remove(a_bd("sdb"))


def test_tags_and_disk_settings_go_to_the_longhorn_node():
    lh = an_lh(disks={"sde": {}})
    assert hh.lh_node_patch(lh, tags=["fast", " ssd "]) == {"spec": {"tags": ["fast", "ssd"]}}
    assert hh.lh_node_patch(lh, disk="sde", disk_tags=["nvme"], scheduling=False) == \
        {"spec": {"disks": {"sde": {"tags": ["nvme"], "allowScheduling": False}}}}
    with pytest.raises(ValueError, match="once"):
        hh.lh_node_patch(lh, tags=["a", "a"])
    with pytest.raises(ValueError, match="no Longhorn disk"):
        hh.lh_node_patch(lh, disk="sdz", scheduling=True)


def test_hugepages_and_ksmtuned_values():
    assert hh.hugepage_patch("madvise", None, "defer") == {"spec": {"transparent": {"enabled": "madvise", "defrag": "defer"}}}
    with pytest.raises(ValueError):
        hh.hugepage_patch("sometimes")
    p = hh.ksmtuned_patch("run", "high", 20, True)
    assert p["spec"]["ksmtunedParameters"] == {"sleepMsec": 20, "boost": 200, "decay": 50, "minPages": 100, "maxPages": 10000}
    assert p["spec"]["mergeAcrossNodes"] == 1 and p["spec"]["thresCoef"] == 20
    with pytest.raises(ValueError, match="minPages"):
        hh.ksmtuned_patch(mode="customized", params={"sleepMsec": 1, "boost": 1, "decay": 1, "minPages": 9, "maxPages": 2})
    with pytest.raises(ValueError, match="threshold"):
        hh.ksmtuned_patch(thres=120)


def test_cpu_manager_request_applies_the_webhook_rules():
    node = a_node()
    p = hh.cpu_manager_request(node, True)
    assert json.loads(p["metadata"]["annotations"][hh.ANN_CPU]) == {"policy": "static", "status": "requested"}
    with pytest.raises(ValueError, match="already disabled"):
        hh.cpu_manager_request(node, False)
    with pytest.raises(ValueError, match="witness"):
        hh.cpu_manager_request(a_node(labels={"node-role.harvesterhci.io/witness": "true"}), True)
    busy = a_node(ann={hh.ANN_CPU: json.dumps({"policy": "static", "status": "running"})})
    with pytest.raises(ValueError, match="in progress"):
        hh.cpu_manager_request(busy, True)
    on = a_node(labels={"cpumanager": "true"})
    pinned = {"metadata": {"namespace": "default", "name": "db"},
              "spec": {"domain": {"cpu": {"dedicatedCpuPlacement": True}}}}
    with pytest.raises(ValueError, match="default/db"):
        hh.cpu_manager_request(on, False, [pinned])


def test_inventory_power_and_deletion():
    inv = hh.inventory("n1", "10.0.0.21", 623, "harvester-system", "n1-bmc", insecure=True)
    assert inv["metadata"]["annotations"]["metal.harvesterhci.io/local-node-name"] == "n1"
    assert inv["spec"]["baseboardSpec"]["connection"] == {"host": "10.0.0.21", "port": 623, "insecureTLS": True,
                                                         "authSecretRef": {"name": "n1-bmc", "namespace": "harvester-system"}}
    with pytest.raises(ValueError, match="interval"):
        hh.inventory("n1", "h", 623, "ns", "s", interval="hourly")
    ready = {"spec": {}, "status": {"status": "inventoryNodeReady", "machinePowerState": "on"}}
    with pytest.raises(ValueError, match="maintenance"):
        hh.power_check(a_node(), ready, "reboot")
    maint = a_node(ann={hh.ANN_MAINT: "completed"})
    assert hh.power_check(maint, ready, "reboot") == {"spec": {"powerActionRequested": "reboot"}}
    with pytest.raises(ValueError, match="already on"):
        hh.power_check(maint, ready, "poweron")
    with pytest.raises(ValueError, match="no out-of-band"):
        hh.power_check(maint, None, "shutdown")
    with pytest.raises(ValueError, match="last node"):
        hh.delete_target(a_node(), [a_node()])
    capi = a_node(ann={"cluster.x-k8s.io/machine": "m-1", "cluster.x-k8s.io/cluster-namespace": "fleet-local"})
    assert hh.delete_target(capi, [capi, a_node("n2")]) == ("machines.cluster.x-k8s.io", "fleet-local", "m-1")
    assert hh.delete_target(a_node(), [a_node(), a_node("n2")]) == ("nodes", None, "n1")
    s = hh.bmc_secret("n1", "admin", "s3cret")
    assert base64.b64decode(s["data"]["password"]).decode() == "s3cret" and "stringData" not in s


# -- l'outil ---------------------------------------------------------------------------

class Kube:
    def __init__(self, objs):
        self.objs = dict(objs)
        self.calls = []

    def get(self, kind, ns, name):
        return json.loads(json.dumps(self.objs[(kind, ns, name)])) if (kind, ns, name) in self.objs else None

    def list(self, kind, ns=None, selector=None):
        return [json.loads(json.dumps(o)) for (k, n, _), o in self.objs.items() if k == kind and (ns is None or n == ns)]

    def patch(self, kind, ns, name, patch):
        self.calls.append(("patch", kind, name, patch))

    def replace(self, obj):
        self.calls.append(("replace", obj["kind"], obj["metadata"]["name"]))
        key = next(k for k in self.objs if k[2] == obj["metadata"]["name"] and k[0] == hh.K_BD)
        done = json.loads(json.dumps(obj))
        done["status"] = {"provisionPhase": "Provisioned" if obj["spec"]["provision"] else "Unprovisioned"}
        self.objs[key] = done
        return obj

    def create(self, obj):
        self.calls.append(("create", obj["kind"], obj["metadata"]["name"]))
        return obj

    def delete(self, kind, ns, name, cascade=None):
        self.calls.append(("delete", kind, ns, name))
        self.objs.pop((kind, ns, name), None)


def hargs(**kw):
    base = {"node": "n1", "timeout": 30, "custom_name": None, "console_url": None, "labels_file": None, "tags": None,
            "disk": None, "provisioner": "LonghornV1", "vg": None, "format": None, "scheduling": None,
            "thp_enabled": None, "thp_shmem": None, "thp_defrag": None, "run": None, "mode": None, "thres": None,
            "merge": None, "params": None, "enable": None, "bmc_host": None, "bmc_port": 623, "username": None,
            "password_file": None, "insecure": False, "no_events": False, "interval": "1h", "off": False,
            "operation": None}
    base.update(kw)
    return type("A", (), base)()


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(hres.time, "sleep", lambda s: None)
    wait = hres._wait                        # son `sleep` par défaut est lié à la définition
    monkeypatch.setattr(hres, "_wait", lambda *a, **kw: wait(*a, **{"sleep": lambda s: None, **kw}))


def test_the_tool_adds_a_disk_and_waits_for_it(capsys):
    k = Kube({("nodes", None, "n1"): a_node(), (hh.K_BD, "longhorn-system", "sdb"): a_bd("sdb")})
    assert hres.host_disk_add(k, hargs(disk="sdb", format=True)) == hres.EXIT_OK
    assert ("replace", "BlockDevice", "sdb") in k.calls
    assert "/dev/sdb is a storage disk of n1" in capsys.readouterr().err
    with pytest.raises(ValueError, match="no disk"):
        hres.host_disk_add(k, hargs(node="n2", disk="sdb"))


def test_the_tool_refuses_cpu_manager_without_a_direction():
    k = Kube({("nodes", None, "n1"): a_node()})
    with pytest.raises(ValueError, match="--enable"):
        hres.host_cpu_manager(k, hargs())


def test_the_tool_keeps_the_bmc_password_in_a_secret(tmp_path):
    pw = tmp_path / "pw"
    pw.write_text("s3cret\n")
    k = Kube({("nodes", None, "n1"): a_node(),
              (hres.K_ADDON, "harvester-system", "harvester-seeder"): {"spec": {"enabled": True}}})
    orig = k.create

    def create(obj):
        if obj["kind"] == "Inventory":
            done = json.loads(json.dumps(obj))
            done["status"] = {"status": "inventoryNodeReady", "machinePowerState": "on"}
            k.objs[(hh.K_INVENTORY, "harvester-system", "n1")] = done
        return orig(obj)
    k.create = create
    assert hres.host_oob(k, hargs(bmc_host="10.0.0.21", username="admin", password_file=str(pw))) == hres.EXIT_OK
    assert ("create", "Secret", "n1-bmc") in k.calls and ("create", "Inventory", "n1") in k.calls
    k2 = Kube({("nodes", None, "n1"): a_node(), (hres.K_ADDON, "harvester-system", "harvester-seeder"): {"spec": {}}})
    with pytest.raises(ValueError, match="harvester-seeder"):
        hres.host_oob(k2, hargs(bmc_host="10.0.0.21", username="admin", password_file=str(pw)))


def test_the_tool_deletes_a_capi_machine_and_waits_for_the_node():
    capi = a_node(ann={"cluster.x-k8s.io/machine": "m-1", "cluster.x-k8s.io/cluster-namespace": "fleet-local"})
    k = Kube({("nodes", None, "n1"): capi, ("nodes", None, "n2"): a_node("n2")})
    orig = k.delete

    def delete(kind, ns, name, cascade=None):
        orig(kind, ns, name)
        k.objs.pop(("nodes", None, "n1"), None)                     # le contrôleur retire le Node
    k.delete = delete
    assert hres.host_delete(k, hargs()) == hres.EXIT_OK
    assert ("delete", "machines.cluster.x-k8s.io", "fleet-local", "m-1") in k.calls


# -- les routes --------------------------------------------------------------------------

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
    wapp._accounts().create("root", PW, "admin")
    wapp._accounts().create("ops", PW, "operator")
    wapp._accounts().create("eye", PW, "viewer")
    w = {"actions": []}

    def fake_action(cluster_, label, cmd, tool, spec=None, dry_run=False, after=None):
        files = {cmd[i + 1]: Path(cmd[i + 1]).read_text() for i, a in enumerate(cmd)
                 if a in ("--password-file", "--labels-file", "--params")}
        w["actions"].append((label, cmd, files))
        if after:
            after()

        class Run:
            id = "host00000001"
        return Run(), None
    monkeypatch.setattr(wapp, "_cli_action", fake_action)
    lh = an_lh(tags=["ssd"], disks={"sde": {"tags": [], "allowScheduling": True}})
    objs = {("nodes", "n1"): a_node(ann={hh.ANN_NAME: "rack 2"}), ("nodes", None): {"items": [a_node(), a_node("n2")]},
            (hh.K_LHNODE, "n1"): lh, (hh.K_BD, None): {"items": [a_bd("sdb"), a_bd("sde", provision=True)]},
            (hh.K_HUGEPAGE, "n1"): {"spec": {"transparent": {"enabled": "madvise"}}, "status": {"meminfo": {"HugePages_Total": 0, "Buffers": 1}}},
            (hh.K_KSM, "n1"): {"spec": {"run": "stop"}, "status": {"shared": 0}},
            ("addons.harvesterhci.io", "harvester-seeder"): {"spec": {"enabled": True}},
            (hh.K_INVENTORY, "n1"): {"spec": {"baseboardSpec": {"connection": {
                "host": "10.0.0.21", "port": 623, "authSecretRef": {"name": "n1-bmc", "namespace": "harvester-system"}}}},
                "status": {"status": "inventoryNodeReady", "machinePowerState": "on"}}}

    def kj(kc, *a, **k):
        kind = a[1]
        name = a[2] if len(a) > 2 and not a[2].startswith("-") else None
        return json.loads(json.dumps(objs.get((kind, name))))
    monkeypatch.setattr(wapp, "_kubectl_json", kj)
    return w


def auth(user):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{PW}".encode()).decode()}


def test_the_host_settings_read(world):
    with wapp.app.test_client() as c:
        d = c.get("/api/host/harv1/n1/settings", headers=auth("eye")).get_json()
        assert c.get("/api/host/harv1/Bad_Node/settings", headers=auth("eye")).status_code == 400
    assert d["custom_name"] == "rack 2" and d["labels"] == {"rack": "r2"} and d["tags"] == ["ssd"]
    assert [x["name"] for x in d["disks"] if x["addable"]] == ["sdb"] and d["nodes"] == 2
    assert d["hugepages"]["meminfo"] == {"HugePages_Total": 0}                  # pas toute la mémoire
    assert d["oob"]["secret"] == "harvester-system/n1-bmc" and d["oob"]["power_state"] == "on"
    assert "password" not in json.dumps(d)


def test_host_writes_are_for_admins_and_checked(world):
    with wapp.app.test_client() as c:
        assert c.post("/api/host/harv1/n1/do/tags", json={"tags": ["a"]}, headers=auth("ops")).status_code == 403
        assert c.post("/api/host/harv1/n1/do/explode", headers=auth("root")).status_code == 400
        assert c.post("/api/host/harv1/n1/do/tags", json={"tags": ["bad tag"]}, headers=auth("root")).status_code == 400
        assert c.post("/api/host/harv1/n1/do/power", json={"operation": "nuke"}, headers=auth("root")).status_code == 400
        assert c.post("/api/host/harv1/n1/do/disk-add", json={"disk": "../x"}, headers=auth("root")).status_code == 400
        r = c.post("/api/host/harv1/n1/do/disk-add", json={"disk": "sdb", "format": False}, headers=auth("root"))
        assert r.status_code == 202
        r = c.post("/api/host/harv1/n1/do/basics", json={"custom_name": "rack 3", "labels": {"zone": "a"}},
                   headers=auth("root"))
        assert r.status_code == 202
    label, cmd, _ = world["actions"][0]
    assert label == "host:disk-add:n1" and cmd[2:4] == ["host", "disk-add"] and cmd[-1] == "--no-format"
    label, cmd, files = world["actions"][1]
    assert ["--custom-name", "rack 3"] == cmd[cmd.index("--custom-name"):cmd.index("--custom-name") + 2]
    assert json.loads(list(files.values())[0]) == {"zone": "a"}
    assert not any(Path(f).exists() for f in files)


def test_the_bmc_password_only_travels_in_a_private_file(world):
    with wapp.app.test_client() as c:
        r = c.post("/api/host/harv1/n1/do/oob", headers=auth("root"),
                   json={"host": "10.0.0.21", "port": 623, "username": "admin", "password": "s3cret-pw", "interval": "1h"})
        assert r.status_code == 202
        assert "s3cret-pw" not in r.get_data(as_text=True)
        assert c.post("/api/host/harv1/n1/do/oob", headers=auth("root"),
                      json={"host": "10.0.0.21", "interval": "every hour"}).status_code == 400
    label, cmd, files = world["actions"][0]
    assert "s3cret-pw" not in " ".join(cmd) and list(files.values()) == ["s3cret-pw"]
    assert not any(Path(f).exists() for f in files)                            # effacé après l'action


def test_the_topology_shows_the_custom_name():
    n = wapp._topology_node({"metadata": {"name": "n1", "annotations": {hh.ANN_NAME: "rack 2"}}, "spec": {}, "status": {}})
    assert n["custom_name"] == "rack 2"


def test_the_seeder_error_is_said_in_one_line():
    """Vu sur harvlab : bmclib essaie chaque fournisseur ; Redfish toujours sur
    443, le port de l'Inventory n'étant que celui d'IPMI, limité à 20 octets."""
    msg = ("failed to open connection to BMC: 7 errors occurred:\n\t* provider: gofish: Get "
           "\"https://10.0.0.1:443/redfish/v1/\": dial tcp 10.0.0.1:443: connect: no route to host\n\t"
           "* provider: ipmitool: lanplus: password is longer than 20 bytes.: exit status 1\n\t* no Opener implementations found\n")
    inv = {"status": {"conditions": [{"type": "machineNotContactable", "status": "True", "message": msg}]}}
    assert hh.bmc_error(inv) == "gofish: connection refused or no route; ipmitool: password longer than 20 bytes (IPMI limit)"
    assert hh.bmc_error({"status": {"status": "inventoryNodeReady", "conditions": []}}) is None


def test_power_clears_the_last_job_and_waits_for_the_new_one():
    """Vu sur harvlab : sans vider status.powerAction.lastJobName (comme
    l'action powerAction de Harvester), le seeder ne lançait rien, et l'état
    du travail précédent faisait croire l'action finie."""
    maint = a_node(ann={hh.ANN_MAINT: "completed"})
    inv = {"spec": {"powerActionRequested": "shutdown"},
           "status": {"status": "inventoryNodeReady", "machinePowerState": "off",
                      "powerAction": {"actionStatus": "complete", "lastJobName": "n1-shutdown-aaaaa"}}}
    k = Kube({("nodes", None, "n1"): maint, (hh.K_INVENTORY, "harvester-system", "n1"): inv,
              (hh.K_BMC_JOB, "harvester-system", "n1-shutdown-aaaaa"): {"status": {"conditions": [{"type": "Completed", "status": "True"}]}}})
    polls = {"n": 0}
    real_get = k.get

    def get(kind, ns, name):
        if kind == hh.K_INVENTORY:
            polls["n"] += 1
            if polls["n"] == 3:                                    # le seeder a lancé son travail
                k.objs[(kind, ns, name)]["status"]["powerAction"]["lastJobName"] = "n1-poweron-bbbbb"
                k.objs[(hh.K_BMC_JOB, "harvester-system", "n1-poweron-bbbbb")] = {"status": {"conditions": [{"type": "Running", "status": "True"}]}}
            if polls["n"] == 5:
                k.objs[(hh.K_BMC_JOB, "harvester-system", "n1-poweron-bbbbb")]["status"]["conditions"].append({"type": "Completed", "status": "True"})
        return real_get(kind, ns, name)
    k.get = get
    k.run = lambda *a, **kw: k.calls.append(("run",) + a) or ""
    assert hres.host_power(k, hargs(operation="poweron")) == hres.EXIT_OK
    assert ("patch", hh.K_INVENTORY, "n1", {"spec": {"powerActionRequested": "poweron"}}) in k.calls
    status = [c for c in k.calls if c[0] == "run" and "--subresource=status" in c]
    assert status and json.loads(status[0][-1]) == {"status": {"powerAction": {"lastJobName": ""}}}
    assert polls["n"] >= 5                                          # pas fini sur l'ancien travail


def test_a_node_name_is_a_dns_subdomain():
    """Vu sur harv1 : le nœud s'appelle `harv1.home.lo` ; l'outil le refusait."""
    assert hh.check_node("harv1.home.lo") == "harv1.home.lo"
    for bad in ("", "Harv1", "-x", "a_b", "a..b/c"):
        with pytest.raises(ValueError):
            hh.check_node(bad)
    k = Kube({("nodes", None, "harv1.home.lo"): a_node("harv1.home.lo"),
              (hh.K_LHNODE, "longhorn-system", "harv1.home.lo"): an_lh(disks={"default-disk-1": {}})})
    assert hres.host_disk_set(k, hargs(node="harv1.home.lo", disk="default-disk-1", tags="t1", scheduling="on")) == hres.EXIT_OK
    assert hres.main(["host", "tags", "--kubeconfig", "/nonexistent", "--node", "Bad_Name", "--tags", "x"]) == hres.EXIT_BLOCKED
