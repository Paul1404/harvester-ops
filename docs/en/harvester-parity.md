# Parity with the Harvester UI

Status on 2026-09-27, console **v1.67.0**, compared with the **Harvester v1.9** UI (menus taken from the harvester-ui-extension v1.9.0 source and the v1.9 documentation).

Of 135 functions of the Harvester UI: **115 done**, **7 partial**, **13 missing**; 2 out of scope. A missing function shows the version it is planned for.

Statuses: Done, Partial (what is missing is said), Missing (planned version), Console only (what Harvester does not have), Out of scope.

## Dashboard

Harvester menu: *Dashboard*

| Function | Status | Version | Note |
|---|---|---|---|
| Host, VM and volume counts | Done | 1.2 | Overview tiles |
| CPU, memory and storage capacity | Done | 1.62 | live usage (metrics.k8s.io), reserved, storage written and promised |
| Cluster events (hosts, VMs, volumes, images) | Done | 1.62 | Overview Events tab, warnings filter |
| Cluster and VM metrics (rancher-monitoring) | Missing | planned 1.69 |  |
| Upgrade Harvester button | Missing | planned 1.69 |  |

## Hosts

Harvester menu: *Hosts*

| Function | Status | Version | Note |
|---|---|---|---|
| Host list, state, roles | Done | 1.0 |  |
| Maintenance mode (with force) | Done | 1.43 | pre-check: what migrates, what stops |
| Cordon / uncordon | Done | 1.27 |  |
| Edit: display name, console URL, labels | Done | 1.62 | system labels protected |
| Disks: add, remove, host and disk tags | Done | 1.62 | Longhorn V1, V2 or LVM; per-disk scheduling |
| Hugepages | Done | 1.62 |  |
| Ksmtuned (strategy, mode, thresholds) | Done | 1.62 |  |
| Enable / disable CPU manager | Done | 1.62 |  |
| Power (shut down, power on, reboot) | Done | 1.62 | through harvester-seeder in maintenance, as Harvester; also through Redfish in Bare-metal |
| Out-of-band access (seeder) | Done | 1.62 | verified over IPMI (virtualbmc); Redfish on 443 only |
| Delete a host (multi-node cluster) | Done | 1.62 | typed name to confirm |
| Detail: host network, storage, VMs | Partial | 1.39 | Fabric and Storage views |

## Virtual machines: list and actions

Harvester menu: *Virtual Machines*

| Function | Status | Version | Note |
|---|---|---|---|
| List per namespace, state, run strategy | Done | 1.2 |  |
| CPU, memory, IP, node columns; label filter | Done | 1.61 |  |
| Start / stop | Done | 1.2 |  |
| Restart | Done | 1.60 | within the grace period (menu) or hard (console) |
| Soft reboot (guest agent) | Done | 1.60 |  |
| Pause / unpause | Done | 1.60 |  |
| Force stop | Done | 1.60 |  |
| Migrate | Done | 1.60 | to a chosen node or any |
| Abort migration | Done | 1.60 |  |
| Storage migration (volume to another) | Done | 1.61 | refused after KubeVirt's switch (would go back to the old copy) |
| Take backup | Done | 1.58 |  |
| Take snapshot | Done | 1.2 |  |
| Restore (new VM or replace, keep MAC) | Done | 1.11 |  |
| Create a schedule from the VM | Done | 1.61 |  |
| VM snapshot quota | Done | 1.61 |  |
| Edit CPU and memory (hotplug) | Done | 1.61 | memory needs virtio-mem in the guest, and 1 GiB at least |
| Hotplug a volume / detach it | Done | 1.60 |  |
| Hotplug / detach a network interface | Done | 1.61 |  |
| Eject CD-ROM | Done | 1.60 | cold, with its volume deleted, like Harvester's legacy action |
| Insert an image into a CD-ROM | Done | 1.61 | empty SATA drive; hot eject too |
| Generate template | Done | 1.60 | from a VM, with or without the data |
| Clone (with or without data) | Done | 1.60 |  |
| Delete, choosing the volumes | Done | 1.60 | the Cluster view button failed until 1.59 (missing route) |
| Edit / download YAML | Done | 1.60 |  |
| Edit config | Done | 1.8 | server dry-run before applying |
| WebVNC console | Done | 1.7 | shared between several people |
| Serial console | Done | 1.61 |  |
| View logs | Done | 1.61 | virt-launcher pod, operators |
| Bulk actions | Done | 1.60 | start, stop, restart, force stop, migrate |

## Virtual machines: create and settings

Harvester menu: *Create / Edit VM*

| Function | Status | Version | Note |
|---|---|---|---|
| Create, single or multiple instances | Done | 1.28 | up to 50, dry-run |
| From a template and version | Done | 1.29 |  |
| CPU, memory, CPU model, pinning, NUMA | Done | 1.12 |  |
| CPU and memory hotplug ceilings | Done | 1.61 | Harvester's checkbox: one core per socket, limits = maximums |
| Volumes: image, blank, existing, container; boot order | Done | 1.8 |  |
| Network interfaces (model, type, MAC) | Done | 1.8 |  |
| Static IP of an interface (v1.9) | Done | 1.62 | applied by kube-ovn on an overlay network (DHCP to the guest); only shown on a VLAN |
| Node scheduling (selector, rules) | Done | 1.13 |  |
| VM affinity / anti-affinity | Done | 1.13 |  |
| PCI devices | Done | 1.15 |  |
| USB devices | Missing | planned 1.68 |  |
| Access credentials (password, keys through the agent) | Done | 1.61 | applied at the next restart |
| Filesystem volume (virtiofs, v1.9) | Done | 1.62 | at creation; the guest kernel needs virtiofs |
| Labels, instance labels, annotations | Done | 1.62 |  |
| Run strategy | Done | 1.2 |  |
| OS type, reserved memory, maintenance strategy | Done | 1.61 | plus display name, description under Harvester's key |
| Hostname, termination grace period | Done | 1.12 |  |
| Cloud configuration (user data, network data) | Done | 1.60 | lost at creation until 1.59; in a Secret, created or converted on save |
| SSH keys at creation | Done | 1.60 |  |
| Install guest agent | Done | 1.60 |  |
| Windows unattend and sysprep | Done | 1.62 | answer file at creation, sysprep drive |
| TPM, EFI, Secure Boot, USB tablet | Done | 1.12 |  |

## Volumes

Harvester menu: *Volumes*

| Function | Status | Version | Note |
|---|---|---|---|
| List: replicas, health, VM, written size | Done | 1.40 |  |
| Create (blank or from an image) | Done | 1.59 |  |
| Expand | Done | 1.59 |  |
| Delete | Done | 1.63 | any volume no VM uses |
| Clone (with or without data) | Done | 1.63 |  |
| Export image | Done | 1.63 |  |
| Take snapshot | Done | 1.63 |  |
| Cancel expand | Done | 1.63 | verified on a frozen expansion (bench) |
| Data migration (to another class) | Done | 1.63 | a copy through CDI, the original stays, as in Harvester |
| Edit / download YAML | Done | 1.60 |  |
| Degraded volume diagnosis and fixes | Console only | 1.42 | console only |

## Images

Harvester menu: *Images*

| Function | Status | Version | Note |
|---|---|---|---|
| List: state, size, class, used by | Done | 1.57 |  |
| Create from a URL | Done | 1.59 |  |
| Upload a file from the browser | Done | 1.63 | the console serves it to the cluster (port 8092) |
| SHA512 checksum | Done | 1.63 |  |
| Encrypt / decrypt | Done | 1.63 |  |
| Download the image | Partial | 1.63 | Longhorn v1 images; CDI images: no third-party storage on the test benches to try them |
| Clone, edit (description, labels) | Done | 1.63 | the name is fixed by Harvester |
| Create a VM from the image | Done | 1.63 |  |
| Delete (when unused) | Done | 1.59 |  |
| Edit / download YAML | Done | 1.60 |  |

## Namespaces

Harvester menu: *Namespaces*

| Function | Status | Version | Note |
|---|---|---|---|
| List | Done | 1.62 | Namespaces window: VMs, volumes, quota; system hidden |
| Create / edit / delete | Done | 1.62 | delete by typed name |
| Namespace snapshot quota | Done | 1.62 |  |

## Networks

Harvester menu: *Networks*

| Function | Status | Version | Note |
|---|---|---|---|
| Cluster networks and configs (NICs, bond, MTU, target hosts, state per host) | Done | 1.65 |  |
| Migrate a network config to another cluster network | Done | 1.65 |  |
| VM networks (VLAN, untagged, trunk: create, edit, delete) | Done | 1.65 |  |
| Overlay network (kube-ovn) | Done | 1.49 |  |
| Trunk mode (VLAN ranges), route and DHCP server | Done | 1.65 |  |
| Load balancers | Done | 1.65 |  |
| IP pools | Done | 1.65 |  |
| Host networks (HostNetworkConfig) | Done | 1.65 |  |
| Storage, migration and RWX networks | Done | 1.65 |  |
| VM network path to the switch (LLDP) | Console only | 1.36 | console only |

## Overlay and underlay networks (kube-ovn)

Harvester menu: *Overlay / Underlay Networks*

| Function | Status | Version | Note |
|---|---|---|---|
| VPC: create, static routes, peerings | Done | 1.49 |  |
| Subnets (CIDR, gateway, NAT outgoing, DHCP, ACL) | Done | 1.49 |  |
| Network policies (VM isolation) | Done | 1.66 |  |
| NAT gateways, external IPs, SNAT / DNAT rules | Done | 1.66 |  |
| Underlay: provider networks, VLANs, external networks | Done | 1.66 |  |
| kube-ovn health said before any change; repair of a NAT gateway (kube-ovn before 1.16.1) | Console only | 1.66 | console only |

## Backup and snapshots

Harvester menu: *Backup & Snapshots*

| Function | Status | Version | Note |
|---|---|---|---|
| Schedules (create, suspend, resume, delete) | Partial | 1.58 | no edit yet |
| Backups: restore new or replace existing | Done | 1.58 |  |
| Replace deleting previous volumes; file system freeze deadline | Missing | planned 1.68 |  |
| VM snapshots: restore, delete | Done | 1.58 |  |
| Volume snapshots: restore, delete | Done | 1.58 |  |
| Backup target (NFS, S3) | Done | 1.67 | NFS or S3 form, connection test, removal |
| Backup and schedule YAML | Done | 1.60 |  |

## Monitoring and logging

Harvester menu: *Monitoring & Logging*

| Function | Status | Version | Note |
|---|---|---|---|
| Enable rancher-monitoring / rancher-logging and their configuration | Done | 1.57 | through add-ons |
| Alertmanager configurations (receivers) | Missing | planned 1.69 |  |
| Flows, cluster flows, outputs, cluster outputs | Missing | planned 1.69 |  |

## Advanced

Harvester menu: *Advanced*

| Function | Status | Version | Note |
|---|---|---|---|
| Templates: versions, default, launch a version, delete | Done | 1.64 |  |
| SSH keys (create, read from file, edit, delete) | Done | 1.64 |  |
| Cloud configuration templates (user / network data) | Done | 1.64 |  |
| Storage classes (Longhorn v1, encryption, topologies, default, delete) | Partial | 1.64 | LVM and Longhorn v2 offered but not verified for real (absent from the benches) |
| PCI devices | Partial | 1.15 | list and attach to VMs; enable passthrough in 1.68 |
| SR-IOV network devices (VF count) | Missing | planned 1.68 |  |
| SR-IOV GPU, vGPU, MIG configurations | Missing | planned 1.68 | to be documented as unverified: no compatible GPU under Harvester |
| USB devices (passthrough) | Missing | planned 1.68 |  |
| Add-ons: enable, disable, configure | Done | 1.57 |  |
| Secrets (Opaque, TLS, Basic, Registry, SSH, encryption: create, new values, delete) | Done | 1.64 |  |
| Harvester settings (all 40: NTP, proxy, CA, TLS, overcommit…) | Done | 1.67 | typed forms, checked beforehand as the webhook would, applied state, masked secrets, warning on settings that can cut an access |

## Harvester upgrade

Harvester menu: *Upgrade*

| Function | Status | Version | Note |
|---|---|---|---|
| Upgrade (version, notes, per-node progress, logs) | Missing | planned 1.69 |  |
| Air-gapped upgrade (uploaded image) | Missing | planned 1.69 |  |

## Support

Harvester menu: *Support*

| Function | Status | Version | Note |
|---|---|---|---|
| Support bundle | Done | 1.67 | Harvester's (create, follow, download, delete) and the console's own (anonymised) |
| Download the cluster kubeconfig | Done | 1.67 | safer than Harvester's: chosen role and namespace, expiring token, revocation, file handed over once |
| Access embedded Rancher and Longhorn UIs | Out of scope |  | out of scope: the console covers these views |

## Users and access

Harvester menu: *Authentication / Rancher*

| Function | Status | Version | Note |
|---|---|---|---|
| Sign-in through Rancher (its identity providers), the person's own rights | Done | 1.50 | as Harvester imported into Rancher (Virtualization Management): calls carry the person's token, Rancher's cluster and project rights apply |
| Virtualization roles (Harvester RBAC chart, Rancher 2.14.1, experimental) | Partial | 1.50 | applied by the Rancher token; checked with a cluster member, not yet with this chart's roles |
| Rancher projects: namespaces grouped by project, resource quotas | Missing | planned 1.69 |  |
| Cluster members | Partial | 1.31 | cluster accounts: enable, disable, admin; Rancher roles not yet |
| Mandatory sign-in, the console's own accounts and roles | Console only | 1.57 | without Rancher; standalone Harvester has a single admin |

## Menus brought by add-ons

Harvester menu: *VM Imports / VM Migration*

| Function | Status | Version | Note |
|---|---|---|---|
| VM imports (VMware, OpenStack, OVA) | Missing | planned 1.69 |  |
| Migration through forklift-operator | Out of scope |  | add-on missing from the 1.9 docs |

## What the console adds

| Function | Status | Version | Note |
|---|---|---|---|
| Several clusters in one interface, without Rancher | Console only | 1.1 | Rancher (Virtualization Management) also gathers several Harvester clusters; the console does it on its own and adds actions between clusters |
| Graceful shutdown and startup of a whole cluster | Console only | 1.0 | Harvester documents it as a manual procedure |
| VM transfer between clusters, export / import | Console only | 1.45 |  |
| Terraform: declarations kept by the console | Console only | 1.54 |  |
| RKE2 clusters through Cluster API, services through CAAPH | Console only | 1.48 | Rancher also creates them, with its Harvester node driver; the console does it without Rancher |
| Bare-metal Harvester install over Redfish | Console only | 1.19 |  |
| Activity: every action tracked, action dock | Console only | 1.6 |  |
| Collaborative notes on VMs and nodes | Console only | 1.4 |  |
| Real Longhorn allocatable per class | Console only | 1.29 |  |
| Five languages | Console only | 1.10 | the Harvester extension is in English |

