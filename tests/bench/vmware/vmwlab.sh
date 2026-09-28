#!/usr/bin/env bash
# vmwlab : banc VMware imbriqué sur node2, source des migrations VMware vers
# Harvester (Forklift, à chaud). Un ESXi 7.0 en VM KVM, un vCenter 8.0.1 dans
# cet ESXi, des VMs sources avec CBT. Voir README.md à côté.
#
# Lancé depuis node1. Secrets dans Vault `secret/infra/vmware-lab`, jamais
# affichés ni passés en argument de commande (govc les lit dans son
# environnement). Rien de ce qui vient des médias VMware ne s'exécute sur
# node1 ou node2 : l'ISO ESXi n'est que remaniée (xorriso), l'OVA du vCenter
# est déployée dans l'ESXi par govc (outil libre de VMware, release GitHub),
# pas par l'installeur du VCSA.
#
#   vmwlab.sh secrets     crée les secrets dans Vault s'ils manquent
#   vmwlab.sh verify      contrôle l'intégrité des médias (empreintes publiées,
#                         signature VMware de l'OVA)
#   vmwlab.sh filter      pose sur node2 le filtre réseau du banc (nftables,
#                         unité systemd) : il ne parle qu'à node1, node2 et
#                         aux bancs Harvester, jamais à Internet
#   vmwlab.sh media       ISO ESXi à installation automatique (kickstart) et
#                         OVA du vCenter, copiées sur node2
#   vmwlab.sh install     crée la VM ESXi et l'installe sans intervention
#   vmwlab.sh vcenter     déploie le vCenter dans l'ESXi et attend qu'il réponde
#   vmwlab.sh inventory   datacenter, cluster, hôte ajouté, VMs sources (CBT)
#   vmwlab.sh status      état de la VM, de l'ESXi, du vCenter, du filtre
#   vmwlab.sh stop|start  arrêt propre (vCenter puis ESXi) ou démarrage
#   vmwlab.sh destroy     supprime la VM ESXi et ses disques (confirmation)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

LAB=vmwlab
NODE2="${VMWLAB_HOST:-ju@172.16.1.12}"
NODE2_IP="${NODE2#*@}"
# Racine btrfs de node2 : le RAID des images libvirt est plein à 93 %.
DIR=/srv/vmware-lab
MEDIA="${VMWLAB_MEDIA:-$HOME/ISO}"
# Médias VMware de l'exploitant (licence VMware, jamais dans le dépôt) : l'ISO
# d'ESXi, l'OVA du vCenter (tirée de l'ISO du VCSA, dossier vcsa/) et le VDDK.
ESXI_ISO="${VMWLAB_ESXI_ISO:-$MEDIA/VMware-VMvisor-Installer-7.0.0-15843807.x86_64.iso}"
OVA="${VMWLAB_VCSA_OVA:-$MEDIA/vmware-lab/vcsa-8.0.1.ova}"
VDDK="${VMWLAB_VDDK:-$MEDIA/VMware-vix-disklib-8.0.3-23950268.x86_64.tar.gz}"
ESX=vmwlab-esx1
ESX_IP=172.16.2.80
VC=vmwlab-vc
VC_IP=172.16.2.81
MAC=52:54:00:4c:ae:80
# 28 Gio : le vCenter « tiny » en prend 14, l'ESXi ~2, les VMs sources le reste.
MEMORY_MB="${VMWLAB_MEMORY_MB:-28672}"
VCPUS="${VMWLAB_VCPUS:-8}"
DISK_GB="${VMWLAB_DISK_GB:-600}"
VAULT_PATH=secret/infra/vmware-lab
# Seuls interlocuteurs du banc (versions non corrigées, failles connues) :
# node1, node2, harvlab, harvlab2 et le banc lui-même.
PEERS="172.16.1.11, 172.16.1.12, 172.16.2.60-172.16.2.63, 172.16.2.70-172.16.2.71, 172.16.2.80-172.16.2.89"
# Seul service extérieur permis : le DNS de Pi-hole. Même nommé par son IP,
# le vCenter résout son adresse à l'installation (VmDirGetCanonicalHostName) :
# sans DNS joignable, chaque requête expirait (~80 s) et la création de
# l'annuaire SSO échouait (« vdcpromo : Can't contact LDAP server », vécu le
# 28/09/2026).
DNS=172.16.3.6

on_node2() { ssh -o BatchMode=yes -o LogLevel=ERROR "$NODE2" "$@"; }
say()      { printf '[%s] %s\n' "$LAB" "$*"; }

vault_env() {
    export VAULT_ADDR=http://127.0.0.1:8200
    VAULT_TOKEN="$(sudo cat /data/vault/root-token)"
    export VAULT_TOKEN
}
vault_field() { vault kv get -field="$1" "$VAULT_PATH"; }

# govc lit URL et identifiants dans son environnement, jamais dans argv.
govc_esx() {
    vault_env
    GOVC_URL="https://$ESX_IP" GOVC_USERNAME=root GOVC_PASSWORD="$(vault_field esxi_root_password)" \
    GOVC_INSECURE=1 GOVC_PERSIST_SESSION=false govc "$@"
}
govc_vc() {
    vault_env
    GOVC_URL="https://$VC_IP" GOVC_USERNAME="$(vault_field sso_user)" \
    GOVC_PASSWORD="$(vault_field sso_password)" GOVC_INSECURE=1 GOVC_PERSIST_SESSION=false govc "$@"
}

cmd_secrets() {
    vault_env
    if vault kv get -field=esxi_root_password "$VAULT_PATH" >/dev/null 2>&1; then
        say "secrets déjà présents dans Vault ($VAULT_PATH)"
        return
    fi
    # Quatre classes de caractères (exigence d'ESXi et du vCenter) ; des
    # spéciaux sûrs en XML : l'environnement OVF du vCenter est du XML.
    python3 - <<'PY' | vault kv put "$VAULT_PATH" - >/dev/null
import json, secrets, string
def pw(n=16):
    sp = "!@#%-_+="
    while True:
        p = "".join(secrets.choice(string.ascii_letters + string.digits + sp) for _ in range(n))
        if (any(c.islower() for c in p) and any(c.isupper() for c in p) and any(c.isdigit() for c in p)
                and any(c in sp for c in p) and p[0].isalnum()):
            return p
print(json.dumps({"esxi_root_password": pw(), "vcsa_root_password": pw(), "sso_password": pw(),
                  "sso_user": "administrator@vsphere.local"}))
PY
    say "secrets créés dans Vault ($VAULT_PATH)"
}

# Les médias sont vérifiés avant tout usage. Empreintes publiées par
# VMware/Broadcom (relevées le 27/09/2026) :
#   ISO ESXi 7.0 GA : MD5 de la page de téléchargement VMware ;
#   VDDK 8.0.3 : MD5 archivé de Broadcom (et SHA-256 du même fichier).
# L'OVA du vCenter se vérifie par sa propre signature : certificat VMware
# identique au certificat publié d'une OVA officielle, et chaque fichier
# conforme au manifeste signé (une OVA tronquée ou modifiée est refusée).
ESXI_MD5=220d2e87290f50c3508214cadf66b737
VDDK_MD5=007ab979e52f52401f02278b75ab5c74
VDDK_SHA256=aadcf8fcfa5fc0ca972283334a273aa49fff96fa2eaadb30b8cc0c71705c4754
VMWARE_OVF_CERT_SHA256="${VMWLAB_OVF_CERT_SHA256:-90:ED:A1:08:19:1B:A0:01:C2:91:59:6A:5A:D5:0F:1E:BA:42:C3:0D:BC:72:C9:B8:1C:BB:44:95:EB:F1:AB:27}"
verify_ova() {
    python3 - "${1:-$OVA}" "$VMWARE_OVF_CERT_SHA256" <<'PY'
import hashlib, re, subprocess, sys, tarfile, tempfile, os
ova, want_fp = sys.argv[1], sys.argv[2]
t = tarfile.open(ova)
members = t.getmembers()
mf_m = next(m for m in members if m.name.endswith(".mf"))
cert_m = next(m for m in members if m.name.endswith(".cert"))
mf = t.extractfile(mf_m).read()
cert = t.extractfile(cert_m).read().decode(errors="replace")
work = tempfile.mkdtemp()
sig_hex = re.search(r"SHA256\([^)]*\)= ([0-9a-f]+)", cert).group(1)
pem = re.search(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", cert, re.S).group(0)
open(f"{work}/signer.pem", "w").write(pem)
open(f"{work}/sig.bin", "wb").write(bytes.fromhex(sig_hex))
open(f"{work}/mf", "wb").write(mf)
fp = subprocess.run(["openssl", "x509", "-in", f"{work}/signer.pem", "-noout", "-fingerprint", "-sha256"],
                    capture_output=True, text=True).stdout.strip().split("=", 1)[-1]
subprocess.run(f"openssl x509 -in {work}/signer.pem -pubkey -noout > {work}/pub.pem", shell=True, check=True)
sig_ok = subprocess.run(["openssl", "dgst", "-sha256", "-verify", f"{work}/pub.pem", "-signature",
                         f"{work}/sig.bin", f"{work}/mf"], capture_output=True).returncode == 0
want = dict(re.findall(r"SHA256\((.+)\)= ([0-9a-f]+)", mf.decode()))
bad = []
for m in members:
    if m in (mf_m, cert_m):
        continue
    h = hashlib.sha256()
    f = t.extractfile(m)
    for chunk in iter(lambda: f.read(8 << 20), b""):
        h.update(chunk)
    if want.get(m.name) != h.hexdigest():
        bad.append(m.name)
extra = [m.name for m in members if m not in (mf_m, cert_m) and m.name not in want]
missing = set(want) - {m.name for m in members}
ok = fp == want_fp and sig_ok and not bad and not extra and not missing
print(f"  certificat VMware {'attendu' if fp == want_fp else 'INCONNU'}, signature du manifeste "
      f"{'valide' if sig_ok else 'INVALIDE'}, {len(want) - len(bad)}/{len(want)} fichiers conformes")
sys.exit(0 if ok else 1)
PY
}

cmd_verify() {
    local ok=0
    [[ "$(md5sum < "$ESXI_ISO" | cut -d' ' -f1)" == "$ESXI_MD5" ]] \
        && say "ISO ESXi 7.0 : MD5 conforme à VMware" || { say "ISO ESXi : MD5 NON CONFORME"; ok=1; }
    [[ "$(md5sum < "$VDDK" | cut -d' ' -f1)" == "$VDDK_MD5" && "$(sha256sum < "$VDDK" | cut -d' ' -f1)" == "$VDDK_SHA256" ]] \
        && say "VDDK 8.0.3 : MD5 conforme à Broadcom" || { say "VDDK : empreinte NON CONFORME"; ok=1; }
    [[ -s "$OVA" ]] || { say "OVA du vCenter absente ($OVA)"; return 1; }
    if verify_ova; then say "OVA vCenter 8.0.1 : signée par VMware, intacte"; else say "OVA vCenter : NON CONFORME"; ok=1; fi
    return $ok
}

cmd_filter() {
    on_node2 "sudo install -d -m 0755 /etc/nftables && sudo tee /etc/nftables/$LAB.nft >/dev/null" <<EOF
# Filtre du banc VMware (harvester-ops, tests/bench/vmware) : les VMs dont
# l'interface s'appelle $LAB-* ne parlent qu'aux pairs listés, dans les deux
# sens (IPv4 et ARP seulement), plus le DNS de Pi-hole ($DNS, port 53) :
# le vCenter résout son propre nom et son adresse à l'installation. Ni
# Internet, ni NTP.
table bridge $LAB
delete table bridge $LAB
table bridge $LAB {
    set peers {
        type ipv4_addr
        flags interval
        elements = { $PEERS }
    }
    chain forward {
        type filter hook forward priority 0; policy accept;
        iifname "$LAB-*" ether type arp accept
        oifname "$LAB-*" ether type arp accept
        iifname "$LAB-*" ip daddr @peers accept
        oifname "$LAB-*" ip saddr @peers accept
        iifname "$LAB-*" ip daddr $DNS udp dport 53 accept
        iifname "$LAB-*" ip daddr $DNS tcp dport 53 accept
        oifname "$LAB-*" ip saddr $DNS udp sport 53 accept
        oifname "$LAB-*" ip saddr $DNS tcp sport 53 accept
        iifname "$LAB-*" counter drop
        oifname "$LAB-*" counter drop
    }
    chain input {
        type filter hook input priority 0; policy accept;
        iifname "$LAB-*" ether type arp accept
        iifname "$LAB-*" ip daddr @peers accept
        iifname "$LAB-*" counter drop
    }
    chain output {
        type filter hook output priority 0; policy accept;
        oifname "$LAB-*" ether type arp accept
        oifname "$LAB-*" ip saddr @peers accept
        oifname "$LAB-*" counter drop
    }
}
EOF
    on_node2 "sudo tee /etc/systemd/system/$LAB-filter.service >/dev/null" <<EOF
[Unit]
Description=Filtre réseau du banc VMware (harvester-ops)
Before=libvirtd.service virtqemud.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/nftables/$LAB.nft
ExecStop=/usr/sbin/nft delete table bridge $LAB

[Install]
WantedBy=multi-user.target
EOF
    on_node2 "sudo systemctl daemon-reload && sudo systemctl enable --now $LAB-filter.service >/dev/null 2>&1 \
        && sudo systemctl restart $LAB-filter.service && sudo nft list table bridge $LAB >/dev/null"
    say "filtre actif sur node2 (unité $LAB-filter, table bridge $LAB)"
}

filter_active() { on_node2 "sudo nft list table bridge $LAB >/dev/null 2>&1"; }

# Kickstart : le mot de passe root n'y figure que haché (SHA-512).
render_ks() {
    local hash key
    vault_env
    hash="$(vault_field esxi_root_password | openssl passwd -6 -stdin)"
    key="$(cat "$HOME/.ssh/id_ed25519.pub")"
    cat <<EOF
vmaccepteula
install --firstdisk --overwritevmfs
rootpw --iscrypted $hash
network --bootproto=static --device=vmnic0 --ip=$ESX_IP --netmask=255.255.0.0 --gateway=172.16.0.1 --hostname=$ESX.home.lo --nameserver=172.16.3.6 --addvmportgroup=1
reboot --noeject

%firstboot --interpreter=busybox
vim-cmd hostsvc/enable_ssh
vim-cmd hostsvc/start_ssh
esxcli system settings advanced set -o /UserVars/SuppressShellWarning -i 1
esxcli system settings advanced set -o /UserVars/HostClientCEIPOptIn -i 2
echo "$key" > /etc/ssh/keys-root/authorized_keys
EOF
}

cmd_media() {
    cmd_verify
    local tmp; tmp="$(mktemp -d)"
    chmod 700 "$tmp"
    xorriso -osirrox on -indev "$ESXI_ISO" -extract /BOOT.CFG "$tmp/boot.cfg" \
        -extract /EFI/BOOT/BOOT.CFG "$tmp/efi.cfg" >/dev/null 2>&1
    chmod 600 "$tmp"/*.cfg
    # Kickstart sur le CD, lu par l'installeur : le lecteur est branché en
    # IDE (voir « install »), ESXi 7.0 ne lisant pas un lecteur SATA de QEMU.
    render_ks > "$tmp/ks.cfg"
    sed -i 's#^kernelopt=.*#kernelopt=cdromBoot runweasel ks=cdrom:/KS.CFG#' "$tmp/boot.cfg" "$tmp/efi.cfg"
    # Les enregistrements El Torito d'origine (BIOS et UEFI) sont rejoués.
    xorriso -indev "$ESXI_ISO" -outdev "$tmp/esxi-ks.iso" -boot_image any replay \
        -map "$tmp/ks.cfg" /KS.CFG -map "$tmp/boot.cfg" /BOOT.CFG -map "$tmp/efi.cfg" /EFI/BOOT/BOOT.CFG \
        >/dev/null 2>&1
    # 0711 : QEMU (utilisateur qemu) doit traverser le répertoire. SELinux est
    # appliqué sur node2 : le type virt_image_t, celui des images libvirt,
    # est hérité par ce qui s'y crée. +C : pas de copie à l'écriture btrfs
    # sous des qcow2.
    on_node2 "sudo install -d -m 0711 $DIR && (sudo chattr +C $DIR 2>/dev/null || true) \
        && sudo chcon -t virt_image_t $DIR"
    rsync -a --chmod=F600 -e "ssh -o LogLevel=ERROR" --rsync-path="sudo rsync" "$tmp/esxi-ks.iso" "$NODE2:$DIR/"
    rm -rf "$tmp"
    say "ISO ESXi à installation automatique posée sur node2 ($DIR/esxi-ks.iso)"
}

cmd_install() {
    filter_active || { say "filtre absent : lancer « filter » d'abord"; exit 1; }
    on_node2 "sudo test -s $DIR/esxi-ks.iso" || { say "ISO absente : lancer « media » d'abord"; exit 1; }
    # ignore_msrs : ESXi lit des MSR que KVM n'émule pas (sans lui, il s'arrête
    # au démarrage). Posé à chaud et pour les démarrages suivants.
    on_node2 "echo 'options kvm ignore_msrs=1 report_ignored_msrs=0' | sudo tee /etc/modprobe.d/$LAB-kvm.conf >/dev/null \
        && echo 1 | sudo tee /sys/module/kvm/parameters/ignore_msrs >/dev/null \
        && echo 0 | sudo tee /sys/module/kvm/parameters/report_ignored_msrs >/dev/null"
    say "installation de $ESX ($ESX_IP), ~15 min"
    # host-passthrough : l'ESXi fait lui-même tourner des VMs (le vCenter).
    # vmxnet3 : ESXi 7 ne reconnaît plus les e1000/rtl8139 émulées.
    # qxl : avec la carte vidéo par défaut, l'écran d'installation boucle.
    # Carte mère i440fx (pc) et lecteur CD en IDE : ESXi 7.0 ne lit pas le
    # lecteur ATAPI d'un contrôleur SATA de QEMU (exceptions vmw_ahci, le
    # kickstart introuvable), alors que le disque SATA, lui, est vu : il
    # reste en SATA (AHCI, plus rapide que l'IDE).
    # Interface nommée $LAB-esx1 : c'est ce nom que le filtre reconnaît.
    # cache=unsafe et discard : banc jetable, qcow2 creux (comme harvlab).
    on_node2 "sudo systemd-run --unit=$LAB-install --collect \
        virt-install --name $ESX --memory $MEMORY_MB --vcpus $VCPUS \
        --cpu host-passthrough --machine pc --osinfo detect=on,require=off \
        --boot uefi,firmware.feature0.name=secure-boot,firmware.feature0.enabled=no \
        --disk path=$DIR/$ESX.qcow2,size=$DISK_GB,format=qcow2,bus=sata,cache=unsafe,discard=unmap \
        --check disk_size=off \
        --cdrom $DIR/esxi-ks.iso \
        --network bridge=br0,model=vmxnet3,mac=$MAC,target.dev=$LAB-esx1 \
        --video qxl --graphics vnc,listen=127.0.0.1 \
        --noautoconsole --wait -1 >/dev/null"
}

# Options de déploiement de l'OVA : configuration « tiny », disques creux,
# et l'environnement OVF injecté dans la VM (un ESXi seul ne le transmet pas
# à l'invité, c'est le rôle d'un vCenter) avec autoconfig=True : le vCenter
# se configure seul au premier démarrage (« stage 2 » de l'installeur).
# Nom système = son nom DNS (Pi-hole le résout dans les deux sens).
vcenter_options() {
    vault_env
    VC_IP="$VC_IP" VC_FQDN="$VC.home.lo" python3 - "$VAULT_PATH" <<'PY'
import json, os, subprocess, sys
vault_path = sys.argv[1]
def vault(field):
    return subprocess.run(["vault", "kv", "get", f"-field={field}", vault_path],
                          capture_output=True, text=True, check=True).stdout
ip = os.environ["VC_IP"]
props = {
    "guestinfo.cis.deployment.node.type": "embedded",
    "guestinfo.cis.deployment.autoconfig": "True",
    "guestinfo.cis.appliance.net.addr.family": "ipv4",
    "guestinfo.cis.appliance.net.mode": "static",
    "guestinfo.cis.appliance.net.addr": ip,
    "guestinfo.cis.appliance.net.prefix": "16",
    "guestinfo.cis.appliance.net.gateway": "172.16.0.1",
    "guestinfo.cis.appliance.net.dns.servers": "172.16.3.6",
    "guestinfo.cis.appliance.net.pnid": os.environ["VC_FQDN"],
    "guestinfo.cis.appliance.root.passwd": vault("vcsa_root_password"),
    "guestinfo.cis.appliance.ssh.enabled": "True",
    # bash plutôt que l'appliancesh : les journaux se lisent par SSH.
    "guestinfo.cis.appliance.root.shell": "/bin/bash",
    "guestinfo.cis.appliance.time.tools-sync": "True",
    "guestinfo.cis.vmdir.domain-name": "vsphere.local",
    "guestinfo.cis.vmdir.username": vault("sso_user"),
    "guestinfo.cis.vmdir.password": vault("sso_password"),
    "guestinfo.cis.vmdir.first-instance": "True",
    "guestinfo.cis.ceip_enabled": "False",
}
print(json.dumps({
    "Deployment": "tiny", "DiskProvisioning": "thin", "IPAllocationPolicy": "fixedPolicy",
    "IPProtocol": "IPv4", "PropertyMapping": [{"Key": k, "Value": v} for k, v in props.items()],
    "NetworkMapping": [{"Name": "Network 1", "Network": "VM Network"}],
    "MarkAsTemplate": False, "PowerOn": True, "InjectOvfEnv": True, "WaitForIP": False,
    "Name": "vmwlab-vc",
}))
PY
}

# Démarrage automatique des VMs avec l'ESXi (réglé sur l'hôte, par son API
# propre : il vaut que le vCenter tourne ou non). Le vCenter d'abord.
autostart() {  # VM...
    govc_esx host.autostart.configure -enabled=true >/dev/null
    govc_esx host.autostart.add "$@" >/dev/null
}

cmd_vcenter() {
    filter_active || { say "filtre absent : lancer « filter » d'abord"; exit 1; }
    govc_esx about >/dev/null 2>&1 || { say "ESXi injoignable : lancer « install » d'abord"; exit 1; }
    if govc_esx vm.info "$VC" 2>/dev/null | grep "^Name:" >/dev/null; then
        say "$VC déjà déployé"
    else
        say "déploiement de l'OVA du vCenter dans l'ESXi (8 Go à envoyer)"
        # Les options (mots de passe compris) passent par un tube, jamais
        # par un fichier ni par argv. -hidden : autoconfig n'est pas une
        # propriété modifiable par l'utilisateur dans l'OVF.
        vcenter_options | govc_esx import.ova -hidden -options - -name "$VC" "$OVA"
    fi
    say "configuration automatique du vCenter en cours (30 à 60 min en imbriqué)"
    local i
    for i in $(seq 1 180); do
        # L'API SOAP (govc) répond avant l'API REST : on attend les deux.
        if govc_vc about >/dev/null 2>&1 && vc_rest ping >/dev/null 2>&1; then
            autostart "$VC"
            say "vCenter prêt : $(govc_vc about | sed -n 's/^FullName: *//p')"
            return
        fi
        sleep 30
    done
    say "le vCenter ne répond pas après 90 min"; exit 1
}

# Appels à l'API REST du vCenter (session, datacenter, ajout de l'hôte) :
# l'ajout d'un hôte exige son mot de passe, que govc ne sait lire que dans
# argv. Ici il reste dans le processus Python.
vc_rest() {
    vault_env
    VC_IP="$VC_IP" ESX_IP="$ESX_IP" DC="$DC" python3 - "$VAULT_PATH" "$@" <<'PY'
import base64, json, os, ssl, subprocess, sys, time, urllib.error, urllib.request
vault_path, action = sys.argv[1], sys.argv[2]
def vault(field):
    return subprocess.run(["vault", "kv", "get", f"-field={field}", vault_path],
                          capture_output=True, text=True, check=True).stdout
ctx = ssl._create_unverified_context()
base = f"https://{os.environ['VC_IP']}"
def call(method, path, body=None, auth=None):
    # 503 : les services du vCenter démarrent encore (l'API SOAP répond
    # bien avant l'API REST) ; on attend jusqu'à 15 min.
    for attempt in range(90):
        req = urllib.request.Request(base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", **(auth or {})})
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=120) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            if e.code == 503 and attempt < 89:
                time.sleep(10)
                continue
            sys.exit(f"{method} {path} : HTTP {e.code} {e.read()[:300].decode(errors='replace')}")
basic = base64.b64encode(f"{vault('sso_user')}:{vault('sso_password')}".encode()).decode()
token = call("POST", "/api/session", auth={"Authorization": "Basic " + basic})
s = {"vmware-api-session-id": token}
if action == "ping":
    call("DELETE", "/api/session", auth=s)
    sys.exit(0)
dc_name = os.environ["DC"]
dcs = call("GET", f"/api/vcenter/datacenter?names={dc_name}", auth=s)
if not dcs:
    root = call("GET", "/api/vcenter/folder?type=DATACENTER", auth=s)[0]["folder"]
    call("POST", "/api/vcenter/datacenter", {"name": dc_name, "folder": root}, auth=s)
    dcs = call("GET", f"/api/vcenter/datacenter?names={dc_name}", auth=s)
    print(f"datacenter {dc_name} créé")
dc = dcs[0]["datacenter"]
esx = os.environ["ESX_IP"]
if not call("GET", f"/api/vcenter/host?names={esx}", auth=s):
    folder = call("GET", f"/api/vcenter/folder?type=HOST&datacenters={dc}", auth=s)[0]["folder"]
    call("POST", "/api/vcenter/host", {"hostname": esx, "user_name": "root",
         "password": vault("esxi_root_password"), "thumbprint_verification": "NONE",
         "folder": folder, "force_add": True}, auth=s)
    print(f"hôte {esx} ajouté")
call("DELETE", "/api/session", auth=s)
PY
}

# Données cloud-init (NoCloud, CD « cidata ») d'une VM source : compte debian
# avec la clé de node1, IP fixe, et un service d'écritures continues (churn)
# pour que les copies incrémentales aient quelque chose à copier.
render_seed() {  # $1 nom, $2 IP, $3 répertoire de sortie
    local name="$1" ip="$2" out="$3" key
    key="$(cat "$HOME/.ssh/id_ed25519.pub")"
    install -d "$out"
    cat > "$out/meta-data" <<EOF
instance-id: $name
local-hostname: $name
EOF
    cat > "$out/network-config" <<EOF
version: 2
ethernets:
  id0:
    match:
      name: "e*"
    addresses: [$ip/16]
    gateway4: 172.16.0.1
EOF
    cat > "$out/user-data" <<EOF
#cloud-config
users:
  - name: debian
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys: ["$key"]
write_files:
  - path: /usr/local/bin/churn.sh
    permissions: "0755"
    content: |
      #!/bin/sh
      # Écritures continues : 4 Mio toutes les CHURN_PAUSE secondes, sur un
      # jeu tournant de 64 fichiers, plus un compteur et l'heure du dernier
      # passage (lus après migration pour dater la coupure).
      mkdir -p /var/lib/churn
      i=\$(cat /var/lib/churn/counter 2>/dev/null || echo 0)
      while :; do
        i=\$((i + 1))
        dd if=/dev/urandom of=/var/lib/churn/blk-\$((i % 64)) bs=1M count=4 conv=fsync status=none
        echo \$i > /var/lib/churn/counter
        date +%s.%N > /var/lib/churn/last
        sync
        sleep \${CHURN_PAUSE:-5}
      done
  - path: /etc/systemd/system/churn.service
    content: |
      [Unit]
      Description=Ecritures continues (banc de migration)
      [Service]
      ExecStart=/usr/local/bin/churn.sh
      Restart=always
      [Install]
      WantedBy=multi-user.target
runcmd:
  - [systemctl, daemon-reload]
  - [systemctl, enable, --now, churn.service]
EOF
    xorriso -as mkisofs -V cidata -J -R -o "$out/seed.iso" \
        "$out/user-data" "$out/meta-data" "$out/network-config" >/dev/null 2>&1
}

DC=vmwlab-dc
DS=datastore1
SRC_IMAGE="$MEDIA/vmware-lab/debian-12-generic-amd64.qcow2"

# Une VM source Debian 12 (image cloud officielle, empreinte vérifiée par
# SHA512SUMS au téléchargement), disque sur PVSCSI, carte vmxnet3, CBT actif.
make_source() {  # $1 numéro (1..4)
    local n="$1" name="vmwlab-src-$1" ip="172.16.2.8$(( $1 + 1 ))" tmp
    if govc_vc vm.info "$name" 2>/dev/null | grep "^Name:" >/dev/null; then
        say "$name existe déjà"; return
    fi
    tmp="$(mktemp -d)"
    qemu-img convert -O vmdk -o subformat=streamOptimized "$SRC_IMAGE" "$tmp/$name.vmdk"
    render_seed "$name" "$ip" "$tmp/seed"
    govc_vc import.vmdk -dc "$DC" -ds "$DS" "$tmp/$name.vmdk" "$name/$name.vmdk" >/dev/null
    govc_vc datastore.upload -dc "$DC" -ds "$DS" "$tmp/seed/seed.iso" "$name/seed.iso" >/dev/null
    rm -rf "$tmp"
    govc_vc vm.create -dc "$DC" -ds "$DS" -host "$ESX_IP" -m 1024 -c 1 -g debian11_64Guest \
        -net "VM Network" -net.adapter vmxnet3 -disk.controller pvscsi -link=false \
        -disk "$name/$name.vmdk" -on=false "$name"
    govc_vc vm.disk.change -dc "$DC" -vm "$name" -size 10G >/dev/null
    local cd; cd="$(govc_vc device.cdrom.add -dc "$DC" -vm "$name")"
    govc_vc device.cdrom.insert -dc "$DC" -ds "$DS" -vm "$name" -device "$cd" "$name/seed.iso"
    # CBT : condition du mode à chaud de Forklift (copies incrémentales).
    govc_vc vm.change -dc "$DC" -vm "$name" -e ctkEnabled=TRUE -e scsi0:0.ctkEnabled=TRUE
    # Console série dans un fichier du datastore (datastore.download pour la
    # lire) : l'image Debian écrit sa console sur ttyS0, l'écran reste noir.
    local ser; ser="$(govc_vc device.serial.add -dc "$DC" -vm "$name")"
    govc_vc device.serial.connect -dc "$DC" -vm "$name" -device "$ser" "[$DS] $name/serial.log"
    govc_vc vm.power -dc "$DC" -on "$name" >/dev/null
    # Le premier démarrage d'une VM créée ainsi s'est déjà bloqué avant le
    # montage de la racine (écran noir, aucune trace ; vécu le 28/09/2026 sur
    # vmwlab-src-1), et un redémarrage l'a débloqué : une seule relance.
    if ! wait_ssh "debian@$ip" 360; then
        say "$name muette après 6 min : redémarrage"
        govc_vc vm.power -dc "$DC" -r -force "$name" >/dev/null
        wait_ssh "debian@$ip" 360 || { say "$name injoignable en SSH"; return 1; }
    fi
    say "$name créée ($ip), CBT actif, SSH ouvert, écritures continues en cours"
}

wait_ssh() {  # $1 compte@hôte, $2 délai en secondes
    local end=$((SECONDS + $2))
    while (( SECONDS < end )); do
        # « exit 0 » : vaut en bash comme dans le PowerShell de Windows
        # (« true » n'y existe pas et la boucle attendait pour rien).
        ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new \
            -o LogLevel=ERROR "$1" "exit 0" 2>/dev/null && return 0
        sleep 10
    done
    return 1
}

# VM source Windows : Windows Server 2025 Standard Evaluation (Core), ISO
# d'évaluation de Microsoft, installée sans intervention par un
# autounattend.xml sur un second CD. Matériel aux pilotes intégrés à
# Windows (LSI Logic SAS, e1000e : vmxnet3 et PVSCSI exigent les VMware
# Tools), BIOS (pas d'invite « press any key » sur un disque vierge), CBT.
WIN_ISO="${VMWLAB_WIN_ISO:-$MEDIA/vmware-lab/windows_server_2025_eval_x64fre_en-us.iso}"

render_windows_cd() {  # $1 nom, $2 IP, $3 répertoire du CD
    local name="$1" ip="$2" cd="$3" pw
    vault_env
    pw="$(vault_field windows_admin_password)"
    install -d -m 700 "$cd"
    cat "$HOME/.ssh/id_ed25519.pub" > "$cd/authorized_keys"
    cat > "$cd/churn.ps1" <<'EOF'
# Écritures continues : 4 Mio toutes les 5 s sur un jeu tournant de 64
# fichiers, plus un compteur et l'heure du dernier passage (ms UTC).
$d = 'C:\churn'; $i = 0
if (Test-Path "$d\counter") { $i = [int](Get-Content "$d\counter") }
$buf = New-Object byte[] (4MB)
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
while ($true) {
    $i++
    $rng.GetBytes($buf)
    [IO.File]::WriteAllBytes("$d\blk-$($i % 64)", $buf)
    Set-Content "$d\counter" $i
    Set-Content "$d\last" ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())
    Start-Sleep 5
}
EOF
    cat > "$cd/vmwlab-setup.ps1" <<EOF
# Premier démarrage : IP fixe, SSH par clé (OpenSSH est livré avec Windows
# Server 2025, pas d'accès réseau requis), service d'écritures continues.
\$nic = Get-NetAdapter | Where-Object Status -eq 'Up' | Select-Object -First 1
New-NetIPAddress -InterfaceIndex \$nic.ifIndex -IPAddress $ip -PrefixLength 16 -DefaultGateway 172.16.0.1
Set-DnsClientServerAddress -InterfaceIndex \$nic.ifIndex -ServerAddresses 172.16.3.6
Copy-Item "\$PSScriptRoot\authorized_keys" C:\ProgramData\ssh\administrators_authorized_keys -Force
icacls C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
New-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -Value 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -PropertyType String -Force
New-NetFirewallRule -DisplayName 'SSH' -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow
Set-Service sshd -StartupType Automatic
Start-Service sshd
New-Item -ItemType Directory -Force C:\churn | Out-Null
Copy-Item "\$PSScriptRoot\churn.ps1" C:\churn\churn.ps1 -Force
\$a = New-ScheduledTaskAction -Execute powershell.exe -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\churn\churn.ps1'
Register-ScheduledTask -TaskName churn -Action \$a -Trigger (New-ScheduledTaskTrigger -AtStartup) -User SYSTEM -RunLevel Highest -Force
Start-ScheduledTask -TaskName churn
reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v AutoAdminLogon /t REG_SZ /d 0 /f
EOF
    cat > "$cd/autounattend.xml" <<EOF
<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend">
  <settings pass="windowsPE">
    <component name="Microsoft-Windows-International-Core-WinPE" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <SetupUILanguage><UILanguage>en-US</UILanguage></SetupUILanguage>
      <InputLocale>en-US</InputLocale><SystemLocale>en-US</SystemLocale>
      <UILanguage>en-US</UILanguage><UserLocale>en-US</UserLocale>
    </component>
    <component name="Microsoft-Windows-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
      <DiskConfiguration>
        <Disk wcm:action="add">
          <DiskID>0</DiskID>
          <WillWipeDisk>true</WillWipeDisk>
          <CreatePartitions>
            <CreatePartition wcm:action="add"><Order>1</Order><Type>Primary</Type><Size>500</Size></CreatePartition>
            <CreatePartition wcm:action="add"><Order>2</Order><Type>Primary</Type><Extend>true</Extend></CreatePartition>
          </CreatePartitions>
          <ModifyPartitions>
            <ModifyPartition wcm:action="add"><Order>1</Order><PartitionID>1</PartitionID><Label>System</Label><Format>NTFS</Format><Active>true</Active></ModifyPartition>
            <ModifyPartition wcm:action="add"><Order>2</Order><PartitionID>2</PartitionID><Label>Windows</Label><Format>NTFS</Format><Letter>C</Letter></ModifyPartition>
          </ModifyPartitions>
        </Disk>
      </DiskConfiguration>
      <ImageInstall>
        <OSImage>
          <InstallFrom><MetaData wcm:action="add"><Key>/IMAGE/NAME</Key><Value>Windows Server 2025 Standard Evaluation</Value></MetaData></InstallFrom>
          <InstallTo><DiskID>0</DiskID><PartitionID>2</PartitionID></InstallTo>
        </OSImage>
      </ImageInstall>
      <UserData><AcceptEula>true</AcceptEula><FullName>Administrator</FullName><Organization>vmwlab</Organization></UserData>
    </component>
  </settings>
  <settings pass="specialize">
    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <ComputerName>$name</ComputerName>
      <TimeZone>Romance Standard Time</TimeZone>
    </component>
  </settings>
  <settings pass="oobeSystem">
    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
      <UserAccounts><AdministratorPassword><Value>$pw</Value><PlainText>true</PlainText></AdministratorPassword></UserAccounts>
      <AutoLogon><Enabled>true</Enabled><Username>Administrator</Username><Password><Value>$pw</Value><PlainText>true</PlainText></Password><LogonCount>1</LogonCount></AutoLogon>
      <OOBE><HideEULAPage>true</HideEULAPage><HideLocalAccountScreen>true</HideLocalAccountScreen><HideOEMRegistrationScreen>true</HideOEMRegistrationScreen><HideOnlineAccountScreens>true</HideOnlineAccountScreens><HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE><NetworkLocation>Work</NetworkLocation><ProtectYourPC>3</ProtectYourPC></OOBE>
      <FirstLogonCommands>
        <SynchronousCommand wcm:action="add">
          <Order>1</Order>
          <CommandLine>cmd /c for %d in (D E F G H) do if exist %d:\\vmwlab-setup.ps1 powershell -NoProfile -ExecutionPolicy Bypass -File %d:\\vmwlab-setup.ps1 &gt; C:\\vmwlab-setup.log 2&gt;&amp;1</CommandLine>
          <Description>Configuration du banc</Description>
        </SynchronousCommand>
      </FirstLogonCommands>
    </component>
  </settings>
</unattend>
EOF
    chmod 600 "$cd"/*
}

make_windows() {  # $1 numéro (3 ou 4)
    local name="vmwlab-src-$1" ip="172.16.2.8$(( $1 + 1 ))" tmp cd
    if govc_vc vm.info "$name" 2>/dev/null | grep "^Name:" >/dev/null; then
        # Reprise : le CD du fichier de réponses (mot de passe) ne doit pas
        # rester, même après une installation interrompue côté outil.
        # (un lecteur restant, ou le fichier : une éjection refusée peut avoir
        # détaché l'ISO du lecteur en laissant le fichier au datastore)
        if govc_vc device.ls -dc "$DC" -vm "$name" | grep "^cdrom-" >/dev/null \
            || govc_vc datastore.ls -dc "$DC" -ds "$DS" "$name/unattend.iso" >/dev/null 2>&1; then
            wait_ssh "Administrator@$ip" 5400 || { say "$name injoignable en SSH"; return 1; }
            unattend_cleanup "$name" "$ip"
        fi
        say "$name existe déjà"; return
    fi
    [[ -s "$WIN_ISO" ]] || { say "ISO Windows absente ($WIN_ISO)"; return 1; }
    if ! govc_vc datastore.ls -dc "$DC" -ds "$DS" "iso/$(basename "$WIN_ISO")" >/dev/null 2>&1; then
        say "envoi de l'ISO Windows dans le datastore"
        govc_vc datastore.mkdir -dc "$DC" -ds "$DS" -p iso
        govc_vc datastore.upload -dc "$DC" -ds "$DS" "$WIN_ISO" "iso/$(basename "$WIN_ISO")" >/dev/null
    fi
    tmp="$(mktemp -d)"; chmod 700 "$tmp"
    render_windows_cd "$name" "$ip" "$tmp/cd"
    xorriso -as mkisofs -V UNATTEND -J -R -o "$tmp/unattend.iso" "$tmp/cd" >/dev/null 2>&1
    govc_vc datastore.mkdir -dc "$DC" -ds "$DS" -p "$name"
    govc_vc datastore.upload -dc "$DC" -ds "$DS" "$tmp/unattend.iso" "$name/unattend.iso" >/dev/null
    rm -rf "$tmp"
    govc_vc vm.create -dc "$DC" -ds "$DS" -host "$ESX_IP" -m 4096 -c 2 -g windows2019srv_64Guest \
        -net "VM Network" -net.adapter e1000e -disk.controller lsilogic-sas -disk 40GB \
        -firmware bios -iso "iso/$(basename "$WIN_ISO")" -on=false "$name"
    local cd2; cd2="$(govc_vc device.cdrom.add -dc "$DC" -vm "$name")"
    govc_vc device.cdrom.insert -dc "$DC" -ds "$DS" -vm "$name" -device "$cd2" "$name/unattend.iso"
    govc_vc vm.change -dc "$DC" -vm "$name" -e ctkEnabled=TRUE -e scsi0:0.ctkEnabled=TRUE
    govc_vc vm.power -dc "$DC" -on "$name" >/dev/null
    say "installation de Windows sur $name ($ip), 30 à 90 min en imbriqué"
    wait_ssh "Administrator@$ip" 5400 || { say "$name injoignable en SSH après 90 min"; return 1; }
    unattend_cleanup "$name" "$ip"
    say "$name installée ($ip), CBT actif, SSH ouvert, écritures continues en cours"
}

# Le second CD porte le mot de passe administrateur : retiré dès
# l'installation finie. Un lecteur IDE ne se retire ni ne s'éjecte VM allumée
# sur cet ESXi imbriqué (« Connection control operation failed for disk
# ide0:1 », vécu le 28/09/2026) : arrêt propre de Windows par SSH, retrait
# des deux lecteurs (la VM source n'en garde aucun), effacement du fichier,
# redémarrage (la tâche churn repart seule).
unattend_cleanup() {  # $1 nom de la VM, $2 IP
    local dev
    ssh -o BatchMode=yes -o LogLevel=ERROR "Administrator@$2" "Stop-Computer -Force" >/dev/null 2>&1 || true
    for _ in $(seq 1 60); do
        govc_vc vm.info -dc "$DC" "$1" | grep poweredOff >/dev/null && break
        sleep 5
    done
    govc_vc vm.info -dc "$DC" "$1" | grep poweredOff >/dev/null || govc_vc vm.power -dc "$DC" -off -force "$1" >/dev/null
    for dev in $(govc_vc device.ls -dc "$DC" -vm "$1" | awk '/^cdrom-/ {print $1}'); do
        govc_vc device.remove -dc "$DC" -vm "$1" "$dev" >/dev/null
    done
    govc_vc datastore.rm -dc "$DC" -ds "$DS" "$1/unattend.iso" >/dev/null 2>&1 || true
    govc_vc vm.power -dc "$DC" -on "$1" >/dev/null
    wait_ssh "Administrator@$2" 900 || say "$1 : pas de SSH après le redémarrage"
}

cmd_inventory() {
    govc_vc about >/dev/null 2>&1 || { say "vCenter injoignable : lancer « vcenter » d'abord"; exit 1; }
    vc_rest setup
    make_source 1
    make_source 2
    make_windows 3
    autostart "$VC" vmwlab-src-1 vmwlab-src-2 vmwlab-src-3
    say "démarrage automatique avec l'ESXi : vCenter puis VMs sources"
}

cmd_status() {
    on_node2 "sudo virsh list --all | grep -E '$ESX|Name' || true"
    filter_active && say "filtre : actif" || say "filtre : ABSENT"
    if govc_esx about >/dev/null 2>&1; then
        say "ESXi : $(govc_esx about | sed -n 's/^FullName: *//p')"
    else
        say "ESXi : injoignable"
    fi
    if govc_vc about >/dev/null 2>&1; then
        say "vCenter : $(govc_vc about | sed -n 's/^FullName: *//p')"
    else
        say "vCenter : injoignable"
    fi
}

# Arrêt propre : les VMs sources par SSH (elles n'ont pas les VMware Tools,
# l'arrêt de l'invité par l'API leur est impossible), le vCenter par son
# invité, puis l'ESXi. Une VM encore allumée après 5 min est coupée.
cmd_stop() {
    if govc_esx about >/dev/null 2>&1; then
        local entry target cmd
        # ssh -n : sinon ssh lit l'entrée de la boucle et avale les lignes
        # suivantes (piège déjà payé sur harvlab).
        while IFS= read -r entry; do
            target="${entry%%:*}"; cmd="${entry#*:}"
            ssh -n -o BatchMode=yes -o ConnectTimeout=5 -o LogLevel=ERROR "$target" "$cmd" >/dev/null 2>&1 || true
        done < <(printf '%s\n' "debian@172.16.2.82:sudo systemctl poweroff" \
            "debian@172.16.2.83:sudo systemctl poweroff" \
            "Administrator@172.16.2.84:Stop-Computer -Force")
        govc_esx vm.power -s "$VC" >/dev/null 2>&1 || true
        local on=""
        for _ in $(seq 1 60); do
            on="$(govc_esx find / -type m -runtime.powerState poweredOn 2>/dev/null)"
            [[ -z "$on" ]] && break
            sleep 5
        done
        if [[ -n "$on" ]]; then
            say "encore allumées après 5 min, coupées : $(echo "$on" | xargs -n1 basename | tr '\n' ' ')"
            echo "$on" | while read -r v; do govc_esx vm.power -off -force "$v" >/dev/null 2>&1 || true; done
        fi
    fi
    on_node2 "sudo virsh shutdown $ESX >/dev/null 2>&1 || true"
    say "arrêt demandé : VMs du banc éteintes, ESXi en cours d'arrêt"
}

cmd_start() {
    filter_active || { say "filtre absent : lancer « filter » d'abord"; exit 1; }
    on_node2 "sudo virsh start $ESX >/dev/null 2>&1 || true"
    say "ESXi démarré ; le vCenter et les VMs sources suivent (démarrage automatique, ~15 min pour le vCenter)"
}

cmd_destroy() {
    read -r -p "Supprimer la VM $ESX et ses disques ? (tapez $LAB) " answer
    [[ "$answer" == "$LAB" ]] || { say "abandon"; exit 1; }
    on_node2 "sudo virsh destroy $ESX >/dev/null 2>&1 || true; \
        sudo virsh undefine $ESX --nvram --remove-all-storage >/dev/null 2>&1 || true"
    say "VM et disques supprimés (médias gardés dans $DIR)"
}

case "${1:-}" in
    secrets)   cmd_secrets ;;
    verify)    cmd_verify ;;
    filter)    cmd_filter ;;
    media)     cmd_media ;;
    install)   cmd_install ;;
    vcenter)   cmd_vcenter ;;
    inventory) cmd_inventory ;;
    status)    cmd_status ;;
    stop)      cmd_stop ;;
    start)     cmd_start ;;
    destroy)   cmd_destroy ;;
    # Débogage : kickstart rendu (le mot de passe n'y est que haché).
    render-ks) render_ks ;;
    # Débogage : CD d'installation Windows (contient le mot de passe).
    render-windows) render_windows_cd "$2" "$3" "$4" ;;
    # Tests (test_vmwlab.sh) : vérifie une OVA quelconque.
    verify-ova) verify_ova "$2" ;;
    *) sed -n '2,/^set -euo/p' "$0" | sed '$d'; exit 1 ;;
esac
