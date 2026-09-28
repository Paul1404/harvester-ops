# Migrer des dizaines de VMs de VMware vers Harvester, avec une coupure courte

État : conception validée par l'exploitant le 27/09/2026 (quatre sections
approuvées une à une), à relire avant le plan d'implémentation. Demande :
« harvops capable de gérer des dizaines de migrations vmware vers harvester
et que ce soit visuel [...] gérer l'interruption de service, donc la copie
incrémentale pour une coupure la plus courte possible ».

## Ce qui existe déjà, et pourquoi ça ne suffit pas

- **Forklift** (projet Konveyor) sait migrer de vSphere vers KubeVirt, à
  froid ou **à chaud** (warm) : copie initiale VM allumée, puis copies
  incrémentales des blocs changés (CBT de VMware), puis bascule. Son
  interface complète est un greffon de la console OpenShift, inutilisable
  ailleurs.
- **Harvester 1.9** propose un assistant Forklift quand l'add-on
  `forklift-operator` est activé (fournisseurs, correspondances réseau et
  stockage, plans ; API `forklift.konveyor.io/v1beta1`, namespace
  `forklift`). Son code force `warm: false`
  (`ReviewMigrationStep.vue`, harvester-ui-extension v1.9.0) : migrations à
  froid seulement, la VM est arrêtée pendant toute la copie.
- **vm-import-controller** (add-on Harvester, actif sur harv1) importe depuis
  VMware à froid seulement.

La valeur de la console est donc : le mode à chaud, piloté par vagues, suivi
visuellement, avec la bascule maîtrisée et le retour arrière.

## Décisions de l'exploitant

| Question | Décision |
|---|---|
| Source VMware pour tester en réel | Banc imbriqué sur node2 (ESXi + vCenter en évaluation) avec les médias de l'exploitant, **vérifiés** avant usage : ESXi 7.0 GA et VDDK 8.0.3 par leurs empreintes publiées, vCenter 8.0.1 par la signature VMware de son OVA ; réseau du banc filtré |
| Moment de la bascule | Les deux : date planifiée par vague ET bouton « Basculer maintenant » ; la taille de la dernière copie incrémentale est montrée |
| Regroupement | Vagues composées à la main depuis l'inventaire vCenter (une vague = un plan Forklift = une bascule) |
| Après la bascule | Sources gardées éteintes, retour arrière possible ; la console ne supprime jamais une VM dans vCenter |
| Moteur | Piloter Forklift (pas de moteur propre, pas de repli à froid par vm-import-controller) |
| Écran | Couloirs par vague sur un axe de temps commun |

## Découpage : trois sous-projets, dans cet ordre

Chacun est vérifié en réel avant le suivant.

### A. Le banc VMware sur node2

- ESXi 7.0 imbriqué (VM KVM sur node2) et un vCenter 8.0.1 (VCSA « tiny »,
  déployé DANS l'ESXi) en mode évaluation (60 jours) ; vCenter 8 gère les
  ESXi 7.0 et 8.0. Budget : 28 Gio de mémoire pour la VM ESXi (le vCenter en
  prend 14), ce qui tient avec harvlab2 ; harvlab et le banc VMware
  ensemble serrent node2, on n'en allume qu'un des deux gros.
- Réseau du banc filtré sur node2 (versions non corrigées) : il ne parle
  qu'à node1, node2 et aux bancs Harvester. Détails, provenance des médias
  et pièges : `tests/bench/vmware/README.md`.
- Quelques VMs d'exemple dans vCenter, CBT activé : une Linux, une Windows.
- Outil `tests/bench/vmware/vmwlab.sh` sur le modèle de `harvlab.sh` :
  `verify`, `filter`, `media`, `install`, `vcenter`, `inventory`, `start`,
  `stop`, `status`, `destroy`, secrets dans Vault
  (`secret/infra/vmware-lab`), jamais affichés.
- IP et noms réservés AVANT l'installation dans NetBox et Pi-hole (règle
  d'enregistrement systématique).
- Pas de release de la console pour ce sous-projet (outil de banc et doc).

### Ce que le réel a appris avant B (28/09/2026)

- **Harvester 1.9 ne livre pas Forklift.** L'add-on `forklift-operator` est
  expérimental (dépôt `harvester/sv-addons`, chart `harvester/charts`, images
  de `harvester/forklift-packaging`, Forklift amont 2.9) ; il n'est pas dans
  le dépôt des add-ons expérimentaux où la conception le cherchait.
- Chart `forklift-operator` 1.9.0 publié le 16/09/2026, mais images publiées
  seulement jusqu'à `v1.8.2` sur `registry.rancher.com/harvester` ; les
  valeurs par défaut du chart pointent sur `rancher/nginx:latest` : dépôt,
  image et tag se posent toujours.
- Forklift exige **cert-manager**, absent de Harvester : la console l'a déjà
  dans son paquet Cluster API (manifeste v1.16.2), il sert ici.
- Un `ForkliftController` fait déployer les composants ; le fournisseur de
  destination `host` est créé par Forklift.
- Harvester 1.9 relaie l'inventaire de Forklift
  (`/v1/harvester/providers/vsphere/<id>/{vms,networks,datastores}`), mais
  par l'authentification de Rancher ; la console lira le service
  `forklift-inventory` elle-même, avec un jeton court d'un compte de service
  (le proxy de l'apiserver retire l'en-tête Authorization). La question
  « inventaire de Forklift ou API de vCenter » est tranchée : Forklift, qui
  donne aussi le CBT et ses propres réserves par VM.
- **Image VDDK** : la console n'a ni podman ni buildah ; l'image se construit
  par l'API de registre (base BCI busybox + l'archive VDDK telle quelle comme
  couche) et se pousse dans le registre de l'exploitant, que le cluster doit
  pouvoir tirer (réglage `containerd-registry`).
- **B se découpe en B1 et B2**, chacun avec sa release : B1 = installation,
  image VDDK, fournisseur, inventaire (plan :
  `docs/design/2026-09-28-forklift-b1-plan.md`) ; B2 = correspondances, plans
  à chaud, bascule, retour arrière.

### B. Forklift du côté de Harvester

- Activer l'add-on `forklift-operator` depuis la console, comme les autres
  add-ons (vérifier d'abord sa provenance et sa version pour Harvester 1.9 :
  dépôt des add-ons expérimentaux de Harvester).
- **Image VDDK** : construite à partir de l'archive de l'exploitant, gardée
  par la console, poussée dans un registre que le cluster atteint. Jamais
  dans le livrable (licence VMware).
- Déclarer le vCenter comme fournisseur Forklift (`Provider` de type
  vsphere, `spec.settings.vddkInitImage`), identifiants dans un Secret,
  jamais renvoyés ni journalisés.
- Parité CLI : un outil `bin/harvester-forklift` (fournisseurs,
  correspondances, vagues, bascule, retour arrière), qui émet des
  `STEP_EVENT` ; la console l'appelle et suit chaque geste (dock, Activité).

### C. La console des migrations VMware

- Une vue par cluster de destination : couloirs par vague sur un axe de
  temps commun (maintenant, les bascules planifiées, la fenêtre de
  maintenance).
- **Composer une vague** : choisir des VMs dans l'inventaire vCenter (dossiers,
  réseaux, datastores, taille, OS, CBT actif ou non), le namespace de
  destination, les correspondances réseau (portgroup vers réseau de VMs, par
  VLAN quand il correspond) et stockage (datastore vers classe de stockage).
  Les contrôles de Forklift avant migration sont montrés par VM avant le
  lancement.
- **Suivre** : par VM, la phase (copie initiale et son avancement par
  disque, copies incrémentales, bascule, conversion, création), le nombre de
  copies incrémentales, la durée et la taille de la dernière, la prochaine ;
  par vague, le compte à rebours de la bascule.
- **Basculer** : à la date de la vague, ou tout de suite par un bouton.
- **Revenir à la source** (une VM ou une vague) : la VM est arrêtée sur
  Harvester et la VM d'origine redémarrée par l'API de vCenter.
- **Clore une vague** : la marquer validée ; le retour arrière n'est plus
  proposé ; les VMs sources restent intactes dans vCenter.

## Déroulement d'une vague et ce qui fixe la coupure

1. **Copie initiale**, VM allumée : Forklift prend un instantané de la VM et
   copie ses disques par VDDK dans des volumes Harvester (DataVolumes de CDI,
   classe de stockage de la correspondance).
2. **Copies incrémentales** : à chaque intervalle (réglage de Forklift,
   modifiable depuis la console, par exemple 15 minutes), un nouvel
   instantané, et seuls les blocs changés depuis le précédent sont copiés
   (CBT).
3. **Bascule** (date de la vague ou bouton) : Forklift arrête proprement la
   VM source, fait la dernière copie incrémentale, convertit l'invité
   (virt-v2v : pilotes virtio, retrait des outils VMware), crée la VM sur
   Harvester et la démarre.

**Coupure ≈ dernière copie incrémentale + conversion + démarrage.** La
console l'estime : la durée de la dernière copie incrémentale, plus le temps
de conversion appris sur les VMs déjà migrées (par OS), plus un démarrage.

Sources des données :

- l'état du Plan (pipeline de Forklift par VM : phases, liste des copies
  incrémentales avec leurs dates, avancement par disque) ;
- l'inventaire : service d'inventaire de Forklift ; s'il n'est pas joignable
  par le cluster, l'API REST de vCenter directement. À trancher sur le banc.

## Erreurs

- Une copie incrémentale ratée est retentée par Forklift, et montrée (échecs
  consécutifs).
- Un échec pendant la bascule laisse la source arrêtée mais intacte : la
  console propose « Revenir à la source ».
- Une VM sans CBT ne peut migrer qu'à froid : la console le dit avant le
  lancement (et propose d'activer CBT dans vCenter).
- Les refus de Forklift ou des webhooks de Harvester sont dits en clair,
  comme ailleurs dans la console.

## Tests

- **Unitaires** : les objets Forklift fabriqués depuis une vague
  (Provider, NetworkMap, StorageMap, Plan `warm: true`, Migration avec
  `cutover`), la lecture de l'état d'un Plan en état par VM, l'estimation de
  la coupure.
- **Navigateur** : le tableau des vagues sur un état simulé.
- **Réel, sur le banc** : une VM Linux et une VM Windows migrées à chaud du
  vCenter imbriqué vers harvlab2 :
  - les copies incrémentales rapetissent après des écritures dans la VM ;
  - la coupure mesurée (arrêt VMware à démarrage Harvester) est comparée à
    l'estimation affichée ;
  - un retour arrière vers la source ;
  - une bascule à date planifiée.

## Livraison

Plusieurs releases : A (sans release de la console), puis B, puis C. Chaque
version au CHANGELOG, doc EN et FR, tableau de parité à jour (la ligne
« Imports de VM » y passe à faite).

## Limites dites

- Le cluster de destination doit joindre le vCenter et les hôtes ESXi
  (ports 443 et 902).
- L'image VDDK reste celle de l'exploitant.
- Les images de Forklift doivent être atteignables par le cluster (registre
  ou miroir en airgap).
- Une VM sans CBT migre à froid.
