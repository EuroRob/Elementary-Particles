---
type: Responsibility
state: organized
ep_tier: infrastructure
ep_particle: elementary-particles
belongs_to: "[[elementary-particles]]"
related_to:
  - "[[HOW_TO_ADD_SERVICE]]"
  - "[[TIER_PATTERNS]]"
  - "[[DOCKER_IMAGE_GUIDE]]"
---

# Tag Reference Guide

Complete reference for all service tags and what they affect.

---

## 📊 Tag System Overview

Tags are the **control plane** of your IaC platform. They drive:
- **Traefik** routing decisions
- **Authelia** access policies
- **Pi-hole** DNS registration
- **Homepage** UI rendering
- **NetBox** device classification
- **Monitoring** alert configurations
- **Backup** scheduling

---

## 🏷️ All Available Tags

### EXPOSURE TAGS (Traefik)

#### `expose`
- **Type:** `boolean` (`"true"` or `"false"`)
- **Default:** `"false"`
- **What it does:**
  - `"true"` → Service is routable via Traefik ingress
  - `"false"` → Service is internal-only (no external routing)
- **Triggers:**
  - ✅ Traefik route generation
  - ✅ Ingress controller setup
  - ✅ TLS certificate issuance
  - ✅ Public accessibility
- **Example:**
  ```yaml
  tags:
    expose: "true"  # Jellyfin is accessible from internet
  ```
- **Consequences:**
  - If `expose: true` but no `auth`, warning generated
  - If `expose: true`, `ingress: https` is highly recommended

#### `ingress`
- **Type:** `string` (`"http"`, `"https"`, `"both"`)
- **Default:** Inferred from `expose` tag
- **What it does:**
  - `"https"` → Only HTTPS routes (recommended)
  - `"http"` → Only HTTP routes (not recommended for external)
  - `"both"` → HTTP → HTTPS redirect available
- **Triggers:**
  - ✅ TLS policy enforcement
  - ✅ Redirect rules (HTTP to HTTPS)
  - ✅ Security headers
  - ✅ Certificate setup
- **Example:**
  ```yaml
  tags:
    expose: "true"
    ingress: "https"  # Force HTTPS only
  ```

---

### AUTHENTICATION TAGS (Authelia)

#### `auth`
- **Type:** `string` (`"none"`, `"basic"`, `"oidc"`, `"custom"`)
- **Default:** `"none"`
- **What it does:**
  - `"none"` → No authentication required
  - `"basic"` → HTTP Basic Auth (username:password)
  - `"oidc"` → OIDC via Authelia (SSO)
  - `"custom"` → Custom integration (manual setup)
- **Triggers:**
  - ✅ Authelia middleware attachment
  - ✅ Auth policy generation
  - ✅ Credential management
  - ✅ Session handling
- **Example:**
  ```yaml
  tags:
    expose: "true"
    auth: "oidc"  # SSO login via Authelia
  ```
- **Important:**
  - If `expose: true` and `auth: none`, generates warning
  - Use `"oidc"` for all user-facing apps
  - Use `"none"` for internal APIs/services only

#### `auth_provider`
- **Type:** `string` (`"authelia"`, `"keycloak"`, `"custom"`)
- **Default:** `"authelia"`
- **What it does:**
  - Specifies which authentication system to use
- **Triggers:**
  - ✅ Authelia-specific middleware configuration
  - ✅ Keycloak integration (if specified)
  - ✅ Custom provider setup
- **Example:**
  ```yaml
  tags:
    auth: "oidc"
    auth_provider: "authelia"  # Use Authelia as OIDC provider
  ```

---

### NETWORKING TAGS

#### `network`
- **Type:** `string` (VLAN identifier: `vlan10`, `vlan20`, `vlan30`, `vlan40`, `vlan50`)
- **Default:** `"vlan30"` (recommendation)
- **What it does:**
  - Specifies which VLAN/network the service runs in
  - Affects firewall rules, DNS zone, routing
- **Triggers:**
  - ✅ Terraform network assignment
  - ✅ Firewall rule generation
  - ✅ DNS zone configuration
  - ✅ Network segmentation
- **VLAN Reference:**
  ```
  vlan10: Management network (admin tools)
  vlan20: IoT/Sensors
  vlan30: Servers & Applications (default for most)
  vlan40: Storage & NAS
  vlan50: Lab/Testing/Development
  ```
- **Example:**
  ```yaml
  tags:
    network: "vlan30"  # Place in app/server VLAN
  ```

#### `dns`
- **Type:** `boolean` (`"true"` or `"false"`)
- **Default:** `"false"`
- **What it does:**
  - `"true"` → Register hostname in Pi-hole DNS
  - `"false"` → No DNS registration
- **Triggers:**
  - ✅ Pi-hole API call to add A record
  - ✅ PTR record generation (reverse DNS)
  - ✅ DNS health checks
  - ✅ Service discovery via DNS
- **Example:**
  ```yaml
  tags:
    dns: "true"       # Register jellyfin.internal
  networking:
    dns_names:
      - "jellyfin.internal"
      - "media"
  ```
- **Requires:**
  - `networking.dns_names` must be set
  - Pi-hole must be accessible

---

### TIER & OWNERSHIP TAGS

#### `tier`
- **Type:** `string` (`"platform"`, `"core"`, `"storage"`, `"application"`, `"analytics"`)
- **Default:** Required field
- **What it does:**
  - Classifies service in the technology stack
  - Affects deployment order, prioritization, runbook linkage
- **Triggers:**
  - ✅ Deployment sequence ordering
  - ✅ Dependency resolution
  - ✅ SLA assignment defaults
  - ✅ Dashboard grouping
- **Tier Descriptions:**
  ```
  platform   - Core IaC (Vault, Terraform, Ansible) - rarely deployed
  core       - Foundational (Traefik, Authelia, Pi-hole) - always on
  storage    - Data backends (Postgres, Redis, NAS mount) - critical
  application - User-facing apps (Jellyfin, NextCloud, etc)
  analytics  - Observability & automation (n8n, Ollama, Grafana)
  ```
- **Example:**
  ```yaml
  tier: "application"  # User-facing media service
  ```

#### `owner`
- **Type:** `string` (`"ops"`, `"infra"`, `"apps"`, `"data"`)
- **Default:** Recommended to set
- **What it does:**
  - Specifies team responsible for this service
  - Used in runbooks, escalations, dashboards
- **Triggers:**
  - ✅ Dashboard ownership display
  - ✅ Runbook recommendations
  - ✅ Alert routing
  - ✅ NetBox ownership metadata
- **Example:**
  ```yaml
  tags:
    owner: "ops"  # Operations team owns this
  ```

---

### SLA & OPERATIONS TAGS

#### `sla`
- **Type:** `string` (`"critical"`, `"high"`, `"medium"`, `"low"`)
- **Default:** `"medium"`
- **What it does:**
  - Defines service level agreement / criticality
  - Used for monitoring sensitivity, backup frequency, restart policies
- **Triggers:**
  - ✅ Alert severity levels
  - ✅ Monitoring thresholds (stricter for critical)
  - ✅ Auto-restart behavior
  - ✅ Failover priority
  - ✅ Backup frequency recommendations
- **Effect on Monitoring:**
  ```
  critical → Alert on ANY failure immediately
  high     → Alert after 2 failures in 5 min
  medium   → Alert after 3 failures in 10 min
  low      → Alert after 5 failures in 30 min
  ```
- **Example:**
  ```yaml
  tags:
    sla: "critical"  # Jellyfin is critical to household
  ```

#### `backup`
- **Type:** `string` (`"never"`, `"daily"`, `"weekly"`, `"on-demand"`)
- **Default:** `"never"`
- **What it does:**
  - Specifies backup schedule frequency
  - Triggers backup job creation and retention policy
- **Triggers:**
  - ✅ Backup scheduler setup
  - ✅ Retention policy configuration
  - ✅ Backup job monitoring
  - ✅ Restore procedure generation
- **Backup Implications:**
  ```
  never      → No backups (OK for stateless services)
  daily      → Every 24h (30 day retention default)
  weekly     → Every 7 days (52 week retention default)
  on-demand  → Manual backups only + monitoring
  ```
- **Example:**
  ```yaml
  tags:
    backup: "daily"  # Back up Jellyfin metadata nightly
  backup:
    enabled: true
    schedule: "daily"
    retention: "30d"
  ```
- **Note:**
  - If `backup: daily` but `backup.enabled: false`, warning issued
  - Large volumes may need `weekly` instead of `daily`

---

### UI & DISPLAY TAGS

#### `category`
- **Type:** `string` (predefined list)
- **Options:** `Admin`, `Files`, `Media`, `Automation`, `Development`, `Monitoring`, `Storage`, `Network`, `Other`
- **Default:** `"Other"`
- **What it does:**
  - Organizes services into menu sections in Homepage
  - Used for UI organization and visual grouping
- **Triggers:**
  - ✅ Homepage dashboard menu generation
  - ✅ Service grouping and display
  - ✅ Shortcut creation
- **Example:**
  ```yaml
  tags:
    category: "Media"      # Shows in Media section
    icon: "play"
    color: "#FF5733"
  ```
- **Result in Homepage:**
  ```
  Media
  ├─ Jellyfin → jellyfin.internal
  ├─ Plex → plex.internal
  └─ Immich → immich.internal
  ```

#### `icon`
- **Type:** `string` (icon library reference)
- **Examples:** `cloud`, `folder`, `play`, `cog`, `database`, `shield`, etc.
- **Default:** auto-selected from category
- **What it does:**
  - Visual icon displayed in Homepage next to service
- **Triggers:**
  - ✅ Homepage icon display
  - ✅ Dashboard visual representation
- **Example:**
  ```yaml
  tags:
    icon: "play"  # Displays play icon for media service
  ```

#### `color`
- **Type:** `string` (hex color code)
- **Format:** `#RRGGBB`
- **Default:** from category default
- **What it does:**
  - Brand color for service in UI
- **Triggers:**
  - ✅ Homepage color theming
  - ✅ Dashboard colors
  - ✅ Service card styling
- **Example:**
  ```yaml
  tags:
    color: "#FF5733"  # Orange red for Jellyfin
  ```

---

### DATA & COMPLIANCE TAGS

#### `data_classification`
- **Type:** `string` (`"public"`, `"internal"`, `"confidential"`, `"restricted"`)
- **Default:** `"internal"`
- **What it does:**
  - Indicates sensitivity of data handled by service
  - Affects backup requirements, audit logging, access rules
- **Triggers:**
  - ✅ Encryption policy enforcement
  - ✅ Audit logging levels
  - ✅ Access control strictness
  - ✅ Retention policy application
  - ✅ Backup encryption requirements
- **Security Implications:**
  ```
  public       → No special handling
  internal     → Basic encryption, standard backups
  confidential  → Full encryption, audit logging, MFA required
  restricted   → Maximum security, limited access, forensic logging
  ```
- **Example:**
  ```yaml
  tags:
    data_classification: "internal"  # Photos are internal
  metadata:
    compliance:
      - "GDPR"  # Must handle EU data
  ```

#### `environment`
- **Type:** `string` (`"production"`, `"staging"`, `"development"`)
- **Default:** `"production"`
- **What it does:**
  - Indicates environment type
  - Affects monitoring, alerting, backup frequency
- **Triggers:**
  - ✅ Environment-specific configurations
  - ✅ Alert routing (dev alerts vs prod)
  - ✅ Backup policies (less strict for dev)
  - ✅ Resource limits (lower for dev)
- **Example:**
  ```yaml
  tags:
    environment: "production"  # This is the production instance
  ```

---

## 📋 Complete Tag Matrix

| Tag | Type | Example Value | Primary Effect | Secondary Effects |
|-----|------|---------------|-----------------|-------------------|
| `expose` | bool | `"true"` | Traefik routing | TLS, public access |
| `ingress` | string | `"https"` | Protocol enforcement | Redirects, certs |
| `auth` | string | `"oidc"` | Authelia middleware | User sessions |
| `auth_provider` | string | `"authelia"` | Auth backend selection | Policy config |
| `network` | string | `"vlan30"` | VLAN assignment | Firewall, DNS zone |
| `dns` | bool | `"true"` | Pi-hole registration | Service discovery |
| `tier` | string | `"application"` | Deployment sequence | Dependency order |
| `owner` | string | `"ops"` | Team assignment | Escalation, runbooks |
| `sla` | string | `"critical"` | Alert sensitivity | Restart behavior |
| `backup` | string | `"daily"` | Backup scheduling | Retention policy |
| `category` | string | `"Media"` | UI grouping | Homepage display |
| `icon` | string | `"play"` | UI appearance | Dashboard display |
| `color` | string | `"#FF5733"` | UI theming | Service branding |
| `data_classification` | string | `"internal"` | Security policy | Encryption, audit logging |
| `environment` | string | `"production"` | Environment type | Alert routing, backups |

---

## 🎯 Common Tag Combinations

### Pattern 1: Public Web App (NextCloud)
```yaml
tags:
  expose: "true"
  ingress: "https"
  auth: "oidc"
  auth_provider: "authelia"
  dns: "true"
  tier: "application"
  sla: "critical"
  backup: "daily"
  category: "Files"
  data_classification: "confidential"
```

### Pattern 2: Internal API (Ollama)
```yaml
tags:
  expose: "false"  # Internal only
  auth: "none"     # Trust network access
  network: "vlan30"
  tier: "analytics"
  sla: "medium"
  backup: "weekly"
  category: "Development"
  data_classification: "internal"
```

### Pattern 3: Database (Postgres)
```yaml
tags:
  expose: "false"
  auth: "none"     # Connection string, not HTTP
  network: "vlan40"
  tier: "storage"
  sla: "critical"
  backup: "daily"
  category: "Storage"
  data_classification: "confidential"
```

### Pattern 4: Monitoring Tool (Grafana)
```yaml
tags:
  expose: "true"
  ingress: "https"
  auth: "oidc"
  dns: "true"
  tier: "analytics"
  sla: "high"
  backup: "weekly"
  category: "Monitoring"
  data_classification: "internal"
```

---

## 🔧 Tag Validation Rules

When validating tags:

1. **If `expose: true`, then:**
   - ✅ `ingress` must be specified
   - ✅ `auth` should NOT be `"none"` (warning if it is)
   - ✅ Service must have web interface defined

2. **If `auth: oidc`, then:**
   - ✅ `auth_provider` must be set
   - ✅ `expose` should be `"true"` (why auth if not exposed?)
   - ✅ Authelia must be running

3. **If `dns: true`, then:**
   - ✅ `networking.dns_names` must be non-empty
   - ✅ Pi-hole must be accessible
   - ✅ Service should have valid FQDN

4. **If `sla: critical`, then:**
   - ⚠️ `backup` should be `"daily"` at minimum
   - ⚠️ Multiple replicas recommended
   - ⚠️ Health check must be defined

5. **If `tier: platform`, then:**
   - ✅ Should be deployed before any other tier
   - ✅ Should rarely change after bootstrap

---

## 📊 Tag Effects on Deployment Order

```
Deployment Order (Topologically Sorted):

1. Tier: platform     (Vault, Terraform, Ansible)
   └─ Everything depends on this

2. Tier: core         (Traefik, Authelia, Pi-hole)
   └─ Apps depend on these

3. Tier: storage      (Postgres, Redis, NFS)
   └─ Apps depend on these

4. Tier: application  (Jellyfin, NextCloud, etc)
   └─ May depend on storage & core

5. Tier: analytics    (n8n, Ollama, Grafana)
   └─ May depend on storage or apps
```

Within each tier, services with no dependencies are deployed in parallel.

---

## 🚨 Anti-Patterns (Don't Do This)

❌ This will cause warnings/errors:

```yaml
# ❌ Exposed but no auth
tags:
  expose: "true"
  auth: "none"        # WARNING: unprotected public service!

# ❌ Auth but not exposed
tags:
  expose: "false"
  auth: "oidc"        # Why have auth for internal service?

# ❌ Critical but no backups
tags:
  sla: "critical"
  backup: "never"     # WARNING: critical with no backups!

# ❌ DNS but no hostname
tags:
  dns: "true"
networking:
  dns_names: []       # ERROR: No names to register!
```

---

## 📖 Using Tags in Your Deployments

### In service.yaml:
```yaml
name: myservice
tags:
  expose: "true"
  auth: "oidc"
  # ... more tags
```

### In Ansible (auto-populated from tags):
```yaml
- name: Register {{ service_name }} with Traefik
  when: services[service_name].expose == "true"
  notify: reload traefik
```

### In Terraform (query services by tag):
```terraform
locals {
  critical_services = {
    for name, service in local.all_services :
    name => service
    if service.tags.sla == "critical"
  }
}
```

### In NetBox (custom fields from tags):
```python
service = nb.dcim.devices.create(
  name="myservice",
  site="homelab",
  custom_fields={
    "expose": service['tags']['expose'],
    "auth": service['tags']['auth'],
    "tier": service['tags']['tier'],
  }
)
```

