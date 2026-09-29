# vmwlab : banc VMware imbriqué, source des migrations vers Harvester

Un **ESXi 7.0** en VM KVM sur node2, un **vCenter 8.0.1** déployé dans cet
ESXi, et des VMs sources avec CBT (suivi des blocs modifiés). Sert à vérifier
en réel les migrations VMware vers Harvester par Forklift, à chaud
(conception : `docs/design/2026-09-27-migrations-vmware.md`).

| | Adresse | Nom |
|---|---|---|
| ESXi (VM KVM `vmwlab-esx1` sur node2) | 172.16.2.80 | `vmwlab-esx1.home.lo` |
| vCenter (VM `vmwlab-vc` dans l'ESXi) | 172.16.2.81 | `vmwlab-vc.home.lo` |
| VMs sources Linux `vmwlab-src-1`, `-2` (dans l'ESXi) | 172.16.2.82, .83 | |
| VM source Windows `vmwlab-src-3` (dans l'ESXi) | 172.16.2.84 | (.85 réservée) |

Enregistrés dans NetBox (`infra-dotfiles/netbox/seed.py`, `seed_vms.py`) et
Pi-hole (`dns.hosts`). Tout est en **mode évaluation** (60 jours) : aucune
clé de licence, jamais.

## Utilisation

Depuis node1, node2 allumé (par son iLO ; node1 + node2 = deux lames, le
maximum du châssis), dans l'ordre :

```bash
tests/bench/vmware/vmwlab.sh secrets     # mots de passe dans Vault
tests/bench/vmware/vmwlab.sh verify      # intégrité des médias
tests/bench/vmware/vmwlab.sh filter      # filtre réseau du banc sur node2
tests/bench/vmware/vmwlab.sh media       # ISO ESXi à installation automatique
tests/bench/vmware/vmwlab.sh install     # VM ESXi, installée sans intervention
tests/bench/vmware/vmwlab.sh vcenter     # vCenter déployé et configuré seul
tests/bench/vmware/vmwlab.sh inventory   # datacenter, hôte, VMs sources
tests/bench/vmware/vmwlab.sh registry    # registre d'images des essais Forklift
```

Durées mesurées : installation d'ESXi ~10 min, envoi de l'OVA ~6 min,
configuration du vCenter ~18 min, une VM source Linux ~3 min, la VM Windows
~20 min (envoi de l'ISO compris).

Puis `status`, `stop`, `start`, `destroy`. `stop` éteint proprement les
VMs sources par SSH (elles n'ont pas les VMware Tools), le vCenter par son
invité, puis l'ESXi (28 s mesurées). `start` démarre l'ESXi, qui relance
seul le vCenter et les VMs sources (démarrage automatique réglé sur l'hôte) :
ESXi en 1 min, VMs Linux en 8 à 9 min, Windows en 10 min, vCenter prêt peu
après ; les écritures continues reprennent leur compteur.
Tests de l'outil : `bash tests/bench/vmware/test_vmwlab.sh`.

## Médias

Les médias VMware sont ceux de l'exploitant (licence VMware, jamais dans le
dépôt), dans `~/ISO` par défaut (`VMWLAB_MEDIA`, ou `VMWLAB_ESXI_ISO`,
`VMWLAB_VCSA_OVA`, `VMWLAB_VDDK` fichier par fichier). L'OVA du vCenter se
tire du dossier `vcsa/` de l'ISO du VCSA. Ils sont **vérifiés avant tout
usage** (`verify`, appelé aussi par `media`) :

| Média | Contrôle |
|---|---|
| `VMware-VMvisor-Installer-7.0.0-15843807.x86_64.iso` | MD5 publié par VMware |
| `VMware-vix-disklib-8.0.3-23950268.x86_64.tar.gz` (VDDK) | MD5 publié par Broadcom, et SHA-256 |
| OVA du vCenter 8.0.1.00200 (build 21860503) | certificat de signature identique à celui d'une OVA officielle publiée, signature du manifeste valide, chaque fichier conforme au manifeste : une OVA tronquée ou modifiée est refusée |

Seule l'OVA sert du VCSA (ni son installeur, ni `ovftool`) : elle est
déployée par **govc** (outil libre de VMware, release GitHub, empreinte
vérifiée). Rien de ce qui vient des médias ne s'exécute sur node1 ou node2 :
ESXi et vCenter tournent dans leurs VMs.

Versions : vCenter 8 gère les ESXi 7.0 et 8.0. Seul défaut connu du couple
ESXi 7.0 GA et vCenter 8 : la vérification de conformité de vSphere
Lifecycle Manager échoue (notes de version de vCenter 8.0 U3k), sans effet sur
le banc, qui ne s'en sert pas.

## Inventaire vCenter

- Datacenter `vmwlab-dc`, l'ESXi ajouté par son IP (API REST du vCenter :
  le mot de passe de l'hôte reste dans le processus, govc l'exigerait en
  argument). Datastore `datastore1`, réseau `VM Network`.
- VMs sources : image cloud **Debian 12 officielle** (`debian-12-generic`,
  noyau complet avec pvscsi et vmxnet3 ; la variante `genericcloud` ne les a
  pas), empreinte vérifiée par `SHA512SUMS` au téléchargement, dans
  `~/ISO/vmware-lab/`. 1 vCPU, 1 Gio, disque de 10 Go sur **PVSCSI**, carte
  **vmxnet3**, **CBT actif** (`ctkEnabled`), données cloud-init sur un CD
  `cidata` : compte `debian` avec la clé de node1, IP fixe.
- Le service **`churn`** écrit en continu dans l'invité (4 Mio toutes les
  5 s, `/var/lib/churn`, compteur et heure du dernier passage) : de quoi
  nourrir les copies incrémentales, et dater la coupure après migration.
  `sudo systemctl stop churn` pour des incrémentales presque vides.
- Console série de chaque VM dans `[datastore1] <vm>/serial.log`
  (`govc datastore.download`) : l'image Debian écrit sur ttyS0.
- VM source **Windows** : Windows Server 2025 Standard Evaluation (Core),
  ISO d'évaluation de Microsoft (`VMWLAB_WIN_ISO`, par défaut
  `~/ISO/vmware-lab/windows_server_2025_eval_x64fre_en-us.iso` ; celle du
  banc vient de l'image du même nom sur harv1). Installée sans intervention
  par un `autounattend.xml` sur un second CD, avec un matériel dont Windows a
  les pilotes (LSI Logic SAS, e1000e ; PVSCSI et vmxnet3 exigent les VMware
  Tools), en BIOS (pas d'invite « press any key » sur un disque vierge),
  2 vCPU, 4 Gio, 40 Go, CBT actif. Premier démarrage : IP fixe, OpenSSH
  (livré avec Windows Server 2025) avec la clé de node1 pour
  `Administrator`, tâche planifiée `churn` (mêmes écritures qu'en Linux,
  `C:\churn`). Le mot de passe administrateur (Vault,
  `windows_admin_password`) n'est que sur ce second CD, retiré de la VM et du
  datastore dès l'installation finie.

## Registre du banc

`vmwlab.sh registry` monte sur node1 le registre d'images des essais
Forklift (image VDDK) : conteneur podman sans privilèges `vmwlab-registry`,
image openSUSE `registry` épinglée par empreinte, `http://172.16.1.11:5005`,
compte `harvops` (mot de passe Vault `secret/infra/vmware-lab`,
`registry_password`), données dans `~/.local/share/vmwlab-registry/`, pare-feu
ouvert aux seuls bancs harvlab (172.16.2.60 à .63) et harvlab2 (.70, .71).
Relancer la commande ne crée rien en double.

- **Pas Gitea** : son service de jetons s'annonce sous `gitea.home.zypp.fr`,
  nom retiré du LAN, que les bancs ne résolvent pas ; containerd ne pourrait
  pas s'y authentifier.
- `--user 0:0` : le compte propre du registre ne lit pas le fichier htpasswd
  en 0600 ; en podman sans privilèges, ce root n'est que l'utilisateur de
  node1.
- Côté Harvester, le réglage `containerd-registry` doit déclarer ce registre
  en HTTP avec le compte `harvops`.

## Filtre réseau

ESXi 7.0 GA et vCenter 8.0.1 ne sont pas corrigés (failles publiques
connues). L'unité `vmwlab-filter` de node2 (table nftables `bridge vmwlab`,
chargée au démarrage avant libvirt) ne laisse passer, pour toute interface
nommée `vmwlab-*`, que l'IPv4 et l'ARP échangés avec :

- node1 (172.16.1.11) et node2 (172.16.1.12) ;
- les bancs Harvester harvlab (172.16.2.60 à .63) et harvlab2 (.70, .71),
  destinations des migrations ;
- le banc lui-même (172.16.2.80 à .89).

Seule exception : le **DNS de Pi-hole** (172.16.3.6, port 53), car le vCenter
résout son nom et son adresse pendant son installation. Ni Internet, ni NTP :
le vCenter prend l'heure de l'ESXi. `install` et `start` refusent de démarrer
la VM sans le filtre.

## Pièges payés

- **ESXi 7.0 ne lit pas un lecteur CD branché sur le contrôleur SATA de
  QEMU** (exceptions `vmw_ahci` sur le port ATAPI ; le disque SATA, lui, est
  vu) : « cannot find kickstart file on cd-rom ». D'où une carte mère
  **i440fx** (`--machine pc`) avec le lecteur en **IDE** et le disque en SATA.
- Deux impasses essayées : un module de démarrage `ks.tgz` n'est **pas**
  décompressé dans le disque mémoire de l'installeur (fichier introuvable) ;
  et `ks=file:///etc/vmware/weasel/ks.cfg` lit le kickstart **par défaut**
  d'ESXi (DHCP, mot de passe root connu de tous). Vécu : un ESXi installé en
  DHCP, détruit et réinstallé.
- `reboot --noeject` : sans lui, l'installeur voulait éjecter le CD et
  attendait une touche au lieu de redémarrer.
- **Le vCenter exige un DNS, même nommé par son IP** : l'installation
  résout l'adresse du nœud (`VmDirGetCanonicalHostName`) ; sans serveur
  joignable, chaque requête expire (~80 s) et la création de l'annuaire SSO
  échoue (« vdcpromo failed, Can't contact LDAP server », puis « Firstboot
  Error » sur la console). D'où l'exception DNS du filtre et le nom
  `vmwlab-vc.home.lo`, enregistré dans Pi-hole dans les deux sens. Journaux
  utiles : `/var/log/firstboot/firstbootStatus.json`,
  `/var/log/vmware/vmdir/vmafdvmdirclient.log`.
- Le **premier démarrage** d'une VM source s'est bloqué une fois avant le
  montage de sa racine (écran noir, aucune trace), puis a démarré au
  redémarrage ; la VM n'avait pas encore de port série. Depuis, chaque VM en
  a un dès sa création, et `inventory` la redémarre une fois si elle reste
  muette 6 min.
- L'**API REST** du vCenter répond 503 plusieurs minutes après son API SOAP :
  `vcenter` attend les deux.
- **Un lecteur CD IDE ne se retire ni ne s'éjecte VM allumée** sur cet ESXi
  imbriqué (« Connection control operation failed for disk ide0:1 ») ; pire,
  l'éjection refusée détache quand même l'ISO du lecteur. Le CD du fichier
  de réponses Windows est donc retiré VM éteinte (arrêt de Windows par SSH),
  et la reprise regarde aussi le fichier resté au datastore.
- `ssh ... true` échoue sur Windows (PowerShell n'a pas de `true`) : les
  sondes SSH envoient `exit 0`, valable partout. Et `ssh -n` dans toute
  boucle `while read`.
- `govc ... | grep -q` sous `set -o pipefail` : `grep -q` sort au premier
  résultat, govc reçoit SIGPIPE et le tube rend 141, donc « faux ». Le script
  lit toute la sortie (`grep ... >/dev/null`).
- **`ignore_msrs`** sur node2 (`/etc/modprobe.d/vmwlab-kvm.conf`, posé aussi
  à chaud) : ESXi lit des MSR que KVM n'émule pas.
- Carte **e1000e** pour l'ESXi : ESXi 7 ne reconnaît plus e1000 ni rtl8139, et la vmxnet3 de QEMU ne
  complète pas les trames courtes. Le vmkernel ignore alors les trames de moins de 60 octets venues du
  pont local : l'ARP de node2 et des bancs Harvester restait sans réponse (node1, derrière le switch
  physique qui complète les trames, passait), et VDDK ne joignait pas l'hôte sur le port 902. Vidéo
  **qxl** (l'écran d'installation boucle sinon), CPU **host-passthrough**
  (l'ESXi fait tourner le vCenter).
- **SELinux est appliqué sur node2** : `/srv/vmware-lab` porte le type
  `virt_image_t` (celui des images libvirt) et le mode 0711 pour que QEMU
  le traverse.
- Le RAID des images libvirt de node2 est plein à 93 % : le banc vit sur la
  racine btrfs (`/srv/vmware-lab`, sans copie à l'écriture).
- **Secrets** : Vault `secret/infra/vmware-lab` (`esxi_root_password`,
  `vcsa_root_password`, `sso_user`, `sso_password`). govc les lit dans son
  environnement, les options du vCenter passent par un tube ; le kickstart ne
  porte que le condensat SHA-512 du mot de passe root.
