# Self-Host and Hosted Modes

Postbridge Core is public and self-hostable. Hosted deployments can reuse Core as a runtime component, but hosted-specific business logic should live outside this repository.

## Self-Host Mode

Use this mode for community installs and private deployments:

```env
POSTBRIDGE_APP_MODE=selfhost
```

In this mode:

- Core serves the bundled frontend;
- the browser calls `/api/app/*`;
- local credentials are stored and encrypted by Core;
- the install owns its database, worker, queues, and platform credentials.

## Hosted Mode

Hosted mode is for private deployment overlays:

```env
POSTBRIDGE_APP_MODE=saas
```

In this mode:

- Core remains the publishing/runtime engine;
- hosted product screens should use a private BFF;
- billing, account lifecycle, hosted authentication, and tenant management stay outside the public Core repository;
- `/internal/service/*` remains server-to-server only.

## Boundary Rule

Public Core should contain reusable runtime behavior, open-source self-host UX, and documented APIs.

Private hosted code should contain deployment-specific product policy, billing, hosted authentication, and business operations that are not required for self-host users.

