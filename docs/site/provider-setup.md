# Connecting a device service

Mirobody includes Garmin, Oura and WHOOP integrations. Each deployment needs
its own credentials from the vendor's developer program.
Apple Health uses a separate [import and client-push workflow](apple-health.md).

## Prepare the connection

1. Register your application with the vendor.
2. Configure a public HTTPS callback on your deployment.
3. Put credentials in the active configuration overlay, not the shipped defaults.
4. Apply the configuration and authorize the account in the web client.

The callback format is:

```text
https://your-host/api/v1/pulse/theta/<provider_slug>/callback
```

| Vendor | Provider slug | Credential keys |
| --- | --- | --- |
| Oura | `theta_oura` | `OURA_CLIENT_ID`, `OURA_CLIENT_SECRET`, `OURA_REDIRECT_URL` |
| WHOOP | `theta_whoop` | `WHOOP_CLIENT_ID`, `WHOOP_CLIENT_SECRET`, `WHOOP_REDIRECT_URL` |
| Garmin | `theta_garmin` | `GARMIN_CLIENT_ID`, `GARMIN_CLIENT_SECRET`, `GARMIN_REDIRECT_URL` |

The redirect URL in vendor registration must match the configured URL exactly.
Oura and WHOOP use OAuth 2.0; the shipped Garmin connection uses OAuth 1.0a.

## Example configuration

```yaml
OURA_CLIENT_ID: your-client-id
OURA_CLIENT_SECRET: your-client-secret
OURA_REDIRECT_URL: https://your-host/api/v1/pulse/theta/theta_oura/callback
```

For Docker, save this in the mounted overlay selected by `ENV` and
`MIROBODY_CONFIG_FILE`. Editing a checkout's `config.devices.yaml` does not
change the copy inside an existing image. See
[Configuration](https://docs.mirobody.ai/en/configuration).

<a id="troubleshooting"></a>

## Check synchronization

Apply changes with `docker compose up -d`, then inspect the app and worker logs:

```bash
docker compose logs --tail 80 mirobody mirobody_worker
```

A provider with missing credentials is skipped. If authorization fails, check
the callback URL and credentials. If connected data does not arrive, check
worker status and the database connection.
The [full setup reference](../provider-setup.md) includes developer portals,
optional endpoints and vendor-specific details.
