"""Chaque fichier JavaScript de la console se lit sans erreur de syntaxe.

v1.61.0 : une modification de backups.js avait perdu la fermeture `})();`
du module ; seuls les tests du navigateur l'avaient vue (une page blanche au
démarrage). `node --check` la voit en une seconde, avant le commit."""

import shutil
import subprocess
from pathlib import Path

import pytest

JS = sorted((Path(__file__).resolve().parent.parent.parent / "web" / "static" / "js").glob("*.js"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("path", JS, ids=lambda p: p.name)
def test_the_file_parses(path):
    r = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr.strip()[-600:]
