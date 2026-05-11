# Guide de reprise autonome — Lakehouse Azure Databricks (DLT + DAB + CI/CD)

Ce document résume **étape par étape** ce qu’il faut refaire sur un **nouveau PC** (ou un nouvel environnement) pour retrouver un socle équivalent à celui construit ensemble : **Azure**, **Unity Catalog**, **Delta Live Tables**, **Databricks Asset Bundles**, **GitHub Actions**, **tags de release**.

Remplace les placeholders (`<...>`) par tes valeurs réelles. Ne commite **jamais** de secrets (mots de passe, client secret) dans Git.

---

## Prérequis sur le nouveau PC

- Compte **Microsoft Azure** avec une subscription
- Compte **GitHub** (accès admin au repo si tu configures CI/CD + environments)
- Navigateur pour Azure Portal et Databricks

### Outils à installer

1. **Git** : [https://git-scm.com](https://git-scm.com)
2. **Python 3.11** (recommandé pour ce projet) : [https://www.python.org](https://www.python.org)
3. **Databricks CLI** (v0.299+ ou récent) :
   - `winget install Databricks.DatabricksCLI`  
   - ou suivre la doc officielle Databricks CLI
4. **uv** (build wheel du bundle) :
   - `winget install astral-sh.uv`  
   - ou `py -m pip install uv`
5. (Optionnel) **Azure CLI** si tu préfères des commandes Azure en terminal : [https://learn.microsoft.com/cli/azure](https://learn.microsoft.com/cli/azure)

Vérifications :

```powershell
git --version
python --version
databricks version
uv --version
```

---

## Partie A — Azure (identité, stockage, coffre à secrets)

### A.1 Noter les identifiants Azure

Depuis le portail Azure, note :

| Information | Exemple de placeholder |
|-------------|-------------------------|
| Tenant ID (Entra ID) | `<TENANT_ID>` |
| Subscription ID | `<SUBSCRIPTION_ID>` |
| Région | `francecentral` |
| Resource group principal | `<RG_NAME>` |
| Compte Storage ADLS Gen2 | `<STORAGE_ACCOUNT>` |
| Container | `<CONTAINER>` |
| Key Vault | `<KEY_VAULT_NAME>` |

### A.2 Service Principal (pour CI/CD et automatisations)

1. **Entra ID** → **App registrations** → **New registration**  
   - Nom : `sp-dbx-<projet>-prod`  
   - Single tenant
2. Noter **Application (client) ID** → `<ARM_CLIENT_ID>`
3. **Certificates & secrets** → **New client secret** → noter la **Value** → `<ARM_CLIENT_SECRET>` (une seule fois)
4. Sur le **Storage Account** → **Access control (IAM)** → **Add role assignment** :
   - Au SP : **`Storage Blob Data Contributor`** (scope compte ou container selon ta politique)

### A.3 Access Connector + Unity Catalog (ADLS → Databricks)

1. Créer une ressource **Databricks Access Connector for Azure Databricks** dans `<RG_NAME>`, région alignée avec Databricks/Storage.
2. Sur le **Storage Account**, IAM → au **Managed identity** de l’Access Connector :
   - **`Storage Blob Data Contributor`**
   - **`Storage Blob Delegator`** (souvent nécessaire pour éviter les erreurs *user delegation key* / 403)
3. Dans **Databricks** (SQL ou UI Catalog) :
   - **Storage credential** (managed identity + ID ressource Access Connector)
   - **External location** sur l’URL `abfss://<CONTAINER>@<STORAGE_ACCOUNT>.dfs.core.windows.net/`
4. Vérifier les **grants** UC sur l’external location pour les groupes / SP qui doivent lire/écrire.

### A.4 Key Vault + Secret Scope Databricks

1. Créer ou utiliser un **Key Vault** `<KEY_VAULT_NAME>`.
2. Y stocker les secrets (ex. `tenant-id`, `sp-client-id`, `sp-client-secret`, etc.) — **noms** cohérents avec ce que tu liras dans Databricks.
3. IAM du Key Vault : au principal **AzureDatabricks** (souvent `appid=2ff814a6-3304-4ab8-85cb-cd0e6f879c1d` dans les messages d’erreur) → rôle **`Key Vault Secrets User`** (si RBAC), ou policy avec **Get/List** sur secrets.
4. Créer un **secret scope** Databricks pointant vers ce Key Vault (UI ou CLI avec JSON + `tenant_id` selon version CLI).
5. Test dans un **notebook Python** :

   ```python
   dbutils.secrets.get(scope="<SCOPE_NAME>", key="tenant-id")
   ```

   Ou en SQL : `SELECT secret("<SCOPE_NAME>", "tenant-id");` → résultat masqué / `REDACTED` selon contexte.

---

## Partie B — Workspace Databricks & Unity Catalog

- Vérifier qu’un **metastore UC** est attaché (`SELECT current_metastore();` en SQL).
- Vérifier les **catalogues** (`SHOW CATALOGS;`) et les droits sur le catalogue cible (ex. `db_workspace_formation`).

### Service Principal “dans” Databricks

Pour que le **même SP** que GitHub puisse faire `bundle validate/deploy` :

1. **Account Console** Databricks (si tu y as accès) : enregistrer le **Service Principal** et l’assigner au **workspace**.
2. Dans le workspace : droits suffisants sur le **catalog** / **schemas** `prod`, `ci`, et chemins Workspace du bundle.

---

## Partie C — Projet local Databricks Asset Bundle (DAB)

### C.1 Récupérer le code

```powershell
git clone <URL_DU_REPO>
cd dab_dlt_learning
```

### C.2 Authentifier le CLI (profil perso)

```powershell
databricks auth login --host https://adb-<WORKSPACE_ID>.<REGION>.azuredatabricks.net
```

Profil conseillé : `DEFAULT`.

Tester :

```powershell
databricks current-user me --profile DEFAULT
```

### C.3 Profil CI (Service Principal) — sur la machine locale

Créer un second profil (ex. `prod-sp`) avec les credentials Azure du SP (selon doc Databricks pour ton type d’auth), ou utiliser variables d’environnement + `~/.databrickscfg` comme en CI.

### C.4 Valider le bundle

```powershell
databricks bundle validate -t dev --profile DEFAULT
databricks bundle validate -t ci --profile prod-sp
databricks bundle validate -t prod --profile prod-sp
```

Les targets `dev` / `ci` / `prod` sont définis dans `databricks.yml` :

- **dev** : mode development, schéma personnel (`${workspace.current_user.short_name}`).
- **ci** : pour CI avec SP, schéma fixe `ci` (évite l’appel `scim/Me` du user courant).
- **prod** : `run_as` SP, schéma `prod`, `root_path` sous `/Workspace/Shared/...` (adapter si ta politique de sécurité change).

### C.5 Déployer / lancer en local

```powershell
databricks bundle deploy -t dev --profile DEFAULT
databricks bundle run dab_dlt_learning_etl -t dev --profile DEFAULT
databricks bundle run dlt_orchestration_job -t dev --profile DEFAULT
```

---

## Partie D — Pipeline DLT (rappel métier)

Fichier principal : `src/dab_dlt_learning/dlt_pipeline.py`

| Couche | Contenu |
|--------|---------|
| **Bronze** | Auto Loader `cloudFiles` + JSON depuis `abfss://...` + **schéma explicite** + `_ingest_ts`, `_source_file` via `_metadata.file_path` (UC : pas `input_file_name()`) |
| **Silver** | Expectations + `dlt.apply_changes` en **SCD2** |
| **Gold** | Agrégations ; filtrer **`__END_AT IS NULL`** pour l’état courant |
| **Liquid clustering** | `cluster_by` sur la table Gold |

Ressources bundle :

- `resources/dab_dlt_learning_etl.pipeline.yml` : pipeline DLT + `libraries` vers `dlt_pipeline.py`
- `resources/sample_job.job.yml` : job `dlt_orchestration_job` avec `pipeline_task`

### Erreurs fréquentes vues ensemble

| Symptôme | Piste |
|----------|--------|
| `403` / *user delegation key* | Ajouter **Storage Blob Delegator** sur l’Access Connector côté Storage |
| `input_file_name` non supporté UC | Utiliser `_metadata.file_path` |
| Dossier vide + inférence | **Schéma explicite** en Bronze |
| `scim/v2/Me` en CI avec SP | Ne pas valider `dev` en CI ; utiliser target **`ci`** avec schéma fixe |

---

## Partie E — GitHub Actions (CI/CD)

Fichier : `.github/workflows/ci-cd-databricks.yml`

### E.1 Secrets du dépôt (ou de l’environment `prod`)

À configurer dans GitHub → **Settings** → **Secrets and variables** → **Actions** (et/ou secrets de l’environment **`prod`**) :

| Secret | Rôle |
|--------|------|
| `DATABRICKS_HOST` | URL du workspace `https://adb-...azuredatabricks.net` |
| `ARM_TENANT_ID` | Tenant Entra |
| `ARM_CLIENT_ID` | Client ID du SP |
| `ARM_CLIENT_SECRET` | Secret du SP |

### E.2 Comportement du workflow

- **PR** + **push `main`** : job **`ci-checks`** (pytest unitaires, `bundle validate` `ci` + `prod`).
- **Push tag `v*`** : après `ci-checks`, job **`deploy-prod`** (avec environment **`prod`** + reviewers si configuré).
- Création de **`~/.databrickscfg`** dans les jobs **avant** tout `databricks bundle ...`.
- Job **`deploy-prod`** : installer **Python + uv** avant `bundle deploy` (build du wheel).

### E.3 Tests pytest

- Marqueur **`@pytest.mark.integration`** sur les tests qui nécessitent Databricks Connect / compute.
- En CI : `pytest -m "not integration"`.
- Déclarer le marker dans `pyproject.toml` (`[tool.pytest.ini_options]`).
- Garder au moins un test unitaire simple (ex. `tests/test_unit_smoke.py`) pour éviter exit code 5 si tout est désélectionné.

### E.4 Release par tag

Sur `main` à jour :

```powershell
git tag -a v0.1.0 -m "Release v0.1.0"
git push origin v0.1.0
```

Puis **Actions** → approuver **`prod`** si demandé.

### E.5 Rollback “code”

Redéployer un **ancien tag** ou créer un **nouveau tag** pointant vers un ancien commit (on ne repousse pas un tag existant).

**Les données Delta** ne reviennent pas seules : voir `DESCRIBE HISTORY` / `RESTORE TABLE` si besoin.

### E.6 Protection de la branche `main` (recommandation entreprise)

Dans GitHub : **Settings** → **Branches** → **Add branch protection rule** (ou éditer la règle existante).

| Réglage | À activer | Pourquoi |
|---------|-----------|----------|
| **Branch name pattern** | `main` | Cible la branche de référence |
| **Require a pull request before merging** | Oui | Interdit le push direct sur `main` ; tout passe par une **PR** |
| **Require approvals** | ≥ 1 (souvent) | Revue humaine avant merge |
| **Require status checks to pass before merging** | Oui | Ajouter le check **`ci-checks`** (nom exact affiché dans l’onglet **Checks** d’une PR) |
| **Do not allow deleting this branch** | Oui | Évite la suppression accidentelle |
| **Do not allow force pushes** | Oui | Évite `git push --force` sur `main` |

Optionnel : **Require linear history** (historique linéaire), selon la convention d’équipe.

Flux typique après protection : **feature branch → PR → CI verte → review → merge sur `main` → tag `v*` → déploiement prod** (avec approval environment `prod` si configuré).

### E.7 Déroulement du workflow (récapitulatif)

Fichier : `.github/workflows/ci-cd-databricks.yml`.

**Déclencheurs (`on:`)**

| Événement | Effet |
|-----------|--------|
| **Pull request** vers `main` | Lance le workflow ; job **`ci-checks`** uniquement ( **`deploy-prod`** ignoré ) |
| **Push** sur `main` | Idem : **`ci-checks`** oui, **`deploy-prod`** non |
| **Push** d’un tag `v*` (ex. `v0.1.0`) | **`ci-checks`** puis, si succès, **`deploy-prod`** |

**Job `ci-checks` (ordre des étapes utiles)**

1. **Checkout** du code (`fetch-depth: 0` pour l’historique / tags).
2. **Setup Python** + **Install uv** + `uv sync` (dépendances + dev).
3. **Run pytest** : `pytest -m "not integration"` (tests unitaires uniquement ; gérer le cas “0 test” si besoin).
4. **Setup Databricks CLI**.
5. **Créer `~/.databrickscfg`** (profils alignés avec `databricks.yml`, ex. `DEFAULT` + `prod-sp`) — **obligatoire avant** toute commande `databricks bundle ...` sur le runner.
6. **`databricks bundle validate -t ci`** puis **`-t prod`**.

**Job `deploy-prod`**

- Condition : uniquement si la référence est un **tag** `refs/tags/v...`.
- **Checkout** avec `ref: ${{ github.ref }}` (code **exact** du tag).
- Log **Release version** (`github.ref_name`, `github.sha`).
- Création **`~/.databrickscfg`**, **Python + uv**, puis **`databricks bundle deploy -t prod`**.
- Environment **`prod`** : peut exiger **Required reviewers** (GitHub → **Settings** → **Environments** → `prod`).

**Pièges fréquents CI**

| Problème | Piste |
|----------|--------|
| `open /home/runner/.databrickscfg: no such file` | Créer le fichier **avant** `bundle validate` / `deploy` |
| `uv: command not found` sur `deploy-prod` | Ajouter **Install uv** (et Python) dans ce job aussi |
| `403` sur `scim/v2/Me` avec le SP | Ne pas valider la target **`dev`** en CI ; utiliser **`ci`** (schéma fixe, pas `current_user`) |
| `deploy-prod` skipped sur une PR | Normal : le déploiement prod est **sur tag**, pas sur chaque PR |
| Avertissement Node.js 20 sur les `actions/*` | Mettre à jour les versions d’actions quand disponibles pour Node 24 |

---

## Partie F — Vérifications “ça marche”

### Azure

- Budget / **Cost analysis** : filtrer par ressource Databricks / RG.

### Databricks

- **Catalog** : tables dans `catalog.schema` attendus.
- **Pipelines DLT** : updates **COMPLETED**, onglet **Data quality**.
- **Jobs** : runs du job d’orchestration OK.
- **Workspace** : fichiers bundle sous le `root_path` configuré.

### GitHub

- Dernier workflow **vert** ; pour un tag, **`deploy-prod`** vert après approval.

---

## Partie G — Git & GitHub (éviter les erreurs vues en formation)

### G.1 Première fois : lier le dossier local au dépôt

```powershell
cd C:\chemin\vers\dab_dlt_learning
git init
git remote add origin https://github.com/<compte>/<repo>.git
git add -A
git commit -m "Initial commit"
git branch -M main
git push -u origin main
```

- **`git add -A`** est souvent plus sûr que `git add *` sous PowerShell (fichiers cachés, etc.).
- Si `git push` affiche **403** : le compte Git utilisé n’a pas les droits sur le repo → ajouter le compte en **collaborateur** avec **Write**, ou utiliser le compte **owner** du repo.

### G.2 Travailler sur une branche (recommandé)

```powershell
git checkout -b feature/ma-modif
# ... modifications ...
git add -A
git commit -m "Description claire"
git push -u origin feature/ma-modif
```

Puis ouvrir une **Pull Request** vers `main` sur GitHub.

### G.3 Push refusé : *non-fast-forward* / *fetch first*

Le distant a des commits que tu n’as pas localement :

```powershell
git pull --rebase origin <ta-branche>
git push
```

### G.4 Syntaxe `git push` avec suivi de branche

Correct :

```powershell
git push -u origin <nom-branche>
```

Erreur fréquente : `git push u- origin ...` ou mauvais ordre des arguments → relire la commande.

### G.5 Changer de branche alors que tu as des modifications locales

Git refuse `git checkout main` si des fichiers non commités seraient écrasés. Options :

- **Commit** puis checkout, ou  
- **`git stash`** (remettre de côté), puis checkout, puis `git stash pop` si besoin.

### G.6 Cherry-pick

- Démarrer : `git cherry-pick <commit_hash>`
- Continuer **après résolution de conflits** : `git add -A` puis `git cherry-pick --continue`
- **`git cherry-pick --continue`** sans cherry-pick en cours → erreur normale ; il faut d’abord lancer `git cherry-pick <hash>`.

### G.7 Récupérer du travail “perdu” après un `pull` / mauvaise manip

```powershell
git reflog
```

Repère le commit **avant** le problème (`HEAD@{n}`), puis par exemple :

```powershell
git cherry-pick <hash>
# ou
git checkout <hash> -- chemin/vers/fichier
```

**Cursor / VS Code** : **Local History** / **Timeline** sur un fichier peut aussi restaurer une version.

### G.8 Variables d’environnement Git parasites

Si le prompt indique `(GIT_DIR!)` ou `fatal: not a work tree` :

```powershell
cd C:\chemin\vers\dab_dlt_learning
Remove-Item Env:GIT_DIR -ErrorAction SilentlyContinue
Remove-Item Env:GIT_WORK_TREE -ErrorAction SilentlyContinue
```

Ne pas travailler **depuis** le sous-dossier `.git` comme répertoire courant.

### G.9 Tags de release

```powershell
git checkout main
git pull origin main
git tag -a v0.1.0 -m "Release v0.1.0"
git push origin v0.1.0
```

Un **tag déjà poussé** ne peut pas être repoussé tel quel ; incrémenter (`v0.1.1`, etc.).

### G.10 Pager et éditeurs

| Situation | Action |
|-----------|--------|
| Bas d’écran **`(END)`** (pager `less`) | Touche **`q`** |
| **Vim** (message de merge / commit) | `Esc` puis `:wq` (sauver) ou `:q!` (abandonner) |
| Warnings **`LF will be replaced by CRLF`** | Souvent bénin sous Windows ; optionnel : `git config core.autocrlf true` (selon équipe) |

---

## Ordre d’exécution recommandé “from scratch”

1. Installer outils (Git, Python, CLI Databricks, `uv`).
2. Azure : RG, Storage, Key Vault, SP, rôles Storage, Access Connector + rôles + External Location UC.
3. Databricks : secrets scope, droits KV, SP dans workspace + droits UC.
4. Cloner le repo, `databricks auth login`, `bundle validate` / `deploy` / `run` en dev.
5. GitHub : secrets, environment `prod` + reviewers, pousser le workflow, tester PR puis tag `v0.0.1`.

---

## Liens utiles (documentation)

- Bundles Databricks : [https://docs.databricks.com/dev-tools/bundles/index.html](https://docs.databricks.com/dev-tools/bundles/index.html)
- Delta Live Tables : [https://docs.databricks.com/delta-live-tables/index.html](https://docs.databricks.com/delta-live-tables/index.html)
- Unity Catalog : [https://docs.databricks.com/data-governance/unity-catalog/index.html](https://docs.databricks.com/data-governance/unity-catalog/index.html)

---

*Document généré pour accompagner une reprise autonome. Adapte les noms de ressources, chemins ADLS et politiques de sécurité à ton organisation.*
