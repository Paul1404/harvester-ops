# Parité harvester-ops / interface de Harvester v1.9 : source unique du tableau
# (Artifact + docs/en/harvester-parity.md + docs/fr/parite-harvester.md).
# Statuts : ok (fait), part (partiel), todo (manquant), plus (console seulement), na (hors périmètre).
# v : version où c'est arrivé (ok/part) ou prévue (todo).

AS_OF = "1.62.0"
DATE = "2026-09-27"

S = []  # sections


def sec(key, fr, en, harvester_fr, harvester_en):
    S.append({"key": key, "fr": fr, "en": en, "hfr": harvester_fr, "hen": harvester_en, "rows": []})


def r(status, v, fr, en, nfr="", nen=""):
    S[-1]["rows"].append({"s": status, "v": v, "fr": fr, "en": en, "nfr": nfr, "nen": nen})


sec("dashboard", "Tableau de bord", "Dashboard", "Dashboard", "Dashboard")
r("ok", "1.2", "Compteurs nœuds, VMs, volumes", "Host, VM and volume counts", "Vue d'ensemble", "Overview tiles")
r("ok", "1.62", "Capacité CPU, mémoire, stockage", "CPU, memory and storage capacity", "usage réel (metrics.k8s.io), réservé, stockage écrit et promis", "live usage (metrics.k8s.io), reserved, storage written and promised")
r("ok", "1.62", "Événements du cluster (hôtes, VMs, volumes, images)", "Cluster events (hosts, VMs, volumes, images)", "onglet Événements de l'aperçu, filtre avertissements", "Overview Events tab, warnings filter")
r("todo", "1.66", "Métriques du cluster et des VMs (rancher-monitoring)", "Cluster and VM metrics (rancher-monitoring)", "", "")
r("todo", "1.66", "Bouton Mettre à jour Harvester", "Upgrade Harvester button", "", "")

sec("hosts", "Hôtes", "Hosts", "Hosts", "Hosts")
r("ok", "1.0", "Liste des hôtes, état, rôles", "Host list, state, roles")
r("ok", "1.43", "Mode maintenance (avec forçage)", "Maintenance mode (with force)", "pré-contrôle : ce qui migre, ce qui s'arrête", "pre-check: what migrates, what stops")
r("ok", "1.27", "Cordon / uncordon", "Cordon / uncordon")
r("ok", "1.62", "Modifier : nom affiché, URL de console, labels", "Edit: display name, console URL, labels", "labels système protégés", "system labels protected")
r("ok", "1.62", "Disques : ajouter, retirer, étiquettes (host/disk tags)", "Disks: add, remove, host and disk tags", "Longhorn V1, V2 ou LVM ; planification par disque", "Longhorn V1, V2 or LVM; per-disk scheduling")
r("ok", "1.62", "Hugepages", "Hugepages")
r("ok", "1.62", "Ksmtuned (stratégie, mode, seuils)", "Ksmtuned (strategy, mode, thresholds)")
r("ok", "1.62", "Activer / désactiver le CPU manager", "Enable / disable CPU manager")
r("ok", "1.62", "Alimentation (éteindre, allumer, redémarrer)", "Power (shut down, power on, reboot)", "par harvester-seeder en maintenance, comme Harvester ; aussi par Redfish dans Bare-metal", "through harvester-seeder in maintenance, as Harvester; also through Redfish in Bare-metal")
r("ok", "1.62", "Accès hors bande (seeder)", "Out-of-band access (seeder)", "vérifié par IPMI (virtualbmc) ; Redfish sur 443 seulement", "verified over IPMI (virtualbmc); Redfish on 443 only")
r("ok", "1.62", "Supprimer un hôte (cluster à plusieurs nœuds)", "Delete a host (multi-node cluster)", "nom tapé pour confirmer", "typed name to confirm")
r("part", "1.39", "Détail : réseau, stockage, VMs de l'hôte", "Detail: host network, storage, VMs", "vues Fabrique et Stockage", "Fabric and Storage views")

sec("vms", "Machines virtuelles : liste et actions", "Virtual machines: list and actions", "Virtual Machines", "Virtual Machines")
r("ok", "1.2", "Liste par namespace, état, stratégie", "List per namespace, state, run strategy")
r("ok", "1.61", "Colonnes CPU, mémoire, IP, nœud ; filtre par labels", "CPU, memory, IP, node columns; label filter")
r("ok", "1.2", "Démarrer / arrêter", "Start / stop")
r("ok", "1.60", "Redémarrer", "Restart", "dans le délai de grâce (menu) ou brutal (console)", "within the grace period (menu) or hard (console)")
r("ok", "1.60", "Redémarrage doux (agent invité)", "Soft reboot (guest agent)")
r("ok", "1.60", "Pause / reprise", "Pause / unpause")
r("ok", "1.60", "Arrêt forcé", "Force stop")
r("ok", "1.60", "Migrer", "Migrate", "vers un nœud choisi ou n'importe lequel", "to a chosen node or any")
r("ok", "1.60", "Abandonner une migration", "Abort migration")
r("ok", "1.61", "Migration du stockage (volume vers un autre)", "Storage migration (volume to another)", "refusée après la bascule de KubeVirt (reviendrait à l'ancienne copie)", "refused after KubeVirt's switch (would go back to the old copy)")
r("ok", "1.58", "Prendre une sauvegarde", "Take backup")
r("ok", "1.2", "Prendre un instantané", "Take snapshot")
r("ok", "1.11", "Restaurer (nouvelle VM ou remplacement, MAC gardée)", "Restore (new VM or replace, keep MAC)")
r("ok", "1.61", "Créer une planification depuis la VM", "Create a schedule from the VM")
r("ok", "1.61", "Quota d'instantanés de la VM", "VM snapshot quota")
r("ok", "1.61", "Modifier CPU et mémoire à chaud", "Edit CPU and memory (hotplug)", "la mémoire exige virtio-mem dans l'invité, et 1 Gio au moins", "memory needs virtio-mem in the guest, and 1 GiB at least")
r("ok", "1.60", "Ajouter un volume à chaud / le détacher", "Hotplug a volume / detach it")
r("ok", "1.61", "Brancher / débrancher une carte réseau à chaud", "Hotplug / detach a network interface")
r("ok", "1.60", "Éjecter un CD-ROM", "Eject CD-ROM", "à froid, avec la suppression de son volume, comme l'action historique de Harvester", "cold, with its volume deleted, like Harvester's legacy action")
r("ok", "1.61", "Insérer une image dans un CD-ROM", "Insert an image into a CD-ROM", "lecteur SATA vide ; éjection à chaud aussi", "empty SATA drive; hot eject too")
r("ok", "1.60", "Générer un template", "Generate template", "depuis une VM, avec ou sans les données", "from a VM, with or without the data")
r("ok", "1.60", "Cloner (avec ou sans les données)", "Clone (with or without data)")
r("ok", "1.60", "Supprimer, en choisissant les volumes", "Delete, choosing the volumes", "le bouton de la vue Cluster échouait jusqu'à 1.59 (route absente)", "the Cluster view button failed until 1.59 (missing route)")
r("ok", "1.60", "Modifier / télécharger le YAML", "Edit / download YAML")
r("ok", "1.8", "Modifier la configuration", "Edit config", "essai à blanc côté serveur avant d'appliquer", "server dry-run before applying")
r("ok", "1.7", "Console VNC", "WebVNC console", "partagée entre plusieurs personnes", "shared between several people")
r("ok", "1.61", "Console série", "Serial console")
r("ok", "1.61", "Journaux de la VM", "View logs", "pod virt-launcher, opérateurs", "virt-launcher pod, operators")
r("ok", "1.60", "Actions groupées", "Bulk actions", "démarrer, arrêter, redémarrer, arrêt forcé, migrer", "start, stop, restart, force stop, migrate")

sec("vmform", "Machines virtuelles : création et réglages", "Virtual machines: create and settings", "Create / Edit VM", "Create / Edit VM")
r("ok", "1.28", "Création, une ou plusieurs instances", "Create, single or multiple instances", "jusqu'à 50, essai à blanc", "up to 50, dry-run")
r("ok", "1.29", "Depuis un template et sa version", "From a template and version")
r("ok", "1.12", "CPU, mémoire, modèle de CPU, épinglage, NUMA", "CPU, memory, CPU model, pinning, NUMA")
r("ok", "1.61", "Plafonds du branchement à chaud CPU / mémoire", "CPU and memory hotplug ceilings", "case de Harvester : un cœur par socket, limites = maximums", "Harvester's checkbox: one core per socket, limits = maximums")
r("ok", "1.8", "Volumes : image, vide, existant, conteneur ; ordre de boot", "Volumes: image, blank, existing, container; boot order")
r("ok", "1.8", "Cartes réseau (modèle, type, MAC)", "Network interfaces (model, type, MAC)")
r("ok", "1.62", "IP statique d'une carte (v1.9)", "Static IP of an interface (v1.9)", "appliquée par kube-ovn sur un réseau overlay (DHCP vers l'invité) ; seulement montrée sur un VLAN", "applied by kube-ovn on an overlay network (DHCP to the guest); only shown on a VLAN")
r("ok", "1.13", "Placement sur les nœuds (sélecteur, règles)", "Node scheduling (selector, rules)")
r("ok", "1.13", "Affinité / anti-affinité entre VMs", "VM affinity / anti-affinity")
r("ok", "1.15", "Périphériques PCI", "PCI devices")
r("todo", "1.65", "Périphériques USB", "USB devices")
r("ok", "1.61", "Access credentials (mot de passe, clés par l'agent)", "Access credentials (password, keys through the agent)", "appliqués au prochain redémarrage", "applied at the next restart")
r("ok", "1.62", "Volume de système de fichiers (virtiofs, v1.9)", "Filesystem volume (virtiofs, v1.9)", "à la création ; le noyau invité doit connaître virtiofs", "at creation; the guest kernel needs virtiofs")
r("ok", "1.62", "Labels, labels d'instance, annotations", "Labels, instance labels, annotations")
r("ok", "1.2", "Stratégie d'exécution", "Run strategy")
r("ok", "1.61", "Type d'OS, mémoire réservée, stratégie de maintenance", "OS type, reserved memory, maintenance strategy", "et nom affiché, description à la clé de Harvester", "plus display name, description under Harvester's key")
r("ok", "1.12", "Nom d'hôte, délai d'arrêt", "Hostname, termination grace period")
r("ok", "1.60", "Cloud-init (user-data, network-data)", "Cloud configuration (user data, network data)", "perdu à la création jusqu'à 1.59 ; en Secret, créé ou converti à l'enregistrement", "lost at creation until 1.59; in a Secret, created or converted on save")
r("ok", "1.60", "Clés SSH à la création", "SSH keys at creation")
r("ok", "1.60", "Installer l'agent invité", "Install guest agent")
r("ok", "1.62", "Windows : unattend et sysprep", "Windows unattend and sysprep", "fichier de réponses à la création, lecteur sysprep", "answer file at creation, sysprep drive")
r("ok", "1.12", "TPM, EFI, Secure Boot, tablette USB", "TPM, EFI, Secure Boot, USB tablet")

sec("volumes", "Volumes", "Volumes", "Volumes", "Volumes")
r("ok", "1.40", "Liste : répliques, santé, VM, place écrite", "List: replicas, health, VM, written size")
r("ok", "1.59", "Créer (vide ou depuis une image)", "Create (blank or from an image)")
r("ok", "1.59", "Agrandir", "Expand")
r("part", "1.16", "Supprimer", "Delete", "volumes orphelins seulement", "orphan volumes only")
r("todo", "1.63", "Cloner (avec ou sans les données)", "Clone (with or without data)")
r("todo", "1.63", "Exporter en image", "Export image")
r("todo", "1.2", "Prendre un instantané", "Take snapshot")
r("todo", "1.63", "Annuler un agrandissement", "Cancel expand")
r("todo", "1.63", "Migration de données (vers une autre classe)", "Data migration (to another class)")
r("ok", "1.60", "Modifier / télécharger le YAML", "Edit / download YAML")
r("plus", "1.42", "Diagnostic des volumes dégradés et corrections", "Degraded volume diagnosis and fixes", "propre à la console", "console only")

sec("images", "Images", "Images", "Images", "Images")
r("ok", "1.57", "Liste : état, taille, classe, qui s'en sert", "List: state, size, class, used by")
r("ok", "1.59", "Créer depuis une URL", "Create from a URL")
r("todo", "1.63", "Envoyer un fichier depuis le navigateur", "Upload a file from the browser")
r("todo", "1.63", "Somme de contrôle SHA512", "SHA512 checksum")
r("todo", "1.63", "Chiffrer / déchiffrer", "Encrypt / decrypt")
r("todo", "1.63", "Télécharger l'image", "Download the image")
r("todo", "1.63", "Cloner, modifier (nom, description, labels)", "Clone, edit (name, description, labels)")
r("todo", "1.63", "Créer une VM depuis l'image", "Create a VM from the image")
r("ok", "1.59", "Supprimer (si rien ne s'en sert)", "Delete (when unused)")
r("ok", "1.60", "Modifier / télécharger le YAML", "Edit / download YAML")

sec("namespaces", "Namespaces", "Namespaces", "Namespaces", "Namespaces")
r("ok", "1.62", "Liste", "List", "fenêtre Namespaces : VMs, volumes, quota ; système caché", "Namespaces window: VMs, volumes, quota; system hidden")
r("ok", "1.62", "Créer / modifier / supprimer", "Create / edit / delete", "suppression par nom tapé", "delete by typed name")
r("ok", "1.62", "Quota d'instantanés du namespace", "Namespace snapshot quota")

sec("networks", "Réseaux", "Networks", "Networks", "Networks")
r("part", "1.39", "Réseaux de cluster et configurations (liaisons, bond, MTU)", "Cluster networks and configs (uplinks, bond, MTU)", "vue Fabrique en lecture ; création en 1.64", "read-only Fabric view; creation in 1.64")
r("todo", "1.64", "Déplacer une configuration vers un autre réseau de cluster", "Migrate a network config to another cluster network")
r("part", "1.59", "Réseaux de VMs (VLAN, sans étiquette)", "VM networks (VLAN, untagged)", "créer, supprimer ; pas encore modifier ni le mode trunk", "create, delete; no edit or trunk mode yet")
r("ok", "1.49", "Réseau overlay (kube-ovn)", "Overlay network (kube-ovn)")
r("todo", "1.64", "Mode trunk (plages de VLAN), serveur DHCP", "Trunk mode (VLAN ranges), DHCP server")
r("todo", "1.64", "Load balancers", "Load balancers")
r("todo", "1.64", "IP pools", "IP pools")
r("todo", "1.64", "Réseaux d'hôte (HostNetworkConfig)", "Host networks (HostNetworkConfig)")
r("todo", "1.64", "Réseau de stockage, de migration, RWX", "Storage, migration and RWX networks")
r("plus", "1.36", "Chemin réseau d'une VM jusqu'au switch (LLDP)", "VM network path to the switch (LLDP)", "propre à la console", "console only")

sec("kubeovn", "Réseaux overlay et underlay (kube-ovn)", "Overlay and underlay networks (kube-ovn)", "Overlay / Underlay Networks", "Overlay / Underlay Networks")
r("ok", "1.49", "VPC : créer, routes statiques, peerings", "VPC: create, static routes, peerings")
r("ok", "1.49", "Sous-réseaux (CIDR, passerelle, NAT sortant, DHCP, ACL)", "Subnets (CIDR, gateway, NAT outgoing, DHCP, ACL)")
r("todo", "1.64", "Politiques réseau (isolation des VMs)", "Network policies (VM isolation)")
r("todo", "1.64", "Passerelles NAT, IP externes, règles SNAT / DNAT", "NAT gateways, external IPs, SNAT / DNAT rules")
r("todo", "1.64", "Underlay : réseaux fournisseurs, VLANs", "Underlay: provider networks, VLANs")

sec("backups", "Sauvegardes et instantanés", "Backup and snapshots", "Backup & Snapshots", "Backup & Snapshots")
r("part", "1.58", "Planifications (créer, suspendre, reprendre, supprimer)", "Schedules (create, suspend, resume, delete)", "pas encore modifier", "no edit yet")
r("ok", "1.58", "Sauvegardes : restaurer en nouvelle VM ou remplacer", "Backups: restore new or replace existing")
r("todo", "1.65", "Remplacer en supprimant les anciens volumes ; délai de gel du système de fichiers", "Replace deleting previous volumes; file system freeze deadline")
r("ok", "1.58", "Instantanés de VM : restaurer, supprimer", "VM snapshots: restore, delete")
r("ok", "1.58", "Instantanés de volume : restaurer, supprimer", "Volume snapshots: restore, delete")
r("part", "1.58", "Cible de sauvegarde (NFS, S3)", "Backup target (NFS, S3)", "affichée ; modifiable en 1.65", "shown; editable in 1.65")
r("ok", "1.60", "YAML des sauvegardes et planifications", "Backup and schedule YAML")

sec("monitoring", "Monitoring et logging", "Monitoring and logging", "Monitoring & Logging", "Monitoring & Logging")
r("ok", "1.57", "Activer rancher-monitoring / rancher-logging et leur configuration", "Enable rancher-monitoring / rancher-logging and their configuration", "par les add-ons", "through add-ons")
r("todo", "1.66", "Configurations Alertmanager (récepteurs)", "Alertmanager configurations (receivers)")
r("todo", "1.66", "Flows, cluster flows, outputs, cluster outputs", "Flows, cluster flows, outputs, cluster outputs")

sec("advanced", "Avancé", "Advanced", "Advanced", "Advanced")
r("part", "1.28", "Templates : créer, lancer une VM", "Templates: create, launch a VM", "versions, version par défaut, suppression en 1.63", "versions, default version, delete in 1.63")
r("part", "1.59", "Clés SSH (créer, lire depuis un fichier, supprimer)", "SSH keys (create, read from file, delete)", "pas encore modifier", "no edit yet")
r("todo", "1.63", "Modèles de configuration cloud (user / network data)", "Cloud configuration templates (user / network data)")
r("part", "1.59", "Classes de stockage (Longhorn v1, par défaut, supprimer)", "Storage classes (Longhorn v1, default, delete)", "chiffrement, LVM, v2 et topologies en 1.63", "encryption, LVM, v2 and topologies in 1.63")
r("part", "1.15", "Périphériques PCI", "PCI devices", "liste et attache aux VMs ; activer le passthrough en 1.65", "list and attach to VMs; enable passthrough in 1.65")
r("todo", "1.65", "SR-IOV réseau (nombre de VF)", "SR-IOV network devices (VF count)")
r("todo", "1.65", "GPU SR-IOV, vGPU, configurations MIG", "SR-IOV GPU, vGPU, MIG configurations", "à documenter comme non vérifié : aucun GPU compatible sous Harvester", "to be documented as unverified: no compatible GPU under Harvester")
r("todo", "1.65", "Périphériques USB (passthrough)", "USB devices (passthrough)")
r("ok", "1.57", "Add-ons : activer, désactiver, configurer", "Add-ons: enable, disable, configure")
r("part", "1.59", "Secrets (Opaque : créer, supprimer)", "Secrets (Opaque: create, delete)", "types TLS, Basic, Registry, SSH et modification en 1.63", "TLS, Basic, Registry, SSH types and edit in 1.63")
r("todo", "1.65", "Réglages de Harvester (les 40 : NTP, proxy, CA, TLS, overcommit…)", "Harvester settings (all 40: NTP, proxy, CA, TLS, overcommit…)")

sec("upgrade", "Mise à jour de Harvester", "Harvester upgrade", "Upgrade", "Upgrade")
r("todo", "1.66", "Mettre à jour (version, notes, suivi par nœud, journaux)", "Upgrade (version, notes, per-node progress, logs)")
r("todo", "1.66", "Mise à jour airgap (image envoyée)", "Air-gapped upgrade (uploaded image)")

sec("support", "Support", "Support", "Support", "Support")
r("part", "1.1", "Bundle de support", "Support bundle", "celui de la console (anonymisé) ; celui de Harvester en 1.65", "the console's own (anonymised); Harvester's in 1.65")
r("todo", "1.65", "Télécharger le kubeconfig du cluster", "Download the cluster kubeconfig")
r("na", "", "Accès aux interfaces Rancher et Longhorn embarquées", "Access embedded Rancher and Longhorn UIs", "hors périmètre : la console couvre ces vues", "out of scope: the console covers these views")

sec("users", "Utilisateurs et accès", "Users and access", "Authentication / Rancher", "Authentication / Rancher")
r("ok", "1.50", "Connexion par Rancher (ses fournisseurs d'identité), droits de la personne",
  "Sign-in through Rancher (its identity providers), the person's own rights",
  "comme Harvester importé dans Rancher (Virtualization Management) : les appels partent avec le jeton de la personne, les droits Rancher du cluster et du projet s'appliquent",
  "as Harvester imported into Rancher (Virtualization Management): calls carry the person's token, Rancher's cluster and project rights apply")
r("part", "1.50", "Rôles de virtualisation (chart Harvester RBAC, Rancher 2.14.1, expérimental)",
  "Virtualization roles (Harvester RBAC chart, Rancher 2.14.1, experimental)",
  "appliqués d'office par le jeton Rancher ; vérifié avec un membre du cluster, pas encore avec les rôles de ce chart",
  "applied by the Rancher token; checked with a cluster member, not yet with this chart's roles")
r("todo", "1.66", "Projets Rancher : namespaces rangés par projet, quotas de ressources",
  "Rancher projects: namespaces grouped by project, resource quotas")
r("part", "1.31", "Membres du cluster", "Cluster members", "comptes du cluster : activer, désactiver, admin ; pas encore les rôles Rancher", "cluster accounts: enable, disable, admin; Rancher roles not yet")
r("plus", "1.57", "Connexion obligatoire, comptes et rôles propres à la console",
  "Mandatory sign-in, the console's own accounts and roles",
  "sans Rancher ; Harvester seul n'a qu'un compte admin", "without Rancher; standalone Harvester has a single admin")

sec("addonmenus", "Menus apportés par des add-ons", "Menus brought by add-ons", "VM Imports / VM Migration", "VM Imports / VM Migration")
r("todo", "1.66", "Imports de VM (VMware, OpenStack, OVA)", "VM imports (VMware, OpenStack, OVA)")
r("na", "", "Migration par forklift-operator", "Migration through forklift-operator", "add-on absent de la documentation 1.9", "add-on missing from the 1.9 docs")

sec("console", "Ce que la console ajoute", "What the console adds", "", "")
r("plus", "1.1", "Plusieurs clusters dans une seule interface, sans Rancher",
  "Several clusters in one interface, without Rancher",
  "Rancher (Virtualization Management) réunit aussi plusieurs clusters Harvester ; la console le fait seule, et ajoute les gestes d'un cluster à l'autre",
  "Rancher (Virtualization Management) also gathers several Harvester clusters; the console does it on its own and adds actions between clusters")
r("plus", "1.0", "Arrêt et démarrage gracieux d'un cluster entier", "Graceful shutdown and startup of a whole cluster",
  "Harvester le décrit comme une procédure manuelle", "Harvester documents it as a manual procedure")
r("plus", "1.45", "Transfert de VM entre clusters, export / import", "VM transfer between clusters, export / import")
r("plus", "1.54", "Terraform : déclarations gardées par la console", "Terraform: declarations kept by the console")
r("plus", "1.48", "Clusters RKE2 par Cluster API, services par CAAPH", "RKE2 clusters through Cluster API, services through CAAPH",
  "Rancher les crée aussi, par son pilote de nœud Harvester ; la console le fait sans Rancher",
  "Rancher also creates them, with its Harvester node driver; the console does it without Rancher")
r("plus", "1.19", "Installation bare-metal de Harvester par Redfish", "Bare-metal Harvester install over Redfish")
r("plus", "1.6", "Activité : chaque geste suivi, dock des actions", "Activity: every action tracked, action dock")
r("plus", "1.4", "Notes collaboratives sur les VMs et nœuds", "Collaborative notes on VMs and nodes")
r("plus", "1.29", "Allouable Longhorn réel par classe", "Real Longhorn allocatable per class")
r("plus", "1.10", "Cinq langues", "Five languages", "l'extension Harvester est en anglais", "the Harvester extension is in English")
