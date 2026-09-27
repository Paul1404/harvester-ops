"""v1.71.0 : les imports de VM de Harvester (vm-import-controller) : sources
écrites comme le contrôleur les lit (clé du certificat propre à chaque type,
namespace du secret toujours posé), imports refusés avant d'écrire là où le
contrôleur bouclerait ou échouerait sans message, état et progression lus
dans l'import et ses images."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin" / "lib"))
import hv_vmimport as vi  # noqa: E402

CA = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"


def test_sources_are_written_as_the_controller_reads_them():
    src, sec = vi.source_manifest({"type": "vmware", "namespace": "mig", "name": "vc", "endpoint": "https://vc.lan/sdk",
                                   "dc": "DC1", "credentials": {"mode": "new", "values": {"username": "u", "password": "p", "ca": CA}}})
    assert src == {"apiVersion": "migration.harvesterhci.io/v1beta1", "kind": "VmwareSource",
                   "metadata": {"name": "vc", "namespace": "mig"},
                   "spec": {"endpoint": "https://vc.lan/sdk", "dc": "DC1", "credentials": {"name": "vc-creds", "namespace": "mig"}}}
    assert sec == {"name": "vc-creds", "data": {"username": "u", "password": "p", "caCert": CA}}
    src, sec = vi.source_manifest({"type": "openstack", "name": "os", "endpoint": "https://os/identity", "region": "RegionOne",
                                   "retry_count": "5", "credentials": {"mode": "new", "values": {
                                       "username": "u", "password": "p", "project_name": "pr", "domain_name": "d", "ca": CA}}})
    assert src["spec"]["uploadImageRetryCount"] == 5 and sec["data"]["ca_cert"] == CA and "caCert" not in sec["data"]
    src, sec = vi.source_manifest({"type": "ova", "name": "o", "url": "http://10.0.0.1/a.ova", "http_timeout": 0})
    assert src["spec"] == {"url": "http://10.0.0.1/a.ova", "httpTimeoutSeconds": 0} and sec is None
    src, sec = vi.source_manifest({"type": "ova", "name": "o", "url": "https://x/a.ova", "credentials": {"mode": "new", "values": {"ca": CA}}})
    assert sec["data"] == {"ca.crt": CA}
    src, _ = vi.source_manifest({"type": "vmware", "name": "vc", "endpoint": "https://vc/sdk", "dc": "DC1",
                                 "credentials": {"mode": "existing", "secret": "vc-login"}})
    assert src["spec"]["credentials"] == {"name": "vc-login", "namespace": "default"}   # namespace toujours écrit


@pytest.mark.parametrize("spec, match", [
    ({"type": "vmware", "name": "vc", "endpoint": "vc.lan", "dc": "DC1"}, "endpoint"),
    ({"type": "vmware", "name": "vc", "endpoint": "https://vc/sdk"}, "datacenter"),
    ({"type": "vmware", "name": "vc", "endpoint": "https://vc/sdk", "dc": "D", "credentials": {"mode": "none"}}, "required for"),
    ({"type": "openstack", "name": "o", "endpoint": "https://o", "region": "R", "credentials": {"mode": "new", "values": {
        "username": "u", "password": "p"}}}, "project_name, domain_name required"),
    ({"type": "ova", "name": "o", "url": "ftp://x/a.ova"}, "http or https"),
    ({"type": "ova", "name": "o", "url": "http://x", "credentials": {"mode": "new", "values": {"username": "u"}}}, "go together"),
    ({"type": "ova", "name": "o", "url": "http://x", "credentials": {"mode": "new", "values": {"ca": "junk"}}}, "PEM"),
    ({"type": "ova", "name": "O", "url": "http://x"}, "source name"),
])
def test_what_the_controller_would_choke_on_is_refused(spec, match):
    with pytest.raises(ValueError, match=match):
        vi.source_manifest(spec)


def test_a_source_state_says_ready_not_ready_or_never_checked():
    rows = vi.source_rows({"ova": [{"metadata": {"namespace": "m", "name": "a"}, "spec": {"url": "http://x"},
                                    "status": {"status": "clusterReady"}},
                                   {"metadata": {"namespace": "m", "name": "b"}, "spec": {"url": "http://y"},
                                    "status": {"status": "clusterNotReady"}},
                                   {"metadata": {"namespace": "m", "name": "c"}, "spec": {"url": "http://z"}}]})
    assert [r["state"] for r in rows] == ["ready", "notready", "pending"]
    running = {"metadata": {"namespace": "m", "name": "i"}, "spec": {"sourceCluster": {"kind": "OvaSource", "name": "a", "namespace": "m"}},
               "status": {"importStatus": "diskImageSubmitted"}}
    over = {**running, "status": {"importStatus": "virtualMachineRunning"}}
    assert vi.source_users(rows[0], [running, over]) == ["m/i"]


SOURCES = [{"type": "ova", "namespace": "mig", "name": "ova1"}, {"type": "vmware", "namespace": "mig", "name": "vc"}]


def test_an_import_is_written_as_harvester_s_form_would_and_checked_first():
    o = vi.import_manifest({"namespace": "apps", "name": "imp", "vm_name": "Web-01", "source": {"type": "vmware", "namespace": "mig", "name": "vc"},
                            "networks": [{"source": "VM Network", "destination": "production", "model": "e1000e"}, {}],
                            "storage_class": "harv-rep1", "default_disk_bus": "scsi", "folder": "/Prod", "force_power_off": True,
                            "graceful_timeout": 120}, SOURCES, ["apps/production"], ["harv-rep1"])
    assert o["spec"] == {"virtualMachineName": "Web-01", "skipPreflightChecks": False,
                         "sourceCluster": {"apiVersion": "migration.harvesterhci.io/v1beta1", "kind": "VmwareSource", "name": "vc", "namespace": "mig"},
                         "storageClass": "harv-rep1",
                         "networkMapping": [{"sourceNetwork": "VM Network", "destinationNetwork": "apps/production", "networkInterfaceModel": "e1000e"}],
                         "defaultDiskBusType": "scsi", "folder": "/Prod", "forcePowerOff": True, "gracefulShutdownTimeoutSeconds": 120}
    assert vi.imported_name("vmware", "Web-01") == "web-01"      # le contrôleur met le nom en minuscules


@pytest.mark.parametrize("spec, match", [
    ({"name": "i", "vm_name": "a", "source": {"type": "ova", "namespace": "mig", "name": "nope"}}, "no OvaSource"),
    ({"name": "i", "vm_name": "web 01", "source": {"type": "vmware", "namespace": "mig", "name": "vc"}}, "not a valid VM name"),
    ({"name": "a-long-import-name-for-a-test", "vm_name": "a-long-vm-name-too", "source": {"type": "ova", "namespace": "mig", "name": "ova1"}},
     "63 characters"),
    ({"name": "i", "vm_name": "a", "source": {"type": "ova", "namespace": "mig", "name": "ova1"},
      "networks": [{"source": "n", "destination": "production"}, {"source": "n", "destination": "production"}]}, "appears twice"),
    ({"name": "i", "vm_name": "a", "source": {"type": "ova", "namespace": "mig", "name": "ova1"},
      "networks": [{"source": "n", "destination": "other/vlan9"}]}, "no VM network other/vlan9"),
    ({"name": "i", "vm_name": "a", "source": {"type": "ova", "namespace": "mig", "name": "ova1"}, "storage_class": "internal"}, "storage class"),
    ({"name": "i", "vm_name": "a", "source": {"type": "ova", "namespace": "mig", "name": "ova1"}, "force_power_off": True}, "for VMware"),
    ({"name": "i", "vm_name": "a", "source": {"type": "ova", "namespace": "mig", "name": "ova1"}, "default_nic_model": "vmxnet3"}, "virtio"),
])
def test_an_import_the_controller_would_loop_on_is_refused(spec, match):
    with pytest.raises(ValueError, match=match):
        vi.import_manifest(dict(spec, namespace="mig"), SOURCES, ["mig/production"], ["harv-rep1"])


def test_the_state_follows_the_controller_and_the_images_give_the_progress():
    imp = {"metadata": {"namespace": "m", "name": "i"},
           "spec": {"virtualMachineName": "a", "sourceCluster": {"kind": "OvaSource", "name": "o", "namespace": "m"}},
           "status": {"importStatus": "diskImageSubmitted",
                      "diskImportStatus": [{"diskName": "a-d1.img", "diskSize": 117440512, "VirtualMachineImage": "image-x", "busType": "virtio"}]}}
    img = {"metadata": {"namespace": "m", "name": "image-x"}, "status": {"progress": 42, "conditions": []}}
    v = vi.import_view(imp, [img])
    assert v["type"] == "ova" and v["step"] == 4 and v["disks"][0]["progress"] == 42 and not v["done"]
    assert vi.settled(imp, [img]) == (None, "diskImageSubmitted (42 %)")
    done = dict(imp, status={**imp["status"], "importStatus": "virtualMachineRunning", "importedVirtualMachineName": "a"})
    assert vi.settled(done, [img]) == (True, "VM a imported and running")
    failed = dict(imp, status={"importStatus": "VMMigrationFailed", "importConditions": [
        {"type": "VMExportFailed", "status": "True", "message": "error exporting VM: HEAD 404"}]})
    assert vi.settled(failed) == (False, "VMMigrationFailed: error exporting VM: HEAD 404")
    assert vi.settled(dict(imp, status={"importStatus": "virtualMachineImportInvalid"}), stuck_reason="")[1].endswith("see the controller log")


def test_the_controller_log_gives_the_reason_the_status_does_not():
    log = ('time="2026-09-27T12:00:01Z" level=info msg="reconciling" name=imp-x\n'
           'time="2026-09-27T12:00:02Z" level=error msg="error syncing" key=m/imp-x '
           'err="admission webhook denied the request: displayName is not a valid Kubernetes label value"\n'
           'time="2026-09-27T12:00:03Z" level=error msg="other" key=m/other err="boom"\n')
    lines = vi.log_lines(log, "imp-x")
    assert len(lines) == 1 and "displayName" in lines[0]
    assert vi.stuck_reason(lines) == "admission webhook denied the request: displayName is not a valid Kubernetes label value"


def test_the_reason_of_a_log_line_is_its_error_not_the_whole_line():
    """Vu en réel : la ligne entière (heure, niveau, champs) remplaçait la
    raison dans le message de la console."""
    ln = ('time="2026-09-27T12:06:30Z" level=error msg="Failed to verify source for migration" '
          'apiVersion=migration.harvesterhci.io/v1beta1 err="failed HEAD request (code=404)" kind=OvaSource name=ova-missing')
    assert vi.log_error(ln) == "failed HEAD request (code=404)"
    assert vi.log_error('level=error msg="boom \\"quoted\\""') == 'boom "quoted"'
