#!/usr/bin/env bash
# Tests de la vérification d'OVA de vmwlab.sh, sur des OVA fabriquées et
# signées ici par un certificat jetable : rien ne touche au banc.
#   bash tests/bench/vmware/test_vmwlab.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL="$HERE/vmwlab.sh"
W="$(mktemp -d)"
trap 'rm -rf "$W"' EXIT
fails=0

check() {  # $1 libellé, $2 code attendu (0 ou 1), $3 OVA, $4 empreinte acceptée
    local got
    VMWLAB_OVF_CERT_SHA256="$4" "$TOOL" verify-ova "$3" >"$W/out" 2>&1
    got=$?
    if [[ "$got" == "$2" ]]; then
        echo "ok   $1"
    else
        echo "KO   $1 (code $got, attendu $2)"; sed 's/^/     /' "$W/out"; fails=$((fails + 1))
    fi
}

# Une OVA au format de VMware : .ovf, .mf (SHA256), .cert (signature du .mf
# puis certificat), disques.
make_ova() {  # $1 répertoire de travail, $2 nom de l'OVA ; fichiers extra après
    local d="$1" out="$2"; shift 2
    (cd "$d" && {
        for f in lab.ovf lab-disk1.vmdk; do echo "SHA256($f)= $(sha256sum "$f" | cut -d' ' -f1)"; done > lab.mf
        printf 'SHA256(lab.mf)= %s\n' "$(openssl dgst -sha256 -sign key.pem lab.mf | od -An -tx1 | tr -d ' \n')" > lab.cert
        cat cert.pem >> lab.cert
        tar cf "$out" lab.ovf lab.mf lab.cert lab-disk1.vmdk "$@"
    })
}

mkdir "$W/a"
openssl req -x509 -newkey rsa:2048 -nodes -keyout "$W/a/key.pem" -out "$W/a/cert.pem" -days 1 \
    -subj "/C=US/ST=California/L=Palo Alto/O=VMware, Inc." >/dev/null 2>&1
FP="$(openssl x509 -in "$W/a/cert.pem" -noout -fingerprint -sha256 | cut -d= -f2)"
echo '<Envelope/>' > "$W/a/lab.ovf"
head -c 3000000 /dev/urandom > "$W/a/lab-disk1.vmdk"
make_ova "$W/a" "$W/good.ova"

check "OVA intacte, certificat attendu" 0 "$W/good.ova" "$FP"
# Même sujet « VMware, Inc. » mais une autre clé : c'est l'empreinte qui compte.
check "certificat au bon nom mais inconnu" 1 "$W/good.ova" "00:11:22"

# Disque modifié APRÈS la signature du manifeste.
cp -r "$W/a" "$W/b"
make_ova "$W/b" "$W/tmp.ova"
(cd "$W/b" && printf 'x' | dd of=lab-disk1.vmdk bs=1 seek=1000 conv=notrunc 2>/dev/null \
    && tar cf "$W/tampered.ova" lab.ovf lab.mf lab.cert lab-disk1.vmdk)
check "disque modifié après signature" 1 "$W/tampered.ova" "$FP"

# Manifeste réécrit (empreintes recalculées) sans nouvelle signature.
cp -r "$W/a" "$W/c"
(cd "$W/c" && make_ova "$W/c" "$W/tmp2.ova" && echo "SHA256(lab.ovf)= $(sha256sum lab.ovf | cut -d' ' -f1)" > lab.mf \
    && echo "SHA256(lab-disk1.vmdk)= $(sha256sum lab-disk1.vmdk | cut -d' ' -f1)" >> lab.mf \
    && echo "SHA256(extra.bin)= 00" >> lab.mf && tar cf "$W/remf.ova" lab.ovf lab.mf lab.cert lab-disk1.vmdk)
check "manifeste réécrit sans signature" 1 "$W/remf.ova" "$FP"

# Fichier glissé dans l'OVA hors du manifeste.
cp -r "$W/a" "$W/e"
echo 'payload' > "$W/e/extra.sh"
make_ova "$W/e" "$W/extra.ova" extra.sh
check "fichier hors manifeste" 1 "$W/extra.ova" "$FP"

# Disque retiré de l'OVA (manifeste signé intact).
(cd "$W/a" && tar cf "$W/missing.ova" lab.ovf lab.mf lab.cert)
check "disque du manifeste absent" 1 "$W/missing.ova" "$FP"

[[ $fails == 0 ]] && echo "tous les tests passent" || { echo "$fails échec(s)"; exit 1; }
