# Forklift sur Harvester, étape B2 : vagues à chaud, bascule, retour arrière, vue globale

**Goal:** migrer à chaud des VMs vSphere vers Harvester par vagues depuis la console : composer une vague depuis l'inventaire, suivre les copies, basculer (maintenant ou à une date), revenir à la source, clore la vague ; voir sur tous les clusters quelles VMs sont prises en charge. Release v1.76.0.

**Architecture:** comme B1. Bibliothèque pure `bin/lib/hv_forklift.py` (objets NetworkMap, StorageMap, Plan, Migration et lecture de leur état), nouveau client vSphere `bin/lib/vsphere_api.py` (REST pour l'alimentation, SOAP pour les instantanés, bibliothèque standard), nouvelles commandes de `bin/harvester-forklift.py` (tout est faisable en ligne de commande), routes dans `web/app.py`, onglet « Vagues » dans `web/static/js/forklift.js` et une entrée globale « Migrations (tous clusters) ».

## Ce que le réel a appris avant d'écrire ce plan (29/09/2026, harvlab2 + vmwlab)

Deux vagues réelles faites à la main (objets dans `mig-b2`) :

- vague 1, `vmwlab-src-1`, avec conversion : copie initiale 10 Gio en ~50 s, copies incrémentales ~1 min 30, **coupure 6 min 24 s** dont ~4 min 30 de conversion de l'invité ;
- vague 2, `vmwlab-src-2`, `skipGuestConversion: true` + `useCompatibilityMode: false` : **coupure 1 min 44 s** ;
- retour à la source : VM Harvester arrêtée en 17 s (`runStrategy: Halted`), source rallumée par vCenter ; ce qui a été écrit sur Harvester après la bascule est perdu ;
- un fichier écrit entre deux copies est retrouvé identique après la bascule ; IP fixe et MAC conservées sur un réseau ponté.

Obstacles, chacun devenu une règle de l'écran :

1. **L'importeur CDI de Harvester (SUSE, 1.65.0) n'a pas le greffon nbdkit VDDK** : aucune copie VDDK possible. Pas corrigé dans 1.9.1-rc1/rc2 ni 1.10 dev. Contournement : `IMPORTER_IMAGE` (et `OVIRT_POPULATOR_IMAGE`) de `harvester-system/cdi-operator` sur l'image amont de même version. -> étape de Préparation.
2. **La bascule arrête la source par les VMware Tools** ; sans outils : échec (« VMware Tools is not running »). L'inventaire le dit : `guestNameFromVmwareTools` vide et aucune IP pour une VM allumée sans outils. -> VM refusée dans une vague.
3. **Intervalle entre copies = réglage global** du ForkliftController (`controller_precopy_interval`, 60 min par défaut, redémarre le contrôleur, ne replanifie pas une copie déjà prévue). -> réglage de Préparation, 15 min proposé.
4. **Un essai raté laisse des instantanés `forklift-migration-precopy`** sur la source (aucun après un essai réussi). L'API REST de vCenter 8.0 n'a pas d'instantanés : SOAP. -> « Clore la vague » et « Nettoyer la source » les retirent, seulement ceux-là.

## Décisions de l'exploitant (29/09/2026, toutes sur recommandation)

- Préparation : détection de l'importeur sans VDDK, bascule vers l'image amont (ou un miroir), retour à l'image d'origine possible.
- Copie brute (sans conversion) proposée par vague, conversion par défaut.
- VMs sans VMware Tools en marche refusées, avec la raison dans l'inventaire.
- Intervalle des copies réglable, 15 min proposé.
- Clôture qui retire les instantanés de Forklift des sources.
- Retour à la source par VM et par vague, confirmation qui rappelle la perte des écritures faites après la bascule.
- Vue globale ; une VM déjà prise en charge par une vague d'un autre cluster (même vCenter, même identifiant de VM) est **refusée**.
- Navigation A : onglet « Vagues » dans « Migrations VMware » du cluster, entrée « Migrations (tous clusters) » à côté d'Activity.

## Global Constraints

- L'UI ne contourne jamais `bin/harvester-forklift.py` pour écrire : `_cli_action(...)` (action suivie, dock, Activité).
- Aucun secret (mot de passe vCenter, identifiants de registre) en argument, réponse HTTP, libellé, `STEP_EVENT` ou journal ; les identifiants vCenter sont relus par l'outil dans le secret du fournisseur, jamais transmis par le navigateur.
- Bibliothèque standard seulement dans `bin/`.
- Écritures réservées aux administrateurs (préfixe `/api/forklift` déjà admin), `@requires_auth` + `@_rate_limit` sur chaque route mutative.
- Les objets d'une vague (NetworkMap, StorageMap, Plan, Migration) vivent dans le namespace `forklift`, étiquetés `harvester-ops.io/managed: "true"` et `harvester-ops.io/wave: <nom>` ; `spec.targetNamespace` = namespace choisi pour les VMs.
- Identité d'une VM source dans toute la console : (hôte du vCenter tiré de `spec.url` du fournisseur, identifiant d'inventaire `vm-NN`).
- Tooltip sur chaque contrôle, `esc()` avant `innerHTML`, i18n dans les CINQ langues, aucun `new EventSource(` brut.
- Code et noms en anglais, commentaires en français, messages de l'outil en anglais.
- `python3 -m pytest tests/api/ -q` vert avant chaque commit ; commits `feat(1.76.0): ...` / `fix(1.76.0): ...`, sans aucune ligne d'attribution, sans tiret cadratin ni flèche Unicode. Aucune mention de l'outillage dans ce qui est commité.
- VERSION reste 1.75.0 jusqu'à la tâche de release.

---

### Task W1 : bibliothèque, objets et état d'une vague

**Files:** `bin/lib/hv_forklift.py`, `tests/api/test_forklift_b2_176.py` (nouveau)

À ajouter (fonctions pures, testées avec des objets réels relevés dans `mig-b2` quand c'est possible : les copier dans `tests/api/fixtures/forklift_b2_*_176.json`, secrets exclus) :

- `L_WAVE = "harvester-ops.io/wave"`, `A_ROLLED_BACK = "harvester-ops.io/rolled-back"` (liste d'identifiants de VM séparés par des virgules), `A_CLOSED = "harvester-ops.io/closed"`.
- `check_wave_name(name)` : RFC 1123, 40 caractères au plus (les objets dérivés ajoutent des suffixes).
- `wave_manifests(spec) -> [NetworkMap, StorageMap, Plan]` : `spec = {name, target_namespace, provider: {namespace, name}, vms: ["vm-16", ...], networks: [{source: "network-13", destination: "pod" | "<ns>/<nad>"}], storages: [{source: "datastore-12", storage_class: "harv-rep1"}], skip_conversion: bool, compat_mode: bool, preserve_static_ips: bool}`. Noms : `<name>-net`, `<name>-sto`, Plan `<name>`. Destination réseau `pod` -> `{type: pod}`, sinon `{type: multus, namespace, name}`. Plan : `warm: true`, `skipGuestConversion`, `useCompatibilityMode` (seulement si `skip_conversion`), `preserveStaticIPs`, `targetNamespace`, `provider.destination = forklift/host`. Refus (`ValueError`, messages anglais) : liste de VMs vide, VM en double, réseau ou stockage source non mappé n'est PAS vérifiable ici (fait par W3 avec l'inventaire).
- `migration_manifest(wave, n)` : Migration `<wave>-m<n>` ; `cutover_patch(when)` (RFC 3339, `when` ne peut pas être dans le passé de plus de 5 min).
- `wave_state(plan, migrations) -> dict` : `{name, target_namespace, state, vms: [...], next_precopy, cutover}` ; `state` parmi `ready` (plan prêt, pas lancé), `invalid` (condition critique, message), `copying`, `cutover-scheduled`, `cutting-over`, `succeeded`, `failed`, `rolled-back` (toutes les VMs dans l'annotation), `closed` (annotation ou `spec.archived`). Par VM : `{id, name, phase, step (libellé court), progress: {done, total}, precopies, last_precopy: {start, end, seconds}, next_precopy, error, rolled_back}`. Lire `status.migration.vms[]` (`phase`, `pipeline[]` avec `progress.completed/total`, `warm.precopies[]` avec `start`/`end`, `warm.nextPrecopyAt`, `error.reasons`). Relevé réel : pipeline `Initialize, DiskTransfer, Cutover, ImageConversion (absent en copie brute), VirtualMachineCreation`.
- `vm_warm_blockers(row)` à partir d'une ligne d'inventaire enrichie : `cbt` faux -> « Changed Block Tracking is off » ; allumée sans outils (`tools` faux) -> « VMware Tools are not running: the switchover cannot shut the source down » ; renvoie la liste des raisons. `inventory_rows("vms", ...)` gagne `tools` (vrai si `guestNameFromVmwareTools` non vide ou `ipAddress` non vide), `snapshot` (id courant ou ""), `uuid`.
- `taken_vms(plans, provider_hosts) -> {(host, vm_id): {"wave", "state"}}` pour les plans étiquetés console non clos ; `provider_host(provider)` = hôte de `spec.url` en minuscules.
- `cdi_importer_state(operator_deploy) -> {"image", "kind": "suse-no-vddk" | "upstream" | "other", "original": <annotation>}` : image `registry.suse.com/.../cdi-importer:*` = `suse-no-vddk` ; `quay.io/kubevirt/cdi-importer:*` = `upstream`. `cdi_importer_patch(image, keep_original)` : env `IMPORTER_IMAGE` et `OVIRT_POPULATOR_IMAGE` + annotation `harvester-ops.io/original-importer-image` posée une seule fois.
- `precopy_interval(controller) -> int` (défaut 60) et `precopy_patch(minutes)` (5 à 1440).

Tests : chaque fonction, cas limites, et un test par règle du réel ci-dessus.

### Task W2 : client vSphere (alimentation, instantanés)

**Files:** `bin/lib/vsphere_api.py` (nouveau), `tests/api/test_vsphere_api_176.py` (nouveau)

`class VSphere(url, user, password, cacert=None, insecure=False)` (bibliothèque standard, `ssl` selon `cacert`/`insecure`, jamais d'identifiant dans un message) :

- REST : `session()` (`POST /api/session`, Basic), `power_state(vm_id)`, `power_on(vm_id)` (`POST /api/vcenter/vm/{vm}/power?action=start`), `logout()`.
- SOAP (`POST /sdk`, `vim25`) : `soap_login()`, `snapshots(vm_id) -> [{id, name, created, children}]` (PropertyCollector `RetrievePropertiesEx` sur `snapshot.rootSnapshotList`), `remove_snapshot(snap_id, consolidate=True) -> task_id` (`RemoveSnapshot_Task`, `removeChildren=false`), `wait_task(task_id, timeout)` (propriété `info.state`), `soap_logout()`.
- `forklift_snapshots(tree) -> [ids]` : seulement les instantanés nommés `forklift-migration-precopy`, des feuilles vers la racine.
- Erreurs : `VSphereError` (message court, sans identifiant, sans chemin).

Tests contre un faux vCenter local (`http.server` en fil, réponses SOAP réelles abrégées : les capturer une fois sur le banc et les garder en fixtures), y compris : session refusée, tâche en échec, arbre d'instantanés mêlant ceux de Forklift et un instantané de l'exploitant (ce dernier n'est jamais retiré).

### Task W3 : l'outil, commandes des vagues

**Files:** `bin/harvester-forklift.py`, `tests/api/test_forklift_b2_cli_176.py` (nouveau)

Nouvelles commandes (toutes avec `--cluster/--kubeconfig`, demandes JSON par `--spec FICHIER` ou stdin) :

- `wave-apply --spec` : relit l'inventaire (réutiliser le chemin de `cmd_inventory`), refuse toute VM avec des `vm_warm_blockers`, tout réseau ou datastore utilisé par une VM et non mappé, puis applique maps et plan, attend `Ready` (ou la condition critique de Forklift, dite telle quelle).
- `wave-start --wave N` : crée la Migration suivante (`-m<n>`), refuse si une migration de la vague est en cours.
- `wave-cutover --wave N [--at RFC3339]` : pose `spec.cutover` sur la migration en cours (maintenant par défaut).
- `wave-status --wave N` / `waves` : `wave_state` en JSON.
- `wave-rollback --wave N [--vm vm-16 ...]` : pour chaque VM (toutes par défaut) : `runStrategy: Halted` sur la VM Harvester et attente de la disparition de la VMI, puis `power_on` de la source par `VSphere` (identifiants lus dans le secret du fournisseur), annotation `A_ROLLED_BACK` mise à jour ; une VM déjà rallumée côté vCenter n'est pas retouchée.
- `wave-close --wave N [--clean-snapshots]` : `spec.archived: true` + annotation `A_CLOSED` ; avec `--clean-snapshots`, retire par SOAP les seuls instantanés `forklift-migration-precopy` des VMs de la vague.
- `wave-delete --wave N` : refusé si une migration est en cours ; supprime plan, migrations, maps (les VMs Harvester créées restent).
- `cdi-importer [--show | --upstream [--image IMG] | --original]` : `--upstream` pose `quay.io/kubevirt/cdi-importer:v<version CDI>` ou l'image donnée (miroir), attend le déploiement de `cdi-deployment` ; `--original` remet l'annotation.
- `precopy-interval MINUTES` : patch du ForkliftController, attend le redémarrage du contrôleur.

Tests : faux cluster en mémoire (même patron que `test_forklift_cli_175.py`), faux `VSphere` injecté.

### Task W4 : routes et vue globale côté serveur

**Files:** `web/app.py` (section v1.75.0 étendue), `tests/api/test_forklift_b2_routes_176.py`

- `GET /api/forklift/<cluster>` gagne `cdi_importer`, `precopy_interval`, `waves: [wave_state]`.
- `POST /api/forklift/<cluster>/do/<action>` gagne `wave-apply`, `wave-start`, `wave-cutover`, `wave-rollback`, `wave-close`, `wave-delete`, `cdi-importer`, `precopy-interval` (validation serveur avant l'action, libellés `forklift:<action>:<vague>`).
- **Refus d'une VM déjà prise en charge** : avant `wave-apply`, la route lit les plans de TOUS les clusters déclarés joignables (`taken_vms`) et refuse en 409 avec « vm-18 is already in wave vague-2 on cluster harvlab2 ».
- `GET /api/forklift-global` : toutes les vagues de tous les clusters (lecture seule, `viewer`), avec par cluster l'état de Forklift et de l'importeur ; clusters injoignables signalés sans bloquer les autres ; cache 15 s par personne.

### Task W5 : Préparation et inventaire

**Files:** `web/static/js/forklift.js`, `web/static/js/i18n.js`, CSS, `tests/e2e/test_forklift_b2_176.py`

- Préparation, nouvelle étape « Importeur de disques (CDI) » entre Forklift et VDDK : état (`suse-no-vddk` en rouge avec l'explication, `upstream` en vert, `other`), bouton « Utiliser l'importeur amont » (champ miroir facultatif), « Revenir à l'image d'origine » ; avertissement : une mise à jour de Harvester peut défaire ce réglage.
- Préparation, réglage « Intervalle entre les copies incrémentales » (minutes, 15 proposé, dit qu'il vaut pour tout le cluster).
- Inventaire : colonne « Outils VMware », raison de refus par VM (CBT, outils), cases à cocher et bouton « Composer une vague » (VMs éligibles seulement).

### Task W6 : onglet « Vagues »

**Files:** mêmes que W5, `web/templates/index.html` (sous-onglet `waves`), `sections.js`

- Liste des vagues : un bloc par vague (état, nombre de VMs, prochaine copie, bascule prévue), actions selon l'état : Lancer, Basculer maintenant, Planifier la bascule, Revenir à la source, Clore, Supprimer.
- Fenêtre « Composer une vague » : nom, namespace cible, VMs choisies (depuis l'inventaire), correspondances réseau (chaque réseau source utilisé -> `pod` ou un NAD du cluster, présélection par nom égal) et stockage (chaque datastore -> une classe, présélection = classe par défaut), options (copie brute avec explication et proposition automatique pour les invités Linux, mode compatibilité, IP fixes conservées).
- Suivi d'une vague (fenêtre) : par VM, étape, progression du disque, nombre de copies, durée de la dernière, prochaine ; compte à rebours de la bascule ; erreurs de Forklift traduites quand elles sont connues (VDDK, outils).
- Confirmations : bascule (dit que la source sera arrêtée), retour à la source (dit que ce qui a été écrit sur Harvester depuis la bascule est perdu), clôture.

### Task W7 : vue globale « Migrations (tous clusters) »

**Files:** `web/templates/index.html` (entrée de menu à côté d'Activity, section), `web/static/js/forklift-global.js` (nouveau), i18n, e2e

Tableau : VM VMware, vCenter, cluster cible, vague, étape, dernière copie, bascule ; filtres Toutes / En cours / Bascule à planifier / Terminées / En échec, filtre vCenter ; une ligne par cluster en tête (Forklift prêt, importeur, sources). Clic sur une vague : ouvre l'onglet Vagues du cluster.

### Task W8 : réel sur harvlab2 + vmwlab (contrôleur)

Remettre les sources à leur état (src-1 et src-2 rallumées dans vCenter, VMs de `mig-b2` supprimées, `mig-b2` supprimé), remettre l'importeur SUSE, puis tout refaire depuis l'écran : Préparation (importeur amont, intervalle 5 min), une vague Linux en copie brute et une avec conversion, refus de `vmwlab-src-3` (pas d'outils), refus d'une VM déjà prise (vague sur harvlab2, tentative de la même VM dans une seconde vague), bascule planifiée, retour à la source d'une VM, clôture avec nettoyage d'instantanés (en provoquer un par un essai volontairement raté), vue globale. Captures pour l'exploitant. Tout écart : test + correction.

### Task W9 : doc, parité, release 1.76.0

Doc EN/FR (sections Migrations VMware : Vagues, Préparation complétée, vue globale, limites : importeur CDI, outils VMware, coupure mesurée avec et sans conversion), parité (ligne forklift en `ok`), CHANGELOG, VERSION 1.76.0, suite complète, paquet et essai du paquet par l'unité, GitHub, tableau de parité republié.
