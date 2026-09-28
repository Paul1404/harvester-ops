"""v1.75.0 : le relais de port de kube.Kube ne doit ni dépasser son délai ni
laisser de tubes ouverts derrière lui.

L'ancien code appelait `readline()` sur le tube texte tamponné du process
kubectl : une ligne coupée en deux (« 127.0.0.1: » livré sans le numéro ni
le saut de ligne) bloquait l'appel bien après le délai demandé. Et
`p.stdout`/`p.stderr` n'étaient jamais fermés, contrairement à ce que fait
`raw_stream()` pour le même genre de sous-processus : deux descripteurs de
fichier perdus par appel.

Ces tests posent un faux `kubectl` exécutable, seul sur PATH, pour observer
`port_forward` sans toucher à un vrai cluster.
"""

import os
import stat
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin" / "lib"))
import kube  # noqa: E402


def fake_kubectl(tmp_path, script):
    """Un faux `kubectl` exécutable, seul sur PATH dans `tmp_path`."""
    path = tmp_path / "kubectl"
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def put_on_path(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))


def test_port_forward_yields_the_port_then_kills_and_closes_the_child(tmp_path, monkeypatch):
    pid_file = tmp_path / "kubectl.pid"
    fake_kubectl(tmp_path, f"""#!/usr/bin/env python3
import os, sys, time
with open({str(pid_file)!r}, "w") as f:
    f.write(str(os.getpid()))
sys.stdout.write("Forwarding from 127.0.0.1:43210 -> 8443\\n")
sys.stdout.flush()
time.sleep(30)
""")
    put_on_path(monkeypatch, tmp_path)

    created = []
    real_popen = kube.subprocess.Popen

    def spy(*a, **kw):
        proc = real_popen(*a, **kw)
        created.append(proc)
        return proc

    monkeypatch.setattr(kube.subprocess, "Popen", spy)

    k = kube.Kube("/nonexistent-kubeconfig")
    with k.port_forward("forklift", "svc/x", 8443, timeout=5) as port:
        assert port == 43210

    # les deux tubes du sous-processus sont refermés à la sortie
    assert created[0].stdout.closed
    assert created[0].stderr.closed

    # et le sous-processus lui-même est bien mort, pas juste détaché
    pid = int(pid_file.read_text())
    deadline = time.time() + 3
    gone = False
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            gone = True
            break
        time.sleep(0.1)
    assert gone, "le faux kubectl est encore vivant après la sortie de port_forward"


def test_port_forward_wait_is_bounded_by_the_deadline_on_a_split_line(tmp_path, monkeypatch):
    fake_kubectl(tmp_path, """#!/usr/bin/env python3
import sys, time
sys.stdout.write("Forwarding from 127.0.0.1:")
sys.stdout.flush()
time.sleep(30)
""")
    put_on_path(monkeypatch, tmp_path)

    k = kube.Kube("/nonexistent-kubeconfig")
    timeout = 2
    start = time.monotonic()
    with pytest.raises(kube.KubeError):
        with k.port_forward("forklift", "svc/x", 8443, timeout=timeout):
            pass
    elapsed = time.monotonic() - start
    assert elapsed < timeout + 3, f"port_forward a dépassé son délai : {elapsed:.1f} s"


def test_port_forward_names_the_kubectl_error_without_the_kubeconfig_path(tmp_path, monkeypatch):
    fake_kubectl(tmp_path, """#!/usr/bin/env python3
import sys
sys.stderr.write('error: services "x" not found\\n')
sys.exit(1)
""")
    put_on_path(monkeypatch, tmp_path)

    secret_kubeconfig = "/home/ju/very/secret/kubeconfig-path"
    k = kube.Kube(secret_kubeconfig)
    with pytest.raises(kube.KubeError) as exc:
        with k.port_forward("forklift", "svc/x", 8443, timeout=5):
            pass
    message = str(exc.value)
    assert "not found" in message
    assert secret_kubeconfig not in message
